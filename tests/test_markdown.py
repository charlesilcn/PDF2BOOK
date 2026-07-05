"""Tests for markdown assembly (T9 `structure.to_markdown`)."""

from __future__ import annotations

from pathlib import Path

from pdf2book.ocr.models import BBox, Element, PageResult
from pdf2book.postprocess.structure import to_markdown

_PAGE_W = 800.0
_PAGE_H = 1000.0


def _el(
    etype: str,
    text: str,
    order: int = 0,
    inferred_level: int | None = None,
    dropped: bool = False,
) -> Element:
    return Element(
        type=etype,
        bbox=BBox(x1=0, y1=0, x2=100, y2=20),
        text=text,
        order_index=order,
        inferred_level=inferred_level,
        dropped=dropped,
    )


def _page(elements: list[Element], idx: int = 0) -> PageResult:
    return PageResult(
        page_index=idx, width=_PAGE_W, height=_PAGE_H, elements=elements
    )


# --- Basic rendering ------------------------------------------------------

def test_title_and_text_render_as_header_and_paragraph(tmp_path: Path) -> None:
    page = _page(
        [_el("paragraph_title", "第一章", order=0, inferred_level=1),
         _el("text", "正文段落。", order=1)]
    )
    out = to_markdown([page], None, tmp_path)
    body = out.read_text(encoding="utf-8")
    assert body == "# 第一章 {#ch-1}\n\n正文段落。\n"


def test_h2_and_h3_use_inferred_level(tmp_path: Path) -> None:
    page = _page(
        [_el("paragraph_title", "第一节", order=0, inferred_level=2),
         _el("paragraph_title", "小标题", order=1, inferred_level=3)]
    )
    body = to_markdown([page], None, tmp_path).read_text(encoding="utf-8")
    assert "## 第一节" in body
    assert "### 小标题" in body
    # No {#ch-N} on non-H1 headers.
    assert "{#ch-" not in body


def test_title_without_inferred_level_falls_back_to_h3(tmp_path: Path) -> None:
    page = _page([_el("paragraph_title", "孤儿标题", order=0)])
    body = to_markdown([page], None, tmp_path).read_text(encoding="utf-8")
    assert body.startswith("### 孤儿标题")


# --- H1 chapter id --------------------------------------------------------

def test_h1_gets_incrementing_ch_id(tmp_path: Path) -> None:
    pages = [
        _page([_el("paragraph_title", "第一章", inferred_level=1)], idx=0),
        _page([_el("text", "中间正文。", inferred_level=None)], idx=1),
        _page([_el("paragraph_title", "第二章", inferred_level=1)], idx=2),
    ]
    body = to_markdown(pages, None, tmp_path).read_text(encoding="utf-8")
    assert "# 第一章 {#ch-1}" in body
    assert "# 第二章 {#ch-2}" in body


def test_repeated_h1_titles_get_distinct_ids(tmp_path: Path) -> None:
    """Two chapters with the same title must not collide on Pandoc anchors."""
    pages = [
        _page([_el("paragraph_title", "序章", inferred_level=1)], idx=0),
        _page([_el("paragraph_title", "序章", inferred_level=1)], idx=1),
    ]
    body = to_markdown(pages, None, tmp_path).read_text(encoding="utf-8")
    assert "# 序章 {#ch-1}" in body
    assert "# 序章 {#ch-2}" in body


# --- Non-text element types ----------------------------------------------

def test_image_renders_as_image_link(tmp_path: Path) -> None:
    page = _page([_el("image", "images/p0_e0.png", order=0)])
    body = to_markdown([page], None, tmp_path).read_text(encoding="utf-8")
    assert body.strip() == "![](images/p0_e0.png)"


def test_figure_and_chart_also_render_as_images(tmp_path: Path) -> None:
    page = _page([
        _el("figure", "images/p0_e0.png", order=0),
        _el("chart", "images/p0_e1.png", order=1),
    ])
    body = to_markdown([page], None, tmp_path).read_text(encoding="utf-8")
    assert "![](images/p0_e0.png)" in body
    assert "![](images/p0_e1.png)" in body


def test_table_renders_as_raw_html(tmp_path: Path) -> None:
    html = "<table><tr><td>A</td></tr></table>"
    page = _page([_el("table", html, order=0)])
    body = to_markdown([page], None, tmp_path).read_text(encoding="utf-8")
    assert body.strip() == html


def test_formula_renders_as_display_math(tmp_path: Path) -> None:
    page = _page([_el("display_formula", r"E = mc^2", order=0)])
    body = to_markdown([page], None, tmp_path).read_text(encoding="utf-8")
    assert "$$" in body
    assert "E = mc^2" in body


def test_unknown_type_renders_as_paragraph(tmp_path: Path) -> None:
    page = _page([_el("seal", "印章文字", order=0)])
    body = to_markdown([page], None, tmp_path).read_text(encoding="utf-8")
    assert body.strip() == "印章文字"


# --- Ordering and skipping ------------------------------------------------

def test_elements_ordered_by_order_index_within_page(tmp_path: Path) -> None:
    page = _page([
        _el("text", "第二个", order=2),
        _el("text", "第一个", order=1),
    ])
    body = to_markdown([page], None, tmp_path).read_text(encoding="utf-8")
    assert body.index("第一个") < body.index("第二个")


def test_pages_concatenated_in_page_index_order(tmp_path: Path) -> None:
    pages = [
        _page([_el("text", "页二", order=0)], idx=1),
        _page([_el("text", "页一", order=0)], idx=0),
    ]
    body = to_markdown(pages, None, tmp_path).read_text(encoding="utf-8")
    assert body.index("页一") < body.index("页二")


def test_dropped_elements_skipped(tmp_path: Path) -> None:
    page = _page([
        _el("text", "保留", order=0),
        _el("text", "丢弃", order=1, dropped=True),
    ])
    body = to_markdown([page], None, tmp_path).read_text(encoding="utf-8")
    assert "保留" in body
    assert "丢弃" not in body


def test_empty_text_elements_skipped(tmp_path: Path) -> None:
    page = _page([
        _el("text", "   ", order=0),
        _el("text", "有内容", order=1),
    ])
    body = to_markdown([page], None, tmp_path).read_text(encoding="utf-8")
    assert body.strip() == "有内容"


# --- Edge cases -----------------------------------------------------------

def test_empty_pages_produces_empty_file(tmp_path: Path) -> None:
    out = to_markdown([], {"title": "x"}, tmp_path)
    assert out.exists()
    assert out.read_text(encoding="utf-8") == ""


def test_creates_work_dir_if_missing(tmp_path: Path) -> None:
    work = tmp_path / "nested" / "work"
    page = _page([_el("text", "hi", order=0)])
    out = to_markdown([page], None, work)
    assert out == work / "book.md"
    assert out.exists()


def test_meta_does_not_pollute_body(tmp_path: Path) -> None:
    """meta is accepted but not written into the markdown body (T10 owns it)."""
    page = _page([_el("text", "正文", order=0)])
    body = to_markdown([page], {"title": "书名", "author": "作者"}, tmp_path).read_text(encoding="utf-8")
    assert "书名" not in body
    assert "作者" not in body
    assert "title:" not in body
    assert body.strip() == "正文"


def test_output_path_returned(tmp_path: Path) -> None:
    page = _page([_el("text", "x", order=0)])
    out = to_markdown([page], None, tmp_path)
    assert out == tmp_path / "book.md"
