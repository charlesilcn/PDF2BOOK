"""CIP (图书在版编目) metadata extractor.

Chinese published books carry a standard CIP data block on the copyright
page, following GB/T 12451. The format is:

    图书在版编目(CIP)数据
    [书名]/[作者]．[出版地]：[出版社]，[出版日期]
    ISBN [ISBN号]
    中国版本图书馆CIP数据核字（[年份]）第[编号]号

This module scans all pages' OCR text for the CIP header and extracts
metadata via regex. Returns a `BookMetadata` or `None` when no CIP block
is found.

OCR tolerance: fullwidth punctuation (．；（）) is frequently misrecognized
as halfwidth (.;;();; etc.) or visually similar chars (·一). Regexes use
character classes to absorb these variations.
"""

from __future__ import annotations

import re

from pdf2book.epub.metadata import BookMetadata
from pdf2book.ocr.models import PageResult

# CIP header: "图书在版编目(CIP)数据" with fullwidth/halfwidth parens.
_CIP_HEADER_RE = re.compile(r"图书在版编目\s*[（(]CIP[)）]\s*数据")

# CIP main line: [title]/[author]．[place]：[publisher]，[date]
# The separator between author and place is a fullwidth period ． that OCR
# frequently misreads as . · 一 。 (Chinese fullwidth period U+3002). Place/
# publisher/date separators are ： and ， which also have halfwidth forms.
_CIP_LINE_RE = re.compile(
    r"(?P<title>.+?)"
    r"\s*/\s*"
    r"(?P<author>.+?)"
    r"\s*[．.·一。]\s*"
    r"(?P<pub_place>[^：:]+)"
    r"\s*[：:]\s*"
    r"(?P<publisher>[^，,]+)"
    r"\s*[，,]\s*"
    r"(?P<pub_date>\d{4}[.\-年]\d{1,2}(?:月)?)"
)

# ISBN: "ISBN 978-7-5511-4721-7" (allow spaces/dashes).
_ISBN_RE = re.compile(r"ISBN\s*[:：]?\s*([\d\-]+)")

# CIP cert number: "CIP数据核字（2019）第119530号"
_CIP_CERT_RE = re.compile(
    r"CIP数据核字\s*[（(]\s*(\d{4})\s*[)）]\s*第\s*([\d]+)\s*号"
)


def extract_metadata(pages: list[PageResult]) -> BookMetadata | None:
    """Scan all pages for a CIP data block and extract metadata.

    Returns a `BookMetadata` with title/author/date populated, or `None`
    when no CIP block is found on any page. The search is page-order
    independent (copyright page position varies), but typically hits
    within the first 10 pages.

    A page may contain the CIP header text ("图书在版编目(CIP)数据") as
    part of a larger text block (e.g. a publisher address line) without
    having the full CIP data. In that case `_parse_cip_page` returns
    None and we continue scanning subsequent pages.

    The CIP block can also span two pages: the header on page N and the
    data line (title/author/publisher/date) on page N+1. When the header
    is found but the data line is not, we retry the search on the
    concatenation of the current and next page's text.
    """
    if not pages:
        return None

    for i, page in enumerate(pages):
        text = _page_text(page)
        if not _CIP_HEADER_RE.search(text):
            continue
        result = _parse_cip_page(text)
        if result is not None:
            return result
        # Header present but body malformed on this page. The CIP data
        # line may have flowed to the next page (common when the header
        # sits at the bottom of a page). Retry with the next page's text
        # appended before falling back to scanning later pages.
        if i + 1 < len(pages):
            next_text = _page_text(pages[i + 1])
            combined = f"{text}\n{next_text}"
            result = _parse_cip_page(combined)
            if result is not None:
                return result

    return None


def _page_text(page: PageResult) -> str:
    """Concatenate all non-dropped text elements on a page."""
    parts: list[str] = []
    for el in page.elements:
        if el.dropped:
            continue
        if el.text:
            parts.append(el.text)
    return "\n".join(parts)


def _parse_cip_page(text: str) -> BookMetadata | None:
    """Parse a CIP data page into BookMetadata.

    Returns None if the CIP line regex fails (header present but body
    malformed — rare but possible with severe OCR corruption).
    """
    m = _CIP_LINE_RE.search(text)
    if not m:
        return None

    title = _clean(m.group("title"))
    author = _clean(m.group("author"))
    pub_place = _clean(m.group("pub_place"))
    # OCR often misreads the CIP separator ． as 一 before the place name
    # (e.g. "．石家庄" → "一石家庄"). Strip the leading 一 artifact.
    if pub_place.startswith("一"):
        pub_place = pub_place[1:]
    pub_date = _normalize_date(m.group("pub_date"))

    # ISBN is optional (some CIP blocks omit it or OCR misses it).
    isbn_m = _ISBN_RE.search(text)
    isbn = isbn_m.group(1).strip() if isbn_m else ""

    # Publisher is a first-class metadata field (Pandoc `publisher` variable).
    # ISBN goes into `rights` for provenance, per GB/T 12451 conventions.
    publisher = _clean(m.group("publisher"))
    rights = f"ISBN {isbn}" if isbn else None

    return BookMetadata(
        title=title or "Untitled",
        author=author or "Unknown",
        lang="zh-CN",
        date=pub_date,
        publisher=publisher or None,
        rights=rights,
    )


def _clean(s: str) -> str:
    """Strip whitespace and normalize OCR artifacts in extracted fields."""
    s = s.strip()
    # OCR sometimes inserts spaces between CJK chars; remove them.
    if _has_cjk(s):
        s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", s)
    return s


def _has_cjk(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", s))


def _normalize_date(s: str) -> str:
    """Normalize '2019.6' / '2019-06' / '2019年6月' → '2019.6'."""
    s = s.strip()
    s = s.replace("年", ".").replace("月", "")
    s = s.replace("-", ".")
    # Collapse multiple dots.
    s = re.sub(r"\.{2,}", ".", s)
    return s
