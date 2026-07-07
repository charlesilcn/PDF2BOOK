"""Page type classifier (Phase 3).

Classifies each page into a semantic type using CIP metadata + text features.
The classification drives downstream handling:

  - Decorative pages (cover/frontispiece/copyright/illustration) bypass
    OCR-based layout; `epub.builder` inserts their `page_image_path` as
    raw PDF page images.
  - Content pages (toc/preface/body/appendix) enter `book.md` for Pandoc
    typesetting.

Classification rules (priority cascade):
  1. Copyright: CIP/ISBN/版权所有 keywords
  2. TOC: "目录" + ≥3 lines of "text......page" pattern
  3. Preface: "前言"/"序言"/"序"/"后记" title
  4. Appendix: "附录"/"索引"/"参考文献"
  5. Cover: contains book title (from CIP) + ≤3 text elements + first 5 pages
  6. Frontispiece: contains title/author + ≤5 text elements + first 5 pages
  7. Illustration: image area >60% + ≤2 text elements
  8. Body: ≥5 text elements + complete sentences
  9. Unknown: none of the above
"""

from __future__ import annotations

import re
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


# Decorative pages bypass OCR layout; their PDF page image is used directly.
DECORATIVE_TYPES = frozenset(
    {PageType.COVER, PageType.FRONTISPIECE, PageType.COPYRIGHT, PageType.ILLUSTRATION}
)

# Copyright page keywords (CIP data block indicators).
_COPYRIGHT_KEYWORDS = ["图书在版编目", "CIP数据", "ISBN", "版权所有", "出版编目"]

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


def classify_pages(
    pages: list[PageResult],
    metadata: BookMetadata | None,
) -> list[PageResult]:
    """Classify each page, setting `page.page_type`.

    Mutates `page_type` in place; returns the same list for chaining.
    `metadata` (from CIP extraction) provides the book title for cover
    detection; pass None when CIP extraction failed.
    """
    if not pages:
        return pages

    book_title = metadata.title if metadata and metadata.title != "Untitled" else ""
    total = len(pages)

    for page in pages:
        page.page_type = _classify_one(page, book_title, total).value

    return pages


def _classify_one(
    page: PageResult, book_title: str, total: int
) -> PageType:
    """Classify a single page by priority cascade."""
    text = _page_text(page)
    text_els = [e for e in page.elements if e.type == "text" and not e.dropped]
    image_els = [e for e in page.elements if e.type in IMAGE_LABELS and not e.dropped]
    text_count = len(text_els)

    # 1. Copyright page: CIP/ISBN keywords.
    if any(kw in text for kw in _COPYRIGHT_KEYWORDS):
        return PageType.COPYRIGHT

    # 2. TOC page: TOC header keyword + ≥3 page-number entries.
    entry_count = len(_TOC_ENTRY_RE.findall(text)) + len(_TOC_ENTRY_SLASH_RE.findall(text))
    if _has_toc_keyword(text) and entry_count >= 3:
        return PageType.TOC

    # 3. Preface: title-like element with preface keyword.
    if _has_title_with_keyword(page, _PREFACE_KEYWORDS):
        return PageType.PREFACE

    # 4. Appendix: title-like element with appendix keyword.
    if _has_title_with_keyword(page, _APPENDIX_KEYWORDS):
        return PageType.APPENDIX

    # 5-6. Cover/frontispiece: title match + low text count + early position.
    if page.page_index < _FRONT_PAGE_WINDOW and book_title:
        if book_title in text:
            if text_count <= 3:
                return PageType.COVER
            if text_count <= 8:
                return PageType.FRONTISPIECE
        elif page.page_index == 0 and text_count <= 3:
            return PageType.COVER

    # 7. Illustration: zero-text pages with any image, or image-dominated pages.
    if image_els and text_count == 0:
        return PageType.ILLUSTRATION
    if image_els and _image_area_ratio(page, image_els) > 0.6 and text_count <= 2:
        return PageType.ILLUSTRATION

    # 8. Body: sufficient text + complete sentences.
    if text_count >= 5 or (text and any(ch in text for ch in _SENTENCE_ENDS)):
        return PageType.BODY

    # 9. Unknown: insufficient features.
    return PageType.UNKNOWN


def _page_text(page: PageResult) -> str:
    """Concatenate non-dropped text elements."""
    return "\n".join(e.text for e in page.elements if not e.dropped and e.text)


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


def _image_area_ratio(page: PageResult, image_els: list) -> float:
    """Ratio of image element area to page area."""
    if page.width <= 0 or page.height <= 0:
        return 0.0
    page_area = page.width * page.height
    image_area = sum(
        (e.bbox.width * e.bbox.height) for e in image_els if e.bbox.width > 0 and e.bbox.height > 0
    )
    return image_area / page_area if page_area > 0 else 0.0
