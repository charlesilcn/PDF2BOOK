"""Tests for title level inference (T8)."""

from __future__ import annotations

from pdf2book.config import PostprocessConfig
from pdf2book.ocr.models import BBox, Element, PageResult
from pdf2book.postprocess.structure import infer_title_levels

_PAGE_H = 1000.0
_PAGE_W = 800.0


def _title(text: str, h: float = 40, cy: float = 100, etype: str = "paragraph_title") -> Element:
    return Element(
        type=etype,
        bbox=BBox(x1=100, y1=cy - h / 2, x2=400, y2=cy + h / 2),
        text=text,
        order_index=1,
    )


def _body(h: float = 20, cy: float = 500) -> Element:
    return Element(
        type="text",
        bbox=BBox(x1=50, y1=cy - h / 2, x2=750, y2=cy + h / 2),
        text="body text",
        order_index=2,
    )


def _page(elements: list[Element], idx: int = 0) -> PageResult:
    return PageResult(page_index=idx, width=_PAGE_W, height=_PAGE_H, elements=elements)


# --- Keyword-based classification ----------------------------------------

def test_chapter_keyword_is_h1() -> None:
    page = _page([_title("第一章 风起"), _body()])
    out = infer_title_levels([page], PostprocessConfig())
    assert out[0].elements[0].inferred_level == 1


def test_chapter_english_is_h1() -> None:
    page = _page([_title("Chapter 5 The Day"), _body()])
    out = infer_title_levels([page], PostprocessConfig())
    assert out[0].elements[0].inferred_level == 1


def test_section_keyword_is_h2() -> None:
    # 第X节 -> H2. Needs an H1 ancestor first or enforce_monotonic demotes it.
    pages = [
        _page([_title("第一章", h=48), _body(h=24)], idx=0),
        _page([_title("第一节 由来", h=36), _body(h=24)], idx=1),
    ]
    out = infer_title_levels(pages, PostprocessConfig())
    assert out[1].elements[0].inferred_level == 2


def test_chapter_variants_all_h1() -> None:
    for txt in ["第二回", "第三卷", "第四篇"]:
        page = _page([_title(txt), _body()])
        out = infer_title_levels([page], PostprocessConfig())
        assert out[0].elements[0].inferred_level == 1, txt


# --- Numeric numbering ---------------------------------------------------

def test_numbering_depth_in_sequence() -> None:
    pages = [
        _page([_title("1 A"), _body()], idx=0),
        _page([_title("1.1 B"), _body()], idx=1),
        _page([_title("1.1.1 C"), _body()], idx=2),
    ]
    out = infer_title_levels(pages, PostprocessConfig())
    assert out[0].elements[0].inferred_level == 1
    assert out[1].elements[0].inferred_level == 2
    assert out[2].elements[0].inferred_level == 3


def test_numbering_depth_capped_by_monotonic() -> None:
    """2.3.4.5 has depth 5, but with no ancestors at levels 1..4 it demotes."""
    pages = [
        _page([_title("1 A"), _body()], idx=0),       # H1, seen={1}
        _page([_title("1.1 B"), _body()], idx=1),     # H2, seen={1,2}
        _page([_title("1.1.1 C"), _body()], idx=2),   # H3, seen={1,2,3}
        _page([_title("2.3.4.5 D"), _body()], idx=3), # depth 5 -> demote to 4
    ]
    out = infer_title_levels(pages, PostprocessConfig())
    assert out[3].elements[0].inferred_level == 4


# --- Font-size ratio -----------------------------------------------------

def test_font_ratio_h1_when_2x_body() -> None:
    """A title 2x the body height with no keyword -> H1 (level 1 needs no ancestor)."""
    page = _page([_title("Plain Title", h=48), _body(h=24)])
    out = infer_title_levels([page], PostprocessConfig())
    assert out[0].elements[0].inferred_level == 1


def test_font_ratio_h2_after_h1() -> None:
    """A 1.4x title after an H1 ancestor keeps H2."""
    pages = [
        _page([_title("Big", h=48), _body(h=24)], idx=0),   # H1 by ratio
        _page([_title("Medium", h=36), _body(h=24)], idx=1),  # H2 by ratio
    ]
    out = infer_title_levels(pages, PostprocessConfig())
    assert out[0].elements[0].inferred_level == 1
    assert out[1].elements[0].inferred_level == 2


def test_font_ratio_h3_in_valid_sequence() -> None:
    """A small title (near body height, no keyword) -> H3, kept after H1+H2."""
    pages = [
        _page([_title("第一章", h=48), _body(h=24)], idx=0),  # H1 keyword
        _page([_title("第一节", h=36), _body(h=24)], idx=1),  # H2 keyword
        _page([_title("small", h=22), _body(h=20)], idx=2),  # H3 by ratio fallback
    ]
    out = infer_title_levels(pages, PostprocessConfig())
    assert out[2].elements[0].inferred_level == 3


# --- enforce_monotonic ---------------------------------------------------

def test_enforce_monotonic_demotes_skipped_level() -> None:
    """First title claims H3 (no H1/H2 ancestor) -> demote to H1."""
    page = _page([_title("small title", h=22), _body(h=20)])  # ratio fallback H3
    out = infer_title_levels([page], PostprocessConfig())
    assert out[0].elements[0].inferred_level == 1


def test_enforce_monotonic_keeps_valid_sequence() -> None:
    pages = [
        _page([_title("第一章", h=48), _body(h=24)], idx=0),
        _page([_title("第一节", h=36), _body(h=24)], idx=1),
        _page([_title("small", h=22), _body(h=20)], idx=2),
    ]
    out = infer_title_levels(pages, PostprocessConfig())
    assert out[0].elements[0].inferred_level == 1
    assert out[1].elements[0].inferred_level == 2
    assert out[2].elements[0].inferred_level == 3


# --- Edge cases ----------------------------------------------------------

def test_skips_dropped_elements() -> None:
    title = _title("第一章", h=48)
    title.dropped = True
    page = _page([title, _body(h=24)])
    out = infer_title_levels([page], PostprocessConfig())
    assert out[0].elements[0].inferred_level is None  # not classified


def test_skips_non_title_types() -> None:
    page = _page([_body(h=24)])
    out = infer_title_levels([page], PostprocessConfig())
    assert out[0].elements[0].inferred_level is None


def test_disabled_by_config() -> None:
    page = _page([_title("第一章", h=48), _body(h=24)])
    out = infer_title_levels([page], PostprocessConfig(infer_title_level=False))
    assert out[0].elements[0].inferred_level is None


def test_empty_pages_noop() -> None:
    assert infer_title_levels([], PostprocessConfig()) == []


def test_doc_title_also_classified() -> None:
    page = _page([_title("Book Title", h=60, etype="doc_title"), _body(h=20)])
    out = infer_title_levels([page], PostprocessConfig())
    assert out[0].elements[0].inferred_level == 1  # 60/20 = 3.0 >= 2.0


def test_keyword_overrides_font_ratio() -> None:
    """第X章 keyword wins even if the bbox is small (would otherwise be H3)."""
    page = _page([_title("第一章", h=22), _body(h=20)])  # ratio 1.1 -> H3, but keyword -> H1
    out = infer_title_levels([page], PostprocessConfig())
    assert out[0].elements[0].inferred_level == 1
