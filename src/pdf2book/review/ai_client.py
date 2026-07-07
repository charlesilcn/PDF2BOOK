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

import json
import re
from pathlib import Path
from typing import Any

import httpx

from pdf2book.config import AIReviewConfig
from pdf2book.epub.metadata import BookMetadata
from pdf2book.utils.logger import get_logger

_log = get_logger()


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

        Returns empty string when ``cfg.enabled=False`` (no network call).
        Raises ``httpx.HTTPError`` on network/server failure — callers
        should catch and degrade gracefully.
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
        choice = choices[0]
        content = choice.get("message", {}).get("content", "") or ""
        # Detect truncation: if finish_reason is "length", the output was
        # cut off by max_tokens. Log a warning so the user knows to bump
        # max_tokens or shrink the prompt.
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            _log.warning(
                "AI response truncated (finish_reason=length, "
                "max_tokens=%d). Consider increasing ai_review.max_tokens "
                "or reducing prompt size.",
                self.cfg.max_tokens,
            )
        return content

    def complete_json(self, prompt: str) -> Any:
        """Complete + parse JSON from the response.

        Uses ``_parse_json_lenient`` which handles Markdown code fences and
        extracts the first JSON object/array from surrounding prose. Returns
        ``None`` if no JSON could be parsed (caller should treat as failure).
        """
        text = self.complete(prompt)
        if not text:
            return None
        return _parse_json_lenient(text)

    def review_markdown(
        self,
        md_path: Path,
        meta_path: Path,
        meta: BookMetadata,
    ) -> BookMetadata:
        """Review and correct ``book.md`` after markdown generation.

        Flow:
          1. ``collect_markdown_issues(md_path, meta)`` → issues dict
          2. ``build_review_prompt(issues)`` → prompt string
          3. ``complete_json(prompt)`` → corrections dict
          4. ``apply_markdown_corrections(...)`` → updated BookMetadata

        Returns the updated BookMetadata. On AI disabled or failure, returns
        the original meta unchanged (``book.md`` keeps its low-confidence
        markers for manual proofreading).
        """
        if not self.cfg.enabled:
            return meta

        from pdf2book.review.markdown_review import (
            apply_markdown_corrections,
            build_review_prompt,
            collect_markdown_issues,
        )

        issues = collect_markdown_issues(md_path, meta)
        prompt = build_review_prompt(issues)
        corrections = self.complete_json(prompt)

        if not isinstance(corrections, dict):
            # AI returned no usable JSON (or returned a non-dict type,
            # which happens when the response is truncated mid-array and
            # _parse_json_lenient salvages an inner array). Log details
            # so the user can diagnose (usually: increase max_tokens).
            corrections_type = type(corrections).__name__ if corrections is not None else "None"
            n_titles = len(issues.get("title_candidates", []))
            n_lc = len(issues.get("low_confidence_texts", []))
            _log.warning(
                "AI review returned %s (expected dict). Prompt had "
                "%d titles + %d low-confidence texts. "
                "Try increasing ai_review.max_tokens.",
                corrections_type,
                n_titles,
                n_lc,
            )
            return meta

        n_fixes = (
            len(corrections.get("low_confidence_fixes", []))
            + len(corrections.get("title_fixes", []))
            + len(corrections.get("paragraph_fixes", []))
            + len(corrections.get("chapter_fixes", []))
        )
        _log.info(
            "AI review applied: %d fixes (titles=%d, low_conf=%d, "
            "paragraph=%d, chapter=%d)",
            n_fixes,
            len(corrections.get("title_fixes", [])),
            len(corrections.get("low_confidence_fixes", [])),
            len(corrections.get("paragraph_fixes", [])),
            len(corrections.get("chapter_fixes", [])),
        )

        updated_meta = apply_markdown_corrections(
            md_path, meta_path, corrections, issues
        )
        return updated_meta

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
]
