r"""Header/footer removal (T6).

Three-pass strategy:
  1. Trust PP-StructureV3 labels: any element whose `type` is in
     {header, footer, header_image, footer_image, number, footnote,
     aside_text} is marked `dropped=True`.
  2. Pure-numeric page numbers: a `text` element at top/bottom margin whose
     content matches `^\d{1,4}$` is dropped (catches PP misses).
  3. Cross-page running heads: collect short `text` strings from the top
     margin of every page; if the same string (normalized edit distance
     ratio >= 0.7) appears on >= 3 pages, drop all occurrences. Same for
     the bottom margin. This catches book/author/chapter running heads
     that PP labels as `text` because they look like body text.

The function mutates Element.dropped in place and returns the same list
(pipeline expects a list[PageResult] for chaining).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from pdf2book.config import PostprocessConfig
from pdf2book.ocr.models import PageResult

# PP-StructureV3 labels that are always dropped (see model_settings.
# markdown_ignore_labels in the spike fixture).
_DROP_LABELS = frozenset(
    {
        "header",
        "footer",
        "header_image",
        "footer_image",
        "number",
        "footnote",
        "aside_text",
    }
)

_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")

# Margins as a fraction of page height. Elements whose vertical center
# falls outside [TOP_MARGIN, 1-BOTTOM_MARGIN] are "margin elements".
_TOP_MARGIN = 0.08
_BOTTOM_MARGIN = 0.08

# A short string (<= this many chars) in the margin is a running-head candidate.
_MAX_RUNNING_HEAD_LEN = 60

# SequenceMatcher ratio threshold: ratio >= this means "same running head".
# Plan spec: normalized edit distance < 0.3  <=>  ratio > 0.7.
_SIMILARITY_THRESHOLD = 0.7

# A running head must repeat on at least this many pages to be dropped.
_MIN_REPEAT_PAGES = 3


def remove(pages: list[PageResult], cfg: PostprocessConfig) -> list[PageResult]:
    """Mark header/footer/page-number/running-head elements as dropped.

    Mutates `Element.dropped` in place; returns the same list for chaining.
    If `cfg.drop_header_footer` is False, returns immediately (no-op).
    """
    if not cfg.drop_header_footer or not pages:
        return pages

    _drop_by_label(pages)
    _drop_numeric_page_numbers(pages)
    _drop_cross_page_running_heads(pages)
    return pages


def _drop_by_label(pages: list[PageResult]) -> None:
    for page in pages:
        for el in page.elements:
            if el.type in _DROP_LABELS:
                el.dropped = True


# Element types that may carry page numbers misclassified by PP-StructureV3
# as titles. Pure-numeric content in the margin from any of these types is
# almost certainly a page number, not a heading.
_PAGE_NUMBER_CANDIDATE_TYPES = frozenset({"text", "paragraph_title", "content_title", "doc_title"})


def _drop_numeric_page_numbers(pages: list[PageResult]) -> None:
    for page in pages:
        for el in page.elements:
            if el.dropped or el.type not in _PAGE_NUMBER_CANDIDATE_TYPES:
                continue
            if not _is_in_margin(el.bbox.cy, page.height):
                continue
            if _PAGE_NUMBER_RE.match(el.text):
                el.dropped = True


def _drop_cross_page_running_heads(pages: list[PageResult]) -> None:
    """Find short margin strings that repeat across >=3 pages and drop them."""
    top_candidates = _collect_margin_candidates(pages, position="top")
    bottom_candidates = _collect_margin_candidates(pages, position="bottom")

    top_repeated = _find_repeated_strings(top_candidates)
    bottom_repeated = _find_repeated_strings(bottom_candidates)

    if not top_repeated and not bottom_repeated:
        return

    for page in pages:
        for el in page.elements:
            if el.dropped or el.type != "text":
                continue
            text = el.text.strip()
            if len(text) > _MAX_RUNNING_HEAD_LEN:
                continue
            cy = el.bbox.cy
            if _is_in_top_margin(cy, page.height) and _matches_any(text, top_repeated):
                el.dropped = True
            elif _is_in_bottom_margin(cy, page.height) and _matches_any(text, bottom_repeated):
                el.dropped = True


def _collect_margin_candidates(
    pages: list[PageResult], position: str
) -> list[tuple[str, str]]:
    """Return [(page_hash_key, text)] for short text elements in the margin.

    `page_hash_key` is a synthetic unique-per-page id so we can count
    distinct pages, not total occurrences (a running head appears once
    per page, not multiple times).
    """
    candidates: list[tuple[str, str]] = []
    for i, page in enumerate(pages):
        for el in page.elements:
            if el.type != "text" or el.dropped:
                continue
            text = el.text.strip()
            if not text or len(text) > _MAX_RUNNING_HEAD_LEN:
                continue
            cy = el.bbox.cy
            if position == "top" and _is_in_top_margin(cy, page.height):
                candidates.append((f"page-{i}", text))
            elif position == "bottom" and _is_in_bottom_margin(cy, page.height):
                candidates.append((f"page-{i}", text))
    return candidates


def _find_repeated_strings(
    candidates: list[tuple[str, str]],
) -> list[str]:
    """Cluster candidate strings by similarity; return those on >=3 distinct pages.

    Greedy clustering: pick the first unassigned candidate as a cluster
    anchor, assign all similar candidates to it, count distinct pages.
    Strings appearing on >= _MIN_REPEAT_PAGES distinct pages are returned
    as "running heads to drop".
    """
    if len(candidates) < _MIN_REPEAT_PAGES:
        return []

    anchors: list[tuple[str, set[str]]] = []  # (anchor_text, page_keys)
    for page_key, text in candidates:
        matched = False
        for i, (anchor, pages_set) in enumerate(anchors):
            if _similar(text, anchor):
                anchors[i] = (anchor, pages_set | {page_key})
                matched = True
                break
        if not matched:
            anchors.append((text, {page_key}))

    return [anchor for anchor, pages_set in anchors if len(pages_set) >= _MIN_REPEAT_PAGES]


def _matches_any(text: str, anchors: list[str]) -> bool:
    return any(_similar(text, a) for a in anchors)


def _similar(a: str, b: str) -> bool:
    if a == b:
        return True
    # Quick length filter: very different lengths can't be similar enough.
    if abs(len(a) - len(b)) > max(len(a), len(b)) * 0.3:
        return False
    return SequenceMatcher(None, a, b).ratio() >= _SIMILARITY_THRESHOLD


def _is_in_margin(cy: float, page_height: float) -> bool:
    return _is_in_top_margin(cy, page_height) or _is_in_bottom_margin(cy, page_height)


def _is_in_top_margin(cy: float, page_height: float) -> bool:
    if page_height <= 0:
        return False
    return cy / page_height < _TOP_MARGIN


def _is_in_bottom_margin(cy: float, page_height: float) -> bool:
    if page_height <= 0:
        return False
    return cy / page_height > 1 - _BOTTOM_MARGIN
