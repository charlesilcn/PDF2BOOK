"""Apply AI review results back to pages and metadata (Phase 5.6).

The `AIClient.review_all` returns a `ReviewResult` with four categories of
AI decisions. This module writes those decisions back into the in-memory
`PageResult` / `BookMetadata` / `Element` objects so the pipeline can
regenerate `book.md` and `meta.md` with the corrected data.

Mapping:
  * `ReviewResult.metadata` → update `BookMetadata` fields (only non-null
    values are applied; null means "AI couldn't determine, keep current").
  * `ReviewResult.low_confidence` → set `Element.ai_corrected` on matching
    elements (looked up by page_index + order_index from the item id).
  * `ReviewResult.titles` → update `Element.inferred_level` on matching
    title elements.
  * `ReviewResult.page_types` → update `PageResult.page_type` on matching
    pages.

Id matching: low_confidence and title results use `p{N}_e{M}` ids that
encode (page_index, order_index). The applier parses these to locate the
target element. Malformed ids are silently skipped (defensive).
"""

from __future__ import annotations

import re

from pdf2book.epub.metadata import BookMetadata
from pdf2book.ocr.models import PageResult
from pdf2book.review.ai_client import ReviewResult

# Match "p{page_index}_e{order_index}" — the id format from collect_review_items.
_ID_RE = re.compile(r"^p(\d+)_e(\d+)$")


def apply_review_results(
    pages: list[PageResult],
    metadata: BookMetadata | None,
    review_result: ReviewResult,
) -> tuple[list[PageResult], BookMetadata]:
    """Apply AI review results back to pages and metadata.

    Mutates `pages` (Element.ai_corrected, Element.inferred_level,
    PageResult.page_type) and returns the (possibly new) `BookMetadata`.
    When `metadata` is None and the AI provided metadata, a new BookMetadata
    is created.

    Args:
        pages: All PageResult objects (post-OCR, post-rule-postprocess).
        metadata: Current BookMetadata (may be None if CIP extraction failed).
        review_result: The ReviewResult from AIClient.review_all().

    Returns:
        (pages, metadata) — the same pages list (mutated in place) and the
        updated BookMetadata (or a new one if metadata was None and AI
        provided values).
    """
    # Build a lookup for fast page access by index.
    pages_by_index = {p.page_index: p for p in pages}

    # --- 1. Apply low-confidence corrections ------------------------------
    for correction in review_result.low_confidence:
        item_id = correction.get("id", "")
        status = correction.get("status", "")
        corrected = correction.get("corrected", "")

        # Only apply successful corrections. "unclear" and "manual" are
        # preserved as markers in the text (e.g. "[需校对]") for manual
        # review — they're written to ai_corrected so to_markdown uses them.
        if status == "skipped":
            continue  # AI disabled, leave element as-is

        parsed = _parse_id(item_id)
        if parsed is None:
            continue
        page_idx, order_idx = parsed
        page = pages_by_index.get(page_idx)
        if page is None:
            continue

        el = _find_element(page, order_idx)
        if el is None:
            continue

        # Write the corrected text (or the [需校对]/[UNCLEAR] marker).
        # to_markdown already prefers ai_corrected over raw text.
        el.ai_corrected = corrected

    # --- 2. Apply title level adjustments --------------------------------
    for title in review_result.titles:
        item_id = title.get("id", "")
        level = title.get("level")

        if level is None or not isinstance(level, int):
            continue
        if level < 1 or level > 6:
            continue  # invalid level, skip

        parsed = _parse_id(item_id)
        if parsed is None:
            continue
        page_idx, order_idx = parsed
        page = pages_by_index.get(page_idx)
        if page is None:
            continue

        el = _find_element(page, order_idx)
        if el is None:
            continue

        el.inferred_level = level

    # --- 3. Apply page type adjustments ----------------------------------
    for page_type_entry in review_result.page_types:
        page_idx = page_type_entry.get("page_index")
        page_type = page_type_entry.get("page_type")

        if page_idx is None or page_type is None:
            continue
        if not isinstance(page_idx, int) or not isinstance(page_type, str):
            continue

        page = pages_by_index.get(page_idx)
        if page is None:
            continue

        # Validate page_type is a known value.
        valid_types = {
            "cover", "frontispiece", "copyright", "toc", "preface",
            "body", "illustration", "appendix", "unknown",
        }
        if page_type not in valid_types:
            continue

        page.page_type = page_type

    # --- 4. Apply metadata ------------------------------------------------
    updated_metadata = metadata
    if review_result.metadata:
        meta_dict = review_result.metadata
        # Create a new BookMetadata if we didn't have one.
        if updated_metadata is None:
            updated_metadata = BookMetadata()

        # Only apply non-null values (null means AI couldn't determine).
        title = meta_dict.get("title")
        if isinstance(title, str) and title.strip():
            updated_metadata.title = title.strip()

        author = meta_dict.get("author")
        if isinstance(author, str) and author.strip():
            updated_metadata.author = author.strip()

        lang = meta_dict.get("lang")
        if isinstance(lang, str) and lang.strip():
            updated_metadata.lang = lang.strip()

        date = meta_dict.get("date")
        if isinstance(date, str) and date.strip():
            updated_metadata.date = date.strip()

    return pages, updated_metadata if updated_metadata is not None else BookMetadata()


def _parse_id(item_id: str) -> tuple[int, int] | None:
    """Parse "p{N}_e{M}" into (page_index, order_index). None on failure."""
    m = _ID_RE.match(item_id)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _find_element(page: PageResult, order_index: int):
    """Find an element by order_index on a page. None if not found."""
    for el in page.elements:
        if el.order_index == order_index:
            return el
    return None


__all__ = ["apply_review_results"]
