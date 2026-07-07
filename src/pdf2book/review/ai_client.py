"""AI client for the review pipeline — OpenAI-compatible chat completions.

This module wraps httpx calls to an OpenAI-compatible `/v1/chat/completions`
endpoint and adds the constraint-validation retry loop for low-confidence
OCR correction.

Design:
  * `AIClient.complete(prompt)` — single chat completion, returns raw text.
  * `AIClient.complete_json(prompt)` — completion + lenient JSON parsing
    (strips Markdown code fences, extracts the first JSON object/array).
  * `AIClient.review_low_confidence(items)` — batch review with per-item
    constraint validation retry. Violations trigger a focused retry prompt
    containing the violation reason; after `max_retries` failures the item
    is marked `[需校对]` for manual review.

When `cfg.enabled=False` the client short-circuits every call to return
empty results — the pipeline still runs, just without AI review. This lets
users without an API key exercise the rest of the pipeline.

The client is HTTP-only (no SDK dependency) so it works with any
OpenAI-compatible endpoint: OpenAI, Azure, Anthropic-via-proxy, vLLM, Ollama,
LocalAI, etc.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from pdf2book.config import AIReviewConfig
from pdf2book.review.constraints import extract_constraints, validate_correction
from pdf2book.review.prompt_builder import (
    build_low_confidence_prompt,
    build_metadata_prompt,
    build_page_type_prompt,
    build_title_prompt,
)


@dataclass
class ReviewResult:
    """Container for the four AI review task outputs.

    Each field is the parsed JSON response from the corresponding prompt.
    Empty list/dict when the task had no input items or AI is disabled.
    """

    metadata: dict
    low_confidence: list[dict]
    titles: list[dict]
    page_types: list[dict]


class AIClient:
    """HTTP client for an OpenAI-compatible chat completions endpoint.

    Not thread-safe (one httpx.Client per instance). Create a fresh client
    per pipeline run, or wrap calls in a lock if sharing.
    """

    def __init__(
        self, cfg: AIReviewConfig, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.cfg = cfg
        # Optional transport injection (used by tests with httpx.MockTransport).
        # When None, httpx.Client uses its default transport (real HTTP).
        self._transport = transport
        # Lazily created; None when disabled so we never open a connection.
        self._client: httpx.Client | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(self, prompt: str) -> str:
        """Send a chat completion request, return the assistant's text.

        Returns empty string when `cfg.enabled=False` (no network call).
        Raises `httpx.HTTPError` on network/server failure — callers should
        catch and degrade gracefully (e.g. mark all items [需校对]).
        """
        if not self.cfg.enabled:
            return ""

        client = self._ensure_client()
        url = f"{self.cfg.api_url.rstrip('/')}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.cfg.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.cfg.max_tokens,
            "temperature": 0.0,  # deterministic for校对
        }
        response = client.post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
        # OpenAI shape: {"choices": [{"message": {"content": "..."}}]}
        choices = data.get("choices") or []
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "") or ""

    def complete_json(self, prompt: str) -> Any:
        """Complete + parse JSON from the response.

        Uses `_parse_json_lenient` which handles Markdown code fences and
        extracts the first JSON object/array from surrounding prose. Returns
        `None` if no JSON could be parsed (caller should treat as failure).
        """
        text = self.complete(prompt)
        if not text:
            return None
        return _parse_json_lenient(text)

    def review_low_confidence(self, items: list[dict]) -> list[dict]:
        """Review low-confidence OCR items with constraint validation retry.

        Args:
            items: List of low-confidence item dicts (from collect_review_items).

        Returns:
            List of {"id": str, "corrected": str, "confidence": float, "status": str}.
            `status` is one of:
              - "corrected": passed validation, `corrected` is the AI's text.
              - "unclear": AI returned [UNCLEAR], `corrected` is "[UNCLEAR]".
              - "manual": failed validation after max_retries, `corrected`
                is the original text with "[需校对]" suffix.
              - "skipped": AI disabled or empty input, `corrected` is original.
        """
        if not self.cfg.enabled or not items:
            return [
                {
                    "id": item["id"],
                    "corrected": item["original_text"],
                    "confidence": 0.0,
                    "status": "skipped",
                }
                for item in items
            ]

        # Batch call: send all items in one prompt.
        prompt = build_low_confidence_prompt(items)
        raw = self.complete_json(prompt)

        # Parse response into a list of {id, corrected, confidence}.
        corrections = _normalize_low_confidence_response(raw, items)

        # Validate each correction; retry failures individually.
        results: list[dict] = []
        for item, correction in zip(items, corrections):
            original = item["original_text"]
            corrected = correction.get("corrected", "").strip()
            constraints = extract_constraints(
                original,
                item.get("context_before", ""),
                item.get("context_after", ""),
            )

            # [UNCLEAR] → mark unclear, no retry.
            if "[UNCLEAR]" in corrected:
                results.append(
                    {
                        "id": item["id"],
                        "corrected": "[UNCLEAR]",
                        "confidence": correction.get("confidence", 0.0),
                        "status": "unclear",
                    }
                )
                continue

            # Validate.
            is_valid, reason = validate_correction(original, corrected, constraints)
            if is_valid:
                results.append(
                    {
                        "id": item["id"],
                        "corrected": corrected,
                        "confidence": correction.get("confidence", 0.0),
                        "status": "corrected",
                    }
                )
                continue

            # Retry with feedback (per-item, focused prompt).
            final_corrected = corrected
            for _ in range(self.cfg.max_retries):
                retry_prompt = _build_retry_prompt(item, final_corrected, reason)
                retry_raw = self.complete_json(retry_prompt)
                if retry_raw is None:
                    break
                retry_correction = _normalize_low_confidence_response(
                    retry_raw, [item]
                )
                if not retry_correction:
                    break
                final_corrected = retry_correction[0].get("corrected", "").strip()

                if "[UNCLEAR]" in final_corrected:
                    break  # AI gave up; stop retrying.

                is_valid, reason = validate_correction(
                    original, final_corrected, constraints
                )
                if is_valid:
                    break

            if "[UNCLEAR]" in final_corrected:
                results.append(
                    {
                        "id": item["id"],
                        "corrected": "[UNCLEAR]",
                        "confidence": 0.0,
                        "status": "unclear",
                    }
                )
            elif is_valid:
                results.append(
                    {
                        "id": item["id"],
                        "corrected": final_corrected,
                        "confidence": 0.5,  # passed on retry, lower confidence
                        "status": "corrected",
                    }
                )
            else:
                # Failed all retries → mark for manual review.
                results.append(
                    {
                        "id": item["id"],
                        "corrected": f"{original}[需校对]",
                        "confidence": 0.0,
                        "status": "manual",
                    }
                )

        return results

    def review_all(
        self,
        review_items: dict,
    ) -> ReviewResult:
        """Run all four review tasks and return combined results.

        This is the main entry point called by the pipeline. It dispatches
        to the appropriate prompt builder + complete_json for each task.
        Low-confidence correction goes through `review_low_confidence` for
        constraint validation retry; the other three tasks are single-shot.
        """
        if not self.cfg.enabled:
            return ReviewResult(
                metadata={},
                low_confidence=[],
                titles=[],
                page_types=[],
            )

        # Task 1: Metadata extraction (skip API call when no candidates).
        meta_candidates = review_items.get("metadata", {}).get("candidates", [])
        metadata: dict = {}
        if meta_candidates:
            meta_prompt = build_metadata_prompt(
                review_items.get("metadata", {}).get("current", {}),
                meta_candidates,
            )
            meta_raw = self.complete_json(meta_prompt)
            metadata = meta_raw if isinstance(meta_raw, dict) else {}

        # Task 2: Low-confidence correction (with retry). review_low_confidence
        # handles the empty-items short-circuit internally.
        low_conf_results = self.review_low_confidence(
            review_items.get("low_confidence_texts", [])
        )

        # Task 3: Title level confirmation (skip API call when no titles).
        title_candidates = review_items.get("title_candidates", [])
        titles: list[dict] = []
        if title_candidates:
            title_prompt = build_title_prompt(title_candidates)
            title_raw = self.complete_json(title_prompt)
            titles = title_raw if isinstance(title_raw, list) else []

        # Task 4: Page type confirmation (skip API call when no candidates).
        page_type_candidates = review_items.get("page_type_candidates", [])
        page_types: list[dict] = []
        if page_type_candidates:
            page_prompt = build_page_type_prompt(page_type_candidates)
            page_raw = self.complete_json(page_prompt)
            page_types = page_raw if isinstance(page_raw, list) else []

        return ReviewResult(
            metadata=metadata,
            low_confidence=low_conf_results,
            titles=titles,
            page_types=page_types,
        )

    def close(self) -> None:
        """Close the underlying httpx client. Safe to call multiple times."""
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            kwargs: dict = {"timeout": self.cfg.timeout}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.Client(**kwargs)
        return self._client


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

# Match ```json ... ``` or ``` ... ``` code fences.
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _parse_json_lenient(text: str) -> Any:
    """Parse JSON from LLM output, tolerating Markdown fences and prose.

    Strategy:
      1. Strip ```json ... ``` code fences if present.
      2. Try `json.loads` on the stripped text.
      3. If that fails, find the first `{` ... `}` or `[` ... `]` span
         and try parsing that.

    Returns None if no JSON could be extracted.
    """
    if not text:
        return None

    # Step 1: strip code fences.
    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1)

    text = text.strip()

    # Step 2: try direct parse.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Step 3: extract first JSON object or array.
    # Find the first { or [ and try to parse from there.
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = text.find(start_char)
        if start < 0:
            continue
        # Find the matching end by scanning from the end (lenient — doesn't
        # handle nested strings with braces, but works for well-formed JSON
        # that the LLM wraps in prose).
        end = text.rfind(end_char)
        if end <= start:
            continue
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            continue

    return None


def _normalize_low_confidence_response(
    raw: Any,
    items: list[dict],
) -> list[dict]:
    """Normalize AI response into a list of {id, corrected, confidence}.

    Handles three response shapes:
      1. List of dicts: [{"id": ..., "corrected": ..., "confidence": ...}]
      2. Dict with "items" key: {"items": [...]}
      3. Single dict (one item): {"id": ..., "corrected": ...}

    Falls back to matching by index if "id" is missing in the response.
    Returns one entry per input item, defaulting to original text if missing.
    """
    if raw is None:
        return [
            {"id": item["id"], "corrected": item["original_text"], "confidence": 0.0}
            for item in items
        ]

    # Normalize to a list of dicts.
    if isinstance(raw, dict):
        if "items" in raw and isinstance(raw["items"], list):
            entries = raw["items"]
        elif "id" in raw or "corrected" in raw:
            entries = [raw]
        else:
            entries = []
    elif isinstance(raw, list):
        entries = raw
    else:
        entries = []

    # Build a lookup by id, falling back to index.
    by_id: dict[str, dict] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        eid = entry.get("id")
        if eid is None and i < len(items):
            eid = items[i]["id"]
        if eid is not None:
            by_id[eid] = entry

    # Produce one result per input item.
    results: list[dict] = []
    for item in items:
        entry = by_id.get(item["id"])
        if entry is None:
            results.append(
                {
                    "id": item["id"],
                    "corrected": item["original_text"],
                    "confidence": 0.0,
                }
            )
        else:
            results.append(
                {
                    "id": item["id"],
                    "corrected": entry.get("corrected", item["original_text"]),
                    "confidence": entry.get("confidence", 0.0),
                }
            )
    return results


def _build_retry_prompt(
    item: dict,
    failed_correction: str,
    violation_reason: str,
) -> str:
    """Build a focused retry prompt for a single failed correction.

    Tells the AI what it got wrong and asks for a corrected attempt that
    respects the constraints. This is per-item (not batch) to focus the
    model's attention.
    """
    from textwrap import dedent

    return dedent(
        f"""\
        ## 重试任务：低置信度 OCR 校对

        你之前的修正被约束验证拒绝了。请重新校对。

        ## 原始 OCR 文本
        {item["original_text"]}

        ## 上下文
        前：{item.get("context_before", "")}
        后：{item.get("context_after", "")}

        ## 约束（必须满足）
        - max_length: {item["constraints"]["max_length"]}
        - preserved_chars: {item["constraints"]["preserved_chars"]}
        - max_edit_distance: {item["constraints"]["max_edit_distance"]}

        ## 你之前的修正（被拒绝）
        {failed_correction}

        ## 拒绝原因
        {violation_reason}

        ## 要求
        请返回一个满足所有约束的修正。只输出 JSON：
        {{"corrected": str, "confidence": float}}

        若无法满足约束或不确定，返回：
        {{"corrected": "[UNCLEAR]", "confidence": 0.0}}
        """
    )


__all__ = [
    "AIClient",
    "ReviewResult",
    "_parse_json_lenient",
    "_normalize_low_confidence_response",
]
