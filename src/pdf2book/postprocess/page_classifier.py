"""Page type classifier (Phase 3).

Classifies each page into a semantic type using OCR layout labels + CIP
metadata + text features. The classification drives downstream handling:

  - Decorative pages (cover/frontispiece/copyright/illustration) bypass
    OCR-based layout; `epub.builder` inserts their `page_image_path` as
    raw PDF page images.
  - Content pages (toc/preface/body/appendix) enter `book.md` for Pandoc
    typesetting.

Two-pass design:
  Pass 1 (pre-scan): collect global info from all pages — chapter titles
    (S_titles) and TOC entry texts (S_toc_entries) — for cross-validation.
  Pass 2 (classify): classify each page using a priority cascade that
    leverages OCR block_labels as the primary signal, with text content
    and global info as secondary validation.

Classification rules (priority cascade):
  1. Cover/Frontispiece with book title in a heading element (first 5
     pages). Checked before keyword-based rules so a real frontispiece
     whose text absorbed CIP keywords via cross-page merging isn't
     misclassified as copyright.
  2. Back cover (last few pages): ≥2 publisher/price/editor keywords.
  3. Copyright: CIP/ISBN/版权所有 keywords.
  4. Copyright fallback: ≥2 extended publishing keywords (first 5 pages).
  5. Cover fallback: page 0 with image + ≤3 text elements + no headings.
  6. Frontispiece fallback: pages 1-2, no sentence punctuation, with
     book title/author in text OR image+few elements OR pure-text no
     headings.
  7. TOC: keyword + ≥3 entries, or ≥5 entries, or entries matching ≥40%
     of global chapter titles (cross-validation method C).
  8. Preface: title element with preface keyword.
  9. Appendix: title element with appendix keyword.
  10. Illustration: has image elements + NO content-class labels at all
      (text/title/figure_title). Uses all content labels, not just text,
      so pages whose text was merged away by the cross-page merger but
      still carry a heading are NOT misclassified.
  11. Body: has content-class labels + sufficient text (≥50 chars, or
      sentence-ending punctuation, or a title element, or figure_title).
  12. Unknown: none of the above.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from enum import Enum

from pdf2book.epub.metadata import BookMetadata
from pdf2book.ocr.models import PageResult
from pdf2book.postprocess.images import IMAGE_LABELS


class PageType(str, Enum):
    """Semantic page type driving downstream handling."""

    COVER = "cover"
    FRONTISPIECE = "frontispiece"
    COPYRIGHT = "copyright"
    TOC = "toc"
    PREFACE = "preface"
    BODY = "body"
    ILLUSTRATION = "illustration"
    APPENDIX = "appendix"
    UNKNOWN = "unknown"
    BACK_COVER = "back_cover"


# Decorative pages bypass OCR layout; their PDF page image is used directly.
DECORATIVE_TYPES = frozenset(
    {
        PageType.COVER,
        PageType.FRONTISPIECE,
        PageType.COPYRIGHT,
        PageType.ILLUSTRATION,
        PageType.BACK_COVER,
    }
)

# Title-typed element labels (mirror TITLE_LABELS in structure.py to avoid
# a circular import). Used by cover/frontispiece detection to check whether
# the book title appears in a *heading* element vs. being mentioned in prose.
TITLE_LABELS = frozenset({"doc_title", "paragraph_title", "content_title"})
_TITLE_LABELS = TITLE_LABELS  # backward-compat alias for existing references

# OCR content-class labels: elements that carry substantive page content.
# Used by ILLUSTRATION/BODY classification — a page with ANY of these labels
# is a content page, not a decorative illustration. Includes figure_title
# (chart/figure captions are content, not decoration).
CONTENT_LABELS = frozenset({"text", "paragraph_title", "doc_title",
                             "content_title", "figure_title"})

# Minimum text length (chars) for BODY classification without title/sentence.
_BODY_MIN_TEXT_CHARS = 50

# TOC cross-validation: minimum match ratio between page entries and global
# chapter titles (method C). 0.4 = 40% of entries must fuzzy-match a title.
_TOC_MATCH_RATIO = 0.4

# Fuzzy-match similarity threshold for TOC↔title cross-validation.
_FUZZY_MATCH_THRESHOLD = 0.6

# Copyright page keywords (CIP data block indicators).
_COPYRIGHT_KEYWORDS = ["图书在版编目", "CIP数据", "ISBN", "版权所有", "出版编目"]

# Copyright page fallback keywords (used when OCR quality is poor and the
# primary CIP keywords are garbled). Requires ≥2 simultaneous hits to
# avoid false positives on body pages that happen to mention one term.
_COPYRIGHT_FALLBACK_KEYWORDS = [
    "出版社",
    "印刷",
    "发行",
    "版次",
    "印次",
    "开本",
    "字数",
    "责任编辑",
    "定价",
]

# Back cover keywords (price, category, editor info typical of a back cover).
_BACK_COVER_KEYWORDS = ["定价", "上架建议", "责任编辑", "美术编辑", "责任校对", "封面设计"]

# Preface/appendix title keywords.
_PREFACE_KEYWORDS = ["前言", "序言", "序", "后记", "跋", "引言"]
_APPENDIX_KEYWORDS = ["附录", "索引", "参考文献", "参考书目"]

# TOC entry pattern: "标题......页码" (3+ dots or leaders).
_TOC_ENTRY_RE = re.compile(r"^.+?[…\.·]{3,}\s*\d+\s*$", re.MULTILINE)
# TOC entry pattern: "标题／页码" or "标题/ 页码" (slash + digits at line end).
_TOC_ENTRY_SLASH_RE = re.compile(r"^.+?[/／]\s*\d+\s*$", re.MULTILINE)

# TOC header keywords (CJK + English).
_TOC_KEYWORDS = ["目录", "目 录", "CONTENTS", "Contents", "contents"]

# Sentence-ending punctuation for body detection.
_SENTENCE_ENDS = frozenset("。！？；")

# First-N-pages heuristic for cover/frontispiece detection.
_FRONT_PAGE_WINDOW = 5

# Last-N-pages heuristic for back cover detection.
_BACK_PAGE_WINDOW = 3

# Minimum number of back-cover keywords required for BACK_COVER classification.
# Set to 2 so a single incidental keyword (e.g. "责任编辑" appearing in a body
# page colophon) doesn't trigger a false positive; real back covers carry
# multiple publisher/price/editing credits together.
_BACK_COVER_MIN_KEYWORDS = 2


def classify_pages(
    pages: list[PageResult],
    metadata: BookMetadata | None,
) -> list[PageResult]:
    """Classify each page, setting `page.page_type`.

    Mutates `page_type` in place; returns the same list for chaining.
    `metadata` (from CIP extraction) provides the book title and author
    for cover/frontispiece detection; pass None when CIP extraction failed.

    Two-pass design:
      Pass 1: collect global chapter titles (S_titles) and TOC entry texts
              (S_toc_entries) for cross-validation.
      Pass 2: classify each page using the priority cascade, leveraging
              global info for TOC method C (entry↔title matching).
    """
    if not pages:
        return pages

    book_title = metadata.title if metadata and metadata.title != "Untitled" else ""
    book_author = metadata.author if metadata else ""
    total = len(pages)

    # Pass 1: pre-scan for global cross-validation data.
    all_titles = _collect_title_texts(pages)
    all_toc_entries = _collect_toc_entry_texts(pages)

    # Pass 2: classify each page.
    for page in pages:
        page.page_type = _classify_one(
            page, book_title, book_author, total,
            all_titles, all_toc_entries,
        ).value

    return pages


def _classify_one(
    page: PageResult,
    book_title: str,
    book_author: str,
    total: int,
    all_titles: set[str],
    all_toc_entries: set[str],
) -> PageType:
    """Classify a single page by priority cascade.

    Uses OCR block_labels as the primary signal (content_els / image_els),
    with text content and global cross-validation data as secondary signals.
    """
    text = _page_text(page)
    text_els = [e for e in page.elements if e.type == "text" and not e.dropped]
    image_els = [e for e in page.elements if e.type in IMAGE_LABELS and not e.dropped]
    content_els = [e for e in page.elements if e.type in CONTENT_LABELS and not e.dropped]
    title_els = [e for e in page.elements if e.type in TITLE_LABELS and not e.dropped]
    text_count = len(text_els)
    content_count = len(content_els)
    text_chars = sum(len(e.text) for e in content_els)
    has_sentence_end = bool(text) and any(ch in text for ch in _SENTENCE_ENDS)

    # 1. Cover/Frontispiece with book title in a *heading element* (first 5
    #    pages). Checked BEFORE copyright/back-cover because the cross-page
    #    merger can append CIP or back-cover keywords from later pages to a
    #    frontispiece's last text element — but a real frontispiece carries
    #    the book title in a heading, which the keyword checks can't override.
    if page.page_index < _FRONT_PAGE_WINDOW:
        if book_title and _has_title_containing(page, book_title):
            if page.page_index == 0:
                return PageType.COVER
            return PageType.FRONTISPIECE

    # 2. Back cover (last few pages): publisher/price/editor keywords.
    if total > 0 and page.page_index >= total - _BACK_PAGE_WINDOW:
        back_kw_count = sum(1 for kw in _BACK_COVER_KEYWORDS if kw in text)
        if back_kw_count >= _BACK_COVER_MIN_KEYWORDS:
            return PageType.BACK_COVER

    # 3. Copyright page: CIP/ISBN keywords.
    if any(kw in text for kw in _COPYRIGHT_KEYWORDS):
        return PageType.COPYRIGHT

    # 3.5. Copyright page fallback: ≥2 extended publishing keywords.
    if (
        page.page_index < _FRONT_PAGE_WINDOW
        and text_count <= 10
        and sum(1 for kw in _COPYRIGHT_FALLBACK_KEYWORDS if kw in text) >= 2
    ):
        return PageType.COPYRIGHT

    # 4. Cover fallback: page 0 with very few text elements, NO chapter-
    #    heading elements, and a decorative image. Runs after copyright so
    #    a page-0 copyright page (rare but possible) isn't misclassified.
    if (
        page.page_index == 0
        and text_count <= 3
        and not _has_heading_element(page)
        and image_els
    ):
        return PageType.COVER

    # 5. Frontispiece fallback: pages 1-2 with book title or author in text,
    #    no sentence-ending punctuation. The author check catches扉页 where
    #    OCR labels author/publisher as plain `text` (not doc_title). Runs
    #    after copyright/back-cover so those page types win when they appear
    #    in the first few pages.
    if 0 < page.page_index <= 2 and not has_sentence_end:
        has_book_or_author = (
            (book_title and book_title in text)
            or (book_author and book_author in text)
        )
        if has_book_or_author and content_count <= 5:
            return PageType.FRONTISPIECE
        # Original frontispiece heuristic (no title/author match):
        # image+few elements, or pure-text with no headings.
        if image_els and text_count <= 5:
            return PageType.FRONTISPIECE
        if not image_els and text_count <= 5 and not _has_heading_element(page):
            return PageType.FRONTISPIECE

    # 6. TOC page: three detection methods.
    #    A. TOC header keyword + ≥3 page-number entries.
    #    B. ≥5 page-number entries (catches continuation pages).
    #    C. Entries fuzzy-matching ≥40% of global chapter titles
    #       (cross-validation — works even when "目录" is OCR-garbled).
    entry_count = len(_TOC_ENTRY_RE.findall(text)) + len(_TOC_ENTRY_SLASH_RE.findall(text))
    if _has_toc_keyword(text) and entry_count >= 3:
        return PageType.TOC
    if entry_count >= 5:
        return PageType.TOC
    if all_titles and entry_count >= 2:
        page_entries = _extract_toc_entries_from_page(text)
        if page_entries:
            match_count = sum(
                1 for e in page_entries if _fuzzy_match_any(e, all_titles)
            )
            if match_count / len(page_entries) >= _TOC_MATCH_RATIO:
                return PageType.TOC

    # 7. Preface: title-like element with preface keyword.
    if _has_title_with_keyword(page, _PREFACE_KEYWORDS):
        return PageType.PREFACE

    # 8. Appendix: title-like element with appendix keyword.
    if _has_title_with_keyword(page, _APPENDIX_KEYWORDS):
        return PageType.APPENDIX

    # 9. Illustration: has image + NO content-class labels at all.
    #    Uses content_els (text + all title types + figure_title), not just
    #    text_count, so a page whose text was merged away by the cross-page
    #    merger but still carries a paragraph_title is NOT misclassified.
    if image_els and content_count == 0:
        return PageType.ILLUSTRATION
    if image_els and _image_area_ratio(page, image_els) > 0.6 and content_count <= 2:
        return PageType.ILLUSTRATION

    # 10. Body: has content-class labels + sufficient text.
    #     Sufficient = has a title element, or ≥50 chars of text, or
    #     sentence-ending punctuation, or a figure_title (chart pages).
    if content_els and (
        title_els
        or text_chars >= _BODY_MIN_TEXT_CHARS
        or has_sentence_end
        or any(e.type == "figure_title" for e in content_els)
    ):
        return PageType.BODY

    # 11. Unknown: insufficient features.
    return PageType.UNKNOWN


def _page_text(page: PageResult) -> str:
    """Concatenate non-dropped text elements."""
    return "\n".join(e.text for e in page.elements if not e.dropped and e.text)


# ---------------------------------------------------------------------------
# Pass 1: global pre-scan helpers (collect cross-validation data)
# ---------------------------------------------------------------------------


def _collect_title_texts(pages: list[PageResult]) -> set[str]:
    """Collect all title-typed element texts across the book (S_titles).

    Used by TOC method C: if a page's ``／页码`` entries fuzzy-match these
    titles, the page is a TOC even when the ``目录`` header is OCR-garbled.
    Only includes texts of length ≥ 2 to exclude page numbers misdetected
    as titles (e.g. ``"3"``).
    """
    titles: set[str] = set()
    for page in pages:
        for el in page.elements:
            if el.dropped or el.type not in TITLE_LABELS:
                continue
            t = el.text.strip()
            if len(t) >= 2:
                titles.add(t)
    return titles


def _collect_toc_entry_texts(pages: list[PageResult]) -> set[str]:
    """Collect all ``／页码`` entry texts across the book (S_toc_entries).

    Used for chapter-title correction: TOC entries can validate/correct
    body chapter titles that suffered OCR errors.
    """
    entries: set[str] = set()
    for page in pages:
        for el in page.elements:
            if el.dropped or el.type != "text":
                continue
            for line in el.text.split("\n"):
                line = line.strip()
                m = _TOC_ENTRY_SLASH_RE.match(line)
                if m:
                    # Strip the trailing "／数字" to get the entry title.
                    entry_text = re.sub(r"[/／]\s*\d+\s*$", "", line).strip()
                    if len(entry_text) >= 2:
                        entries.add(entry_text)
    return entries


def _extract_toc_entries_from_page(text: str) -> list[str]:
    """Extract ``／页码`` entry titles from a single page's text.

    Returns the title portion (before the slash) for each matching line.
    Used by TOC method C cross-validation.
    """
    entries: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if _TOC_ENTRY_SLASH_RE.match(line):
            entry_text = re.sub(r"[/／]\s*\d+\s*$", "", line).strip()
            if len(entry_text) >= 2:
                entries.append(entry_text)
    return entries


def _fuzzy_match_any(entry: str, titles: set[str]) -> bool:
    """True if ``entry`` fuzzy-matches any string in ``titles``.

    Matching rules (any one suffices):
      1. Substring containment (either direction) — catches short titles
         that are substrings of longer ones.
      2. ``SequenceMatcher`` ratio ≥ 0.6 — catches OCR errors where 1-2
         characters differ (e.g. ``刻`` → ``剌``).
    """
    for title in titles:
        if entry in title or title in entry:
            return True
        if SequenceMatcher(None, entry, title).ratio() >= _FUZZY_MATCH_THRESHOLD:
            return True
    return False


def _has_toc_keyword(text: str) -> bool:
    """True if text contains any TOC header keyword (目录/CONTENTS)."""
    return any(kw in text for kw in _TOC_KEYWORDS)


def _has_title_keyword(page: PageResult, keywords: list[str]) -> bool:
    """True if any title-typed element starts with a keyword."""
    for el in page.elements:
        if el.dropped:
            continue
        if el.type in ("doc_title", "paragraph_title", "content_title"):
            stripped = el.text.strip()
            if any(stripped.startswith(kw) for kw in keywords):
                return True
    return False


def _has_title_with_keyword(page: PageResult, keywords: list[str]) -> bool:
    """True if any title-typed element starts with a keyword (alias)."""
    return _has_title_keyword(page, keywords)


def _has_heading_element(page: PageResult) -> bool:
    """True if the page has any non-dropped heading-typed element.

    Used by cover detection to skip pages that carry chapter headings
    (paragraph_title / content_title / doc_title) — such pages are body
    content, not covers, even when text_count is low.
    """
    return any(not el.dropped and el.type in _TITLE_LABELS for el in page.elements)


def _has_title_containing(page: PageResult, book_title: str) -> bool:
    """True if the book title appears in a heading or short text label.

    Unlike checking `book_title in page_text` (which would match prose
    mentions like "《地球的故事》引发了..."), this only matches when the
    title appears in:
      - a heading element (doc_title / paragraph_title / content_title), or
      - a short text element (≤20 chars, no sentence-ending punctuation),
        which covers扉页 where OCR labels the title as plain `text`.

    Long prose paragraphs mentioning the book title are excluded so body
    pages (e.g. a preface page quoting the book title) are not
    misclassified as cover/frontispiece.
    """
    for el in page.elements:
        if el.dropped:
            continue
        if book_title not in el.text:
            continue
        if el.type in _TITLE_LABELS:
            return True
        if (
            el.type == "text"
            and len(el.text) <= 20
            and not any(ch in el.text for ch in _SENTENCE_ENDS)
        ):
            return True
    return False


def _image_area_ratio(page: PageResult, image_els: list) -> float:
    """Ratio of image element area to page area."""
    if page.width <= 0 or page.height <= 0:
        return 0.0
    page_area = page.width * page.height
    image_area = sum(
        (e.bbox.width * e.bbox.height) for e in image_els if e.bbox.width > 0 and e.bbox.height > 0
    )
    return image_area / page_area if page_area > 0 else 0.0
