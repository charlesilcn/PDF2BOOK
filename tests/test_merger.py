"""Tests for cross-page paragraph merging (T7)."""

from __future__ import annotations

from pdf2book.config import PostprocessConfig
from pdf2book.ocr.models import BBox, Element, PageResult
from pdf2book.postprocess.merger import merge_paragraphs


def _text(text: str, order: int = 0) -> Element:
    return Element(type="text", bbox=BBox(x1=0, y1=0, x2=100, y2=10), text=text, order_index=order)


def _page(elements: list[Element], idx: int) -> PageResult:
    return PageResult(page_index=idx, width=800, height=1000, elements=elements)


def test_merge_split_chinese_paragraph() -> None:
    """Paragraph split mid-sentence across two pages -> single merged text."""
    p0 = _page([_text("这是一段被分页打断的正文内容，它在第", order=1)], idx=0)
    p1 = _page([_text("二页继续，没有句号结尾。", order=1)], idx=1)
    out = merge_paragraphs([p0, p1], PostprocessConfig())
    assert out[0].elements[0].text == "这是一段被分页打断的正文内容，它在第二页继续，没有句号结尾。"
    assert out[1].elements[0].dropped is True


def test_no_merge_when_current_ends_with_period() -> None:
    p0 = _page([_text("第一段已完结。", order=1)], idx=0)
    p1 = _page([_text("第二段重新开始。", order=1)], idx=1)
    out = merge_paragraphs([p0, p1], PostprocessConfig())
    assert out[0].elements[0].text == "第一段已完结。"
    assert out[1].elements[0].dropped is False
    assert out[1].elements[0].text == "第二段重新开始。"


def test_no_merge_when_current_ends_with_closing_quote() -> None:
    """Closing quote 」 after a terminator should also block merging."""
    p0 = _page([_text("他说：“好了。”", order=1)], idx=0)
    p1 = _page([_text("她回答。", order=1)], idx=1)
    out = merge_paragraphs([p0, p1], PostprocessConfig())
    assert out[1].elements[0].dropped is False


def test_no_merge_when_next_starts_with_chapter_title() -> None:
    p0 = _page([_text("正文未结束", order=1)], idx=0)
    p1 = _page([_text("第二章 新的开始", order=1)], idx=1)
    out = merge_paragraphs([p0, p1], PostprocessConfig())
    assert out[0].elements[0].text == "正文未结束"
    assert out[1].elements[0].dropped is False


def test_no_merge_when_next_starts_with_chapter_english() -> None:
    p0 = _page([_text("body text continues", order=1)], idx=0)
    p1 = _page([_text("Chapter 5 The Next Day", order=1)], idx=1)
    out = merge_paragraphs([p0, p1], PostprocessConfig())
    assert out[1].elements[0].dropped is False


def test_merge_english_with_space_separator() -> None:
    p0 = _page([_text("The quick brown", order=1)], idx=0)
    p1 = _page([_text("fox jumps.", order=1)], idx=1)
    out = merge_paragraphs([p0, p1], PostprocessConfig())
    assert out[0].elements[0].text == "The quick brown fox jumps."
    assert out[1].elements[0].dropped is True


def test_no_merge_when_next_is_title_type() -> None:
    """If next page's first live element is a paragraph_title, do not merge."""
    title_el = Element(
        type="paragraph_title",
        bbox=BBox(x1=0, y1=0, x2=100, y2=10),
        text="第二节",
        order_index=1,
    )
    p0 = _page([_text("正文未结束", order=1)], idx=0)
    p1 = _page([title_el], idx=1)
    out = merge_paragraphs([p0, p1], PostprocessConfig())
    assert out[0].elements[0].text == "正文未结束"


def test_no_merge_when_either_dropped() -> None:
    """If the next page's first text is already dropped (e.g. running head), skip."""
    e0 = _text("正文未结束", order=1)
    e1 = _text("书眉串", order=1)
    e1.dropped = True
    p0 = _page([e0], idx=0)
    p1 = _page([e1], idx=1)
    out = merge_paragraphs([p0, p1], PostprocessConfig())
    assert out[0].elements[0].text == "正文未结束"


def test_merge_three_way_chain() -> None:
    """Paragraph split across 3 pages -> all joined onto page[0]."""
    p0 = _page([_text("起始于第一页", order=1)], idx=0)
    p1 = _page([_text("继续于第二页", order=1)], idx=1)
    p2 = _page([_text("结束于第三页。", order=1)], idx=2)
    out = merge_paragraphs([p0, p1, p2], PostprocessConfig())
    assert out[0].elements[0].text == "起始于第一页继续于第二页结束于第三页。"
    assert out[1].elements[0].dropped is True
    assert out[2].elements[0].dropped is True


def test_disabled_by_config() -> None:
    p0 = _page([_text("正文", order=1)], idx=0)
    p1 = _page([_text("继续", order=1)], idx=1)
    out = merge_paragraphs([p0, p1], PostprocessConfig(merge_cross_page=False))
    assert out[0].elements[0].text == "正文"
    assert out[1].elements[0].dropped is False


def test_single_page_noop() -> None:
    p0 = _page([_text("正文", order=1)], idx=0)
    out = merge_paragraphs([p0], PostprocessConfig())
    assert out[0].elements[0].text == "正文"


def test_empty_pages_noop() -> None:
    assert merge_paragraphs([], PostprocessConfig()) == []


def test_no_live_text_on_next_page() -> None:
    """If next page has no live `text` element, do not crash; do not merge."""
    p0 = _page([_text("正文未结束", order=1)], idx=0)
    p1 = _page(
        [Element(type="image", bbox=BBox(x1=0, y1=0, x2=100, y2=10), text="", order_index=1)],
        idx=1,
    )
    out = merge_paragraphs([p0, p1], PostprocessConfig())
    assert out[0].elements[0].text == "正文未结束"


def test_respects_order_index_not_list_order() -> None:
    """The 'last text on page' is the one with the highest order_index, not
    the last in the elements list."""
    p0 = _page(
        [
            _text("靠后的正文段", order=5),
            _text("靠前的正文段", order=2),
        ],
        idx=0,
    )
    p1 = _page([_text("继续。", order=1)], idx=1)
    out = merge_paragraphs([p0, p1], PostprocessConfig())
    # The high-order element should be merged; the low-order one kept.
    merged = [e for e in out[0].elements if e.type == "text" and not e.dropped]
    assert any(e.text == "靠后的正文段继续。" for e in merged)
    assert any(e.text == "靠前的正文段" for e in merged)
