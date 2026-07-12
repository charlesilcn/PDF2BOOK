"""AI client for the Markdown-based review pipeline.

Wraps httpx calls to an OpenAI-compatible ``/v1/chat/completions`` endpoint.
The review flow is:

  1. ``collect_markdown_issues`` (in ``markdown_review.py``) scans the
     generated ``book.md`` for low-confidence blocks, chapter titles with
     issues, and current metadata.
  2. ``build_review_prompt`` builds a single comprehensive prompt.
  3. ``AIClient.review_markdown`` calls the API and applies corrections
     back to ``book.md`` and ``meta.md``.

When ``cfg.enabled=False`` the client short-circuits every call to return
empty results — the pipeline still runs, just without AI review.

The client is HTTP-only (no SDK dependency) so it works with any
OpenAI-compatible endpoint: OpenAI, Azure, Anthropic-via-proxy, vLLM,
Ollama, LocalAI, etc.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx

from pdf2book.config import AIReviewConfig
from pdf2book.epub.metadata import BookMetadata
from pdf2book.progress import NullReporter, ProgressReporter
from pdf2book.utils.logger import get_logger

_log = get_logger()


class AIClient:
    """HTTP client for an OpenAI-compatible chat completions endpoint.

    Not thread-safe (one httpx.Client per instance). Create a fresh client
    per pipeline run, or wrap calls in a lock if sharing.
    """

    def __init__(
        self,
        cfg: AIReviewConfig,
        transport: httpx.BaseTransport | None = None,
        work_dir: Path | None = None,
        reporter: ProgressReporter | None = None,
    ) -> None:
        self.cfg = cfg
        # Optional transport injection (used by tests with httpx.MockTransport).
        # When None, httpx.Client uses its default transport (real HTTP).
        self._transport = transport
        # Lazily created; None when disabled so we never open a connection.
        self._client: httpx.Client | None = None
        # Work directory for resolving page images (multimodal review).
        # When None, multimodal review falls back to text-only with a warning.
        self._work_dir = work_dir
        # Progress reporter for batch-level progress (Web UI / CLI). Defaults
        # to a no-op so existing callers/tests are unaffected.
        self._reporter = reporter or NullReporter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(
        self, prompt: str, image_paths: list[Path] | None = None
    ) -> str:
        """Send a chat completion request, return the assistant's text.

        Returns empty string when ``cfg.enabled=False`` (no network call).
        Raises ``httpx.HTTPError`` on network/server failure — callers
        should catch and degrade gracefully.

        When ``image_paths`` is non-empty, builds a multimodal content
        array (text + image_url blocks) for OpenAI-compatible vision APIs.
        If all images fail to encode, falls back to text-only.
        """
        content, _ = self._post_chat(prompt, image_paths=image_paths)
        return content

    def _post_chat(
        self, prompt: str, image_paths: list[Path] | None = None
    ) -> tuple[str, str | None]:
        """Send a chat completion request, return (content, finish_reason).

        ``finish_reason`` is ``None`` when not provided by the API. Common
        values: ``"stop"`` (normal), ``"length"`` (truncated by max_tokens),
        ``"content_filter"`` (blocked). Callers use ``finish_reason`` to
        decide whether to retry a truncated batch via splitting.

        Returns ``("", None)`` when ``cfg.enabled=False``. Raises
        ``httpx.HTTPError`` on network/server failure.
        """
        if not self.cfg.enabled:
            return "", None

        client = self._ensure_client()
        url = f"{self.cfg.api_url.rstrip('/')}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }

        content: str | list[dict]
        if image_paths:
            content_blocks: list[dict] = [{"type": "text", "text": prompt}]
            for img_path in image_paths:
                data_url = _encode_image_to_data_url(img_path)
                if data_url is not None:
                    content_blocks.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        }
                    )
            if len(content_blocks) == 1:
                _log.warning(
                    "Multimodal review: all images failed to encode, "
                    "falling back to text-only"
                )
                content = prompt
            else:
                content = content_blocks
        else:
            content = prompt

        body = {
            "model": self.cfg.model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": self.cfg.max_tokens,
            "temperature": 0.0,  # deterministic for校对
        }
        response = client.post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
        # OpenAI shape: {"choices": [{"message": {"content": "..."}}]}
        choices = data.get("choices") or []
        if not choices:
            return "", None
        choice = choices[0]
        content_str = choice.get("message", {}).get("content", "") or ""
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            _log.warning(
                "AI response truncated (finish_reason=length, "
                "max_tokens=%d). Batch will be split and retried.",
                self.cfg.max_tokens,
            )
        return content_str, finish_reason

    def complete_json(
        self, prompt: str, image_paths: list[Path] | None = None
    ) -> Any:
        """Complete + parse JSON from the response.

        Uses ``_parse_json_lenient`` which handles Markdown code fences and
        extracts the first JSON object/array from surrounding prose. Returns
        ``None`` if no JSON could be parsed (caller should treat as failure).
        """
        content, _ = self._post_chat(prompt, image_paths=image_paths)
        if not content:
            return None
        return _parse_json_lenient(content)

    def review_markdown(
        self,
        md_path: Path,
        meta_path: Path,
        meta: BookMetadata,
    ) -> BookMetadata:
        """Review and correct ``book.md`` after markdown generation.

        Uses **batched API calls**: the full issues set is split into
        smaller batches (low-confidence texts, title chunks of 15,
        decoration candidates, structure tasks) so each API response
        stays well within the model's output token limit. Results from
        all batches are merged before being applied to ``book.md``.

        Flow:
          1. ``collect_markdown_issues(md_path, meta)`` → issues dict
          2. ``build_review_batches(issues)`` → list of (sub-issues, images)
          3. For each batch: ``build_review_prompt`` + ``complete_json``
          4. ``merge_corrections(results)`` → combined corrections dict
          5. ``apply_markdown_corrections(...)`` → updated BookMetadata

        Returns the updated BookMetadata. On AI disabled or all batches
        failing, returns the original meta unchanged.
        """
        if not self.cfg.enabled:
            return meta

        from pdf2book.review.markdown_review import (
            apply_markdown_corrections,
            build_review_batches,
            build_review_prompt,
            collect_markdown_issues,
            merge_corrections,
        )

        max_images = self.cfg.max_images if self.cfg.multimodal else 0
        if self.cfg.multimodal and self._work_dir is None:
            _log.warning(
                "Multimodal review enabled but work_dir not set; "
                "falling back to text-only review"
            )
        issues = collect_markdown_issues(
            md_path, meta, work_dir=self._work_dir, max_images=max_images
        )

        batches = build_review_batches(
            issues, max_images=max_images, multimodal=self.cfg.multimodal
        )

        n_titles = len(issues.get("title_candidates", []))
        n_lc = len(issues.get("low_confidence_texts", []))
        n_deco = len(issues.get("decoration_candidates", []))
        _log.info(
            "AI markdown review stage: %d batches "
            "(titles=%d, low_conf=%d, decoration=%d, model=%s)",
            len(batches),
            n_titles,
            n_lc,
            n_deco,
            self.cfg.model,
        )

        all_results: list[dict] = []
        n_batches_succeeded = 0
        self._reporter.start("ai_review", "AI 审查", len(batches))
        for i, batch in enumerate(batches, 1):
            _log.info(
                "  batch %d/%d: %s, %d images",
                i,
                len(batches),
                batch["label"],
                len(batch["image_paths"]),
            )
            results = self._process_batch_recursive(
                batch, i, len(batches),
            )
            if results:
                n_batches_succeeded += 1
            all_results.extend(results)
            self._reporter.advance("ai_review", message=f"batch {i}/{len(batches)}")
        self._reporter.finish("ai_review")

        if not all_results:
            _log.warning(
                "All %d AI review batches failed; using rule-based results",
                len(batches),
            )
            return meta

        corrections = merge_corrections(all_results)

        n_fixes = (
            len(corrections.get("low_confidence_fixes", []))
            + len(corrections.get("title_fixes", []))
            + len(corrections.get("paragraph_fixes", []))
            + len(corrections.get("chapter_fixes", []))
            + len(corrections.get("decoration_fixes", []))
        )
        # `n_batches_succeeded` counts original batches that produced at
        # least one result (possibly via truncation-triggered splitting),
        # so it never exceeds `len(batches)`. `len(all_results)` may be
        # larger when splits occurred; we log it as a secondary stat.
        _log.info(
            "AI review applied: %d fixes across %d/%d batches "
            "(titles=%d, low_conf=%d, paragraph=%d, chapter=%d, decoration=%d, "
            "split_results=%d)",
            n_fixes,
            n_batches_succeeded,
            len(batches),
            len(corrections.get("title_fixes", [])),
            len(corrections.get("low_confidence_fixes", [])),
            len(corrections.get("paragraph_fixes", [])),
            len(corrections.get("chapter_fixes", [])),
            len(corrections.get("decoration_fixes", [])),
            len(all_results) - n_batches_succeeded,
        )

        updated_meta = apply_markdown_corrections(
            md_path, meta_path, corrections, issues
        )
        return updated_meta

    def _process_batch_recursive(
        self,
        batch: dict,
        batch_idx: int,
        total_batches: int,
        depth: int = 0,
    ) -> list[dict]:
        """Process a batch with retry-on-truncation.

        On truncation (``finish_reason="length"``), if the batch carries
        splittable items (``low_confidence_texts``, ``title_candidates``,
        or ``decoration_candidates``), the batch is split in half and each
        half is retried recursively up to ``cfg.max_retries`` depth. With
        ``max_retries=3`` a 5-item batch can be split three times
        (5→2+3→1+1+1+2→all singletons), so even heavily truncation-prone
        batches eventually produce results. Non-splittable batches
        (structure tasks, single-item batches) are skipped on truncation.

        Returns a list of result dicts (may be empty if the batch failed
        and could not be salvaged by splitting).
        """
        from pdf2book.review.markdown_review import build_review_prompt

        batch_issues = batch["issues"]
        batch_images = batch["image_paths"]
        label = batch["label"]
        indent = "  " * (depth + 1)

        prompt = build_review_prompt(batch_issues)

        try:
            content, finish_reason = self._post_chat(
                prompt, image_paths=batch_images or None,
            )
        except Exception as exc:
            _log.warning(
                "%sbatch %d/%d (%s) failed (%s); skipping",
                indent, batch_idx, total_batches, label, exc,
            )
            return []

        # Truncation detected — split the batch and retry each half.
        if finish_reason == "length" and depth < self.cfg.max_retries:
            splittable_key: str | None = None
            for key in (
                "low_confidence_texts",
                "title_candidates",
                "decoration_candidates",
            ):
                items = batch_issues.get(key, [])
                if len(items) > 1:
                    splittable_key = key
                    break

            if splittable_key is not None:
                items = batch_issues[splittable_key]
                mid = len(items) // 2
                _log.warning(
                    "%sbatch %d/%d (%s) truncated; splitting into "
                    "%d + %d items (retry %d/%d)",
                    indent, batch_idx, total_batches, label,
                    mid, len(items) - mid, depth + 1, self.cfg.max_retries,
                )
                results: list[dict] = []
                for split_idx, split_items in enumerate(
                    (items[:mid], items[mid:]), 1,
                ):
                    if not split_items:
                        continue
                    split_issues = {**batch_issues, splittable_key: split_items}
                    split_batch = {
                        "issues": split_issues,
                        # Decoration batches may carry images matched to
                        # the original item set; passing the full list to
                        # both halves is harmless (unused images ignored).
                        "image_paths": batch_images,
                        "label": f"{label} (split {split_idx})",
                    }
                    results.extend(
                        self._process_batch_recursive(
                            split_batch, batch_idx, total_batches, depth + 1,
                        )
                    )
                return results
            else:
                _log.warning(
                    "%sbatch %d/%d (%s) truncated but not splittable; "
                    "skipping",
                    indent, batch_idx, total_batches, label,
                )
                return []

        result = _parse_json_lenient(content) if content else None
        if not isinstance(result, dict):
            result_type = (
                type(result).__name__ if result is not None else "None"
            )
            _log.warning(
                "%sbatch %d/%d (%s): returned %s, skipping",
                indent, batch_idx, total_batches, label, result_type,
            )
            return []

        return [result]

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
      1. Strip ```` ```json ... ``` ```` code fences if present.
      2. Try ``json.loads`` on the stripped text.
      3. If that fails, find the first ``{`` ... ``}`` or ``[`` ... ``]``
         span and try parsing that.
      4. If that fails too (likely because the response was truncated by
         ``max_tokens`` mid-JSON), attempt to repair the truncated JSON
         by closing open ``{`` / ``[`` structures and retry parsing.

    Returns ``None`` if no JSON could be extracted.

    Note: when the response is truncated, this function prefers to
    salvage a partial dict over a complete inner array. Callers like
    ``review_markdown`` check ``isinstance(corrections, dict)`` and
    skip applying corrections otherwise, so returning a partial dict
    with whatever fixes the AI managed to emit is strictly better than
    returning an inner array (which would cause all corrections to be
    silently dropped).
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
    # Prefer object over array — review responses are always objects.
    # Use a stack-based scan to find the matching close brace instead
    # of naively taking the last `}` (which fails on truncated JSON
    # where the last `}` belongs to a nested object).
    obj_start = text.find("{")
    arr_start = text.find("[")

    # Try object first if it appears before array (or array doesn't exist).
    if obj_start >= 0 and (arr_start < 0 or obj_start <= arr_start):
        repaired = _repair_truncated_json(text, obj_start, "{")
        if repaired is not None:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

    # Then try array.
    if arr_start >= 0:
        repaired = _repair_truncated_json(text, arr_start, "[")
        if repaired is not None:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

    return None


def _repair_truncated_json(text: str, start: int, open_char: str) -> str | None:
    """Repair a possibly-truncated JSON object/array starting at ``start``.

    Scans from ``start`` tracking string state and bracket nesting depth.
    If the JSON is well-formed (all brackets closed), returns the slice
    ``text[start:end+1]``. If truncated (open brackets remain), strips
    any trailing partial entry (e.g. ``"key": "value`` without closing
    quote) and closes the open structures in reverse order.

    Returns ``None`` only if no matching close char is found at all
    (e.g. the open char is the only one in the text).
    """
    in_string = False
    escape = False
    stack: list[str] = []
    # Snapshot of (position, stack) at the last "complete" point —
    # i.e. right after a `}` or `]` successfully closed a structure.
    # When the JSON is truncated mid-value, we roll back to this point
    # and close only the structures that were open here. Closing the
    # current (incomplete) stack would add extra brackets that don't
    # correspond to any open char in the truncated text.
    last_complete_pos = start
    last_complete_stack: list[str] = []
    well_formed_end: int | None = None

    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch == "}" or ch == "]":
            if stack and stack[-1] == ch:
                stack.pop()
                last_complete_pos = i + 1
                last_complete_stack = list(stack)
                if not stack:
                    well_formed_end = i
                    break

    if well_formed_end is not None:
        return text[start : well_formed_end + 1]

    if last_complete_pos == start:
        # No complete sub-structure found at all — too broken to repair.
        return None

    # Truncated: roll back to last complete position and close the
    # structures that were open at that point (NOT the current stack,
    # which includes brackets opened by the truncated partial entry).
    truncated = text[start:last_complete_pos].rstrip()
    while truncated.endswith(","):
        truncated = truncated[:-1].rstrip()
    for close in reversed(last_complete_stack):
        truncated += close
    return truncated


__all__ = [
    "AIClient",
    "_parse_json_lenient",
    "_encode_image_to_data_url",
]


# ---------------------------------------------------------------------------
# Multimodal helpers
# ---------------------------------------------------------------------------

_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _encode_image_to_data_url(image_path: Path) -> str | None:
    """Encode an image file as a data URL for OpenAI vision API.

    Returns a ``data:{mime};base64,...`` string. Returns None on read
    failure (caller skips the image).
    """
    try:
        data = image_path.read_bytes()
    except OSError as exc:
        _log.warning(
            "Failed to read image for multimodal review: %s (%s)",
            image_path,
            exc,
        )
        return None
    encoded = base64.b64encode(data).decode("ascii")
    mime = _MIME_MAP.get(image_path.suffix.lower(), "image/png")
    return f"data:{mime};base64,{encoded}"
