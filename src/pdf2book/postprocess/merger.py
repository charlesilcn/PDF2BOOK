"""Cross-page paragraph merging (T7).

When a paragraph is split across a page break, PP-StructureV3 produces two
separate `text` elements: one at the bottom of page[i] (not ending in a
sentence terminator) and one at the top of page[i+1]. This module stitches
them back into a single element on page[i], appending page[i+1]'s text.

Rules for NOT merging (the next element starts a new block):
  * Either element is marked `dropped` (e.g. running head, page number).
  * The next element's type is not `text` (titles, images, tables start
    a new block).
  * The current element ends with a sentence terminator: 。！？…"')]!?"
    (Chinese + ASCII).
  * The next element starts with an indent (two leading spaces / full-width
    space), a CJK character that typically opens a new paragraph, or
    matches a title pattern (第X章/Chapter N).

When merging, Chinese text is concatenated with no separator (the page
break is invisible to the reader). ASCII text gets a single space if
neither side already has trailing/leading whitespace.

Mutates Element.text in place on the first element; the second element is
marked `dropped=True` (so downstream markdown assembly skips it). Returns
the same list for chaining.
"""

from __future__ import annotations

import re

from pdf2book.config import PostprocessConfig
from pdf2book.ocr.models import Element, PageResult

# Sentence terminators that signal "paragraph ends here, do not merge".
_TERMINATORS = frozenset("。！？；…\u3000\"')]!?\"")
# Unicode left/right quotes that close a sentence.
_QUOTE_CLOSE = frozenset("\u201d\u2019）」』】）")

# Patterns that signal "next element starts a new block, do not merge".
_TITLE_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十百千0-9]+[章回节卷篇]"),
    re.compile(r"^Chapter\s+[IVX0-9]+", re.IGNORECASE),
]

# Full-width space / indent signals a new paragraph.
_INDENT_RE = re.compile(r"^[\s\u3000]+")


def merge_paragraphs(pages: list[PageResult], cfg: PostprocessConfig) -> list[PageResult]:
    """Stitch text elements split across page boundaries.

    Mutates elements in place; returns the same list for chaining.
    No-op when `cfg.merge_cross_page` is False or there are < 2 pages.

    Algorithm: walk a flat sequence of live `text` elements in reading order
    (page_index, then order_index). Track an "open paragraph" — the element
    awaiting possible continuation. When the next element lives on a
    different page AND the open paragraph doesn't end with a terminator AND
    the next element doesn't start a new block, join them and mark the next
    element dropped. The open paragraph stays open across the merge so a
    paragraph split across 3+ pages chains correctly.
    """
    if not cfg.merge_cross_page or len(pages) < 2:
        return pages

    seq: list[tuple[int, Element]] = []
    for page_idx, page in enumerate(pages):
        live_texts = sorted(
            [e for e in page.elements if e.type == "text" and not e.dropped],
            key=lambda e: e.order_index,
        )
        for el in live_texts:
            seq.append((page_idx, el))

    if len(seq) < 2:
        return pages

    open_el = seq[0][1]
    open_page = seq[0][0]
    for i in range(1, len(seq)):
        page_idx, el = seq[i]
        if (
            page_idx != open_page
            and not _ends_with_terminator(open_el.text)
            and not _starts_new_block(el.text)
        ):
            open_el.text = _join(open_el.text, el.text)
            el.dropped = True
            # The open paragraph now extends to this page; keep open_el
            # so a 3rd page can continue the same paragraph.
            open_page = page_idx
        else:
            open_el = el
            open_page = page_idx

    return pages


def _ends_with_terminator(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return True  # empty paragraph: nothing to merge into
    return stripped[-1] in _TERMINATORS or stripped[-1] in _QUOTE_CLOSE


def _starts_new_block(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return True
    if _INDENT_RE.match(text):
        return True
    return any(p.match(stripped) for p in _TITLE_PATTERNS)


def _join(a: str, b: str) -> str:
    """Join two text fragments across a page break.

    CJK-to-CJK: no separator. ASCII boundary: single space if neither side
    has whitespace at the seam.
    """
    a_stripped = a.rstrip()
    b_stripped = b.lstrip()
    if not a_stripped:
        return b
    if not b_stripped:
        return a
    last = a_stripped[-1]
    first = b_stripped[0]
    if _is_cjk(last) and _is_cjk(first):
        return a_stripped + b_stripped
    if last.isspace() or first.isspace():
        return a_stripped + " " + b_stripped
    return a_stripped + " " + b_stripped


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        # CJK Unified Ideographs + extensions A/B.
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
        # CJK Symbols and Punctuation (、。〈〉《》「」 etc.).
        or 0x3000 <= code <= 0x303F
        # Fullwidth ASCII Forms (，！？：；""'' etc.).
        or 0xFF00 <= code <= 0xFFEF
    )
