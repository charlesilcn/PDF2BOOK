"""Page type classifier (Phase 3).

Classifies each page into a semantic type using CIP metadata + text features.
The classification drives downstream handling:

  - Decorative pages (cover/frontispiece/copyright/illustration) bypass
    OCR-based layout; `epub.builder` inserts their `page_image_path` as
    raw PDF page images.
  - Content pages (toc/preface/body/appendix) enter `book.md` for Pandoc
    typesetting.

Classification rules (priority cascade):
  1. Cover/Frontispiece (early pages only): book title in a heading element
     + page_index < 5. Checked before copyright because cross-page merger
     pollution can append CIP keywords from the copyright page to the
     frontispiece's last text element, which would otherwise trigger a
     false COPYRIGHT classification.
  2. Back cover (last few pages): ≥2 publisher/price/editor keywords
     (定价/上架建议/责任编辑/...). Checked before copyright because back
     covers often carry ISBN and 版权所有.
  3. Copyright: CIP/ISBN/版权所有 keywords
  4. TOC: "目录" + ≥3 lines of "text......page" pattern
  5. Preface: "前言"/"序言"/"序"/"后记" title
  6. Appendix: "附录"/"索引"/"参考文献"
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
_TITLE_LABELS = frozenset({"doc_title", "paragraph_title", "content_title"})

# Copyright page keywords (CIP data block indicators).
_COPYRIGHT_KEYWORDS = ["图书在版编目", "CIP数据", "ISBN", "版权所有", "出版编目"]

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

    # 1. Cover/Frontispiece (early pages only): book title in a *heading
    # element* + page_index < 5. Checked BEFORE copyright because the
    # cross-page merger can append CIP keywords from the copyright page
    # to the frontispiece's last text element (e.g. "河北·石家庄" +
    # "图书在版编目(CIP)数据" → "河北·石家庄图书在版编目(CIP)数据"),
    # which would otherwise trigger a false COPYRIGHT classification.
    # A true copyright page rarely carries the book title as a doc_title
    # heading element, so this priority is safe.
    if page.page_index < _FRONT_PAGE_WINDOW:
        if book_title and _has_title_containing(page, book_title):
            if page.page_index == 0:
                return PageType.COVER
            return PageType.FRONTISPIECE
        # Page 0 with very few text elements, NO chapter-heading elements,
        # and a decorative image is almost certainly a cover (title is
        # typically a stylized image, not OCR-detected text). The image
        # requirement distinguishes real covers from sparse body pages at
        # page 0 (common in test fixtures and short PDFs without front
        # matter). The no-heading check prevents catching body pages that
        # start with a chapter title.
        elif (
            page.page_index == 0
            and text_count <= 3
            and not _has_heading_element(page)
            and image_els
        ):
            return PageType.COVER

    # 2. Back cover (last few pages): publisher/price/editor keywords.
    # Checked before copyright because back covers often carry ISBN and
    # "版权所有" which would otherwise trigger a false COPYRIGHT hit.
    # Requires ≥2 distinct keywords to avoid false positives on body pages
    # that happen to mention "责任编辑" in a colophon.
    if total > 0 and page.page_index >= total - _BACK_PAGE_WINDOW:
        back_kw_count = sum(1 for kw in _BACK_COVER_KEYWORDS if kw in text)
        if back_kw_count >= _BACK_COVER_MIN_KEYWORDS:
            return PageType.BACK_COVER

    # 3. Copyright page: CIP/ISBN keywords.
    if any(kw in text for kw in _COPYRIGHT_KEYWORDS):
        return PageType.COPYRIGHT

    # 3.5. Frontispiece fallback: pages 1-2 with very few text elements,
    # a decorative image, and no sentence-ending punctuation. Catches扉页
    # where OCR didn't detect the title as a heading element (e.g. stylized
    # text labeled as `image`). The image requirement distinguishes real
    # frontispieces from sparse body pages (which rarely carry images at
    # index 1-2); the no-sentence-end check excludes body pages that happen
    # to have few text elements but contain complete sentences.
    if (
        0 < page.page_index <= 2
        and text_count <= 3
        and image_els
        and not any(ch in text for ch in _SENTENCE_ENDS)
    ):
        return PageType.FRONTISPIECE

    # 4. TOC page: TOC header keyword + ≥3 page-number entries, OR ≥5 entries
    # (catches TOC continuation pages that don't carry the "目录" header).
    entry_count = len(_TOC_ENTRY_RE.findall(text)) + len(_TOC_ENTRY_SLASH_RE.findall(text))
    if _has_toc_keyword(text) and entry_count >= 3:
        return PageType.TOC
    if entry_count >= 5:
        return PageType.TOC

    # 5. Preface: title-like element with preface keyword.
    if _has_title_with_keyword(page, _PREFACE_KEYWORDS):
        return PageType.PREFACE

    # 6. Appendix: title-like element with appendix keyword.
    if _has_title_with_keyword(page, _APPENDIX_KEYWORDS):
        return PageType.APPENDIX

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


def _has_heading_element(page: PageResult) -> bool:
    """True if the page has any non-dropped heading-typed element.

    Used by cover detection to skip pages that carry chapter headings
    (paragraph_title / content_title / doc_title) — such pages are body
    content, not covers, even when text_count is low.
    """
    return any(
        not el.dropped and el.type in _TITLE_LABELS
        for el in page.elements
    )


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
        if el.type == "text" and len(el.text) <= 20 and not any(
            ch in el.text for ch in _SENTENCE_ENDS
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
