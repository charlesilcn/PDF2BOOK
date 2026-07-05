"""Tests for header/footer removal (T6)."""

from __future__ import annotations

from pdf2book.config import PostprocessConfig
from pdf2book.ocr.models import BBox, Element, PageResult
from pdf2book.postprocess.header_footer import remove

_PAGE_H = 1000.0


def _text(text: str, cy: float, etype: str = "text") -> Element:
    """Build a text element whose vertical center is at `cy`."""
    return Element(
        type=etype,
        bbox=BBox(x1=0, y1=cy - 10, x2=100, y2=cy + 10),
        text=text,
    )


def _page(elements: list[Element], idx: int = 0) -> PageResult:
    return PageResult(page_index=idx, width=800, height=_PAGE_H, elements=elements)


def test_drop_by_label_header_footer_number() -> None:
    page = _page(
        [
            _text("正文", cy=500),
            _text("书眉", cy=50, etype="header"),
            _text("页脚", cy=950, etype="footer"),
            _text("12", cy=960, etype="number"),
        ]
    )
    out = remove([page], PostprocessConfig())
    els = out[0].elements
    assert els[0].dropped is False  # 正文保留
    assert els[1].dropped is True   # header
    assert els[2].dropped is True   # footer
    assert els[3].dropped is True   # number


def test_drop_numeric_page_number_in_margin() -> None:
    """A pure-numeric text element in the bottom margin is dropped."""
    page = _page(
        [
            _text("正文内容", cy=500),
            _text("42", cy=970),  # bottom margin, numeric
        ]
    )
    out = remove([page], PostprocessConfig())
    assert out[0].elements[0].dropped is False
    assert out[0].elements[1].dropped is True


def test_keep_numeric_text_in_body() -> None:
    """A numeric text element in the body (e.g. a year '1842') is kept."""
    page = _page(
        [
            _text("1842", cy=500),  # body, numeric
        ]
    )
    out = remove([page], PostprocessConfig())
    assert out[0].elements[0].dropped is False


def test_drop_cross_page_running_head_top() -> None:
    """Same string at top of >=3 pages is a running head -> dropped."""
    pages = [
        _page([_text("红楼梦", cy=30), _text(f"正文{idx}", cy=500)], idx=idx)
        for idx in range(4)
    ]
    out = remove(pages, PostprocessConfig())
    for page in out:
        assert page.elements[0].dropped is True   # 红楼梦
        assert page.elements[1].dropped is False  # 正文


def test_keep_running_head_below_repeat_threshold() -> None:
    """Same string on only 2 pages (< 3) is not dropped (could be section title)."""
    pages = [
        _page([_text("楔子", cy=30), _text("正文", cy=500)], idx=idx)
        for idx in range(2)
    ]
    out = remove(pages, PostprocessConfig())
    for page in out:
        assert page.elements[0].dropped is False


def test_drop_cross_page_running_head_bottom() -> None:
    """Same string at bottom of >=3 pages is a footer running head -> dropped."""
    pages = [
        _page([_text("正文", cy=500), _text("作者名", cy=970)], idx=idx)
        for idx in range(3)
    ]
    out = remove(pages, PostprocessConfig())
    for page in out:
        assert page.elements[0].dropped is False
        assert page.elements[1].dropped is True


def test_similar_running_heads_clustered() -> None:
    """Near-identical running heads (minor OCR drift) cluster and drop together."""
    pages = [
        _page([_text("曹雪芹著", cy=30), _text("正文", cy=500)], idx=0),
        _page([_text("曹雪芹著", cy=30), _text("正文", cy=500)], idx=1),
        _page([_text("曹雪芹著", cy=30), _text("正文", cy=500)], idx=2),
        _page([_text("曹雪芹著.", cy=30), _text("正文", cy=500)], idx=3),  # stray dot
    ]
    out = remove(pages, PostprocessConfig())
    for page in out:
        assert page.elements[0].dropped is True
        assert page.elements[1].dropped is False


def test_disabled_when_config_says_so() -> None:
    page = _page([_text("header", cy=30, etype="header")])
    out = remove([page], PostprocessConfig(drop_header_footer=False))
    assert out[0].elements[0].dropped is False


def test_empty_pages_noop() -> None:
    assert remove([], PostprocessConfig()) == []


def test_does_not_drop_titles_in_margin() -> None:
    """A short paragraph_title at the top of 3 pages (e.g. chapter title) must
    NOT be dropped — only `text` elements are running-head candidates."""
    pages = [
        _page(
            [
                _text("第一章", cy=30, etype="paragraph_title"),
                _text("正文", cy=500),
            ],
            idx=idx,
        )
        for idx in range(3)
    ]
    out = remove(pages, PostprocessConfig())
    for page in out:
        # paragraph_title is not a running-head candidate; only `text` is.
        assert page.elements[0].dropped is False
