"""Tests for PandocBuilder EPUB generation (T10).

These run real Pandoc builds (pypandoc_binary is a core dependency) and
inspect the resulting .epub zip to verify structure, metadata, splitting,
CSS, and image embedding.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pdf2book.epub.builder import EpubBuilder, PandocBuilder, make_epub_builder
from pdf2book.epub.metadata import BookMetadata


def _book_md(work: Path, body: str = "") -> Path:
    p = work / "book.md"
    p.write_text(body, encoding="utf-8")
    return p


def _read_epub(path: Path) -> zipfile.ZipFile:
    return zipfile.ZipFile(path)


def _entries(zf: zipfile.ZipFile) -> list[str]:
    return zf.namelist()


def _epub_text(work: Path, md_body: str, meta: BookMetadata | None = None,
               cover: Path | None = None, css: Path | None = None) -> tuple[Path, zipfile.ZipFile]:
    meta = meta or BookMetadata(title="测试书", author="作者", lang="zh-CN",
                                date="2026-07-06")
    out = work / "book.epub"
    md = _book_md(work, md_body)
    builder = PandocBuilder()
    returned = builder.build(md, meta, out, cover=cover, css=css)
    assert returned == out
    return out, _read_epub(out)


# --- Basic build ----------------------------------------------------------

def test_build_produces_valid_epub(tmp_path: Path) -> None:
    out, zf = _epub_text(tmp_path, "# 第一章 {#ch-1}\n\n正文。\n")
    assert out.exists()
    assert out.stat().st_size > 0
    entries = _entries(zf)
    # Every EPUB must have these.
    assert "mimetype" in entries
    assert any(e.endswith("content.opf") for e in entries)
    assert any(e.endswith("toc.ncx") for e in entries)
    zf.close()


def test_returns_out_path_and_creates_parent(tmp_path: Path) -> None:
    nested = tmp_path / "out" / "deep"
    md = _book_md(tmp_path, "# 章 {#ch-1}\n\n正文。\n")
    out = nested / "book.epub"
    result = PandocBuilder().build(
        md, BookMetadata(title="x"), out
    )
    assert result == out
    assert out.exists()
    assert nested.is_dir()


# --- Metadata embedding ---------------------------------------------------

def test_metadata_embedded_in_opf(tmp_path: Path) -> None:
    out, zf = _epub_text(
        tmp_path, "# 章 {#ch-1}\n\n正文。\n",
        meta=BookMetadata(title="我的书", author="张三", lang="zh-CN", date="2026-01-01"),
    )
    opf = [e for e in _entries(zf) if e.endswith(".opf")][0]
    opf_text = zf.read(opf).decode("utf-8")
    assert "<dc:title" in opf_text and "我的书" in opf_text
    assert "<dc:creator" in opf_text and "张三" in opf_text
    assert "<dc:language>zh-CN</dc:language>" in opf_text
    assert "2026-01-01" in opf_text
    zf.close()


# --- H1 splitting (split-level) -------------------------------------------

def test_h1_split_into_separate_xhtml_files(tmp_path: Path) -> None:
    body = (
        "# 第一章 序 {#ch-1}\n\n第一段。\n\n"
        "## 第一节\n\n小节。\n\n"
        "# 第二章 高潮 {#ch-2}\n\n第二段。\n"
    )
    out, zf = _epub_text(tmp_path, body,
                         meta=BookMetadata(title="x", chapter_level=1))
    entries = _entries(zf)
    chapter_xhtml = sorted(e for e in entries if "ch00" in e and e.endswith(".xhtml"))
    assert len(chapter_xhtml) >= 2, entries
    # H2 (第一节) should live INSIDE ch001, not its own file.
    ch1_text = zf.read(chapter_xhtml[0]).decode("utf-8")
    assert "第一章 序" in ch1_text
    assert "第一节" in ch1_text  # H2 nested in the H1 chapter
    ch2_text = zf.read(chapter_xhtml[1]).decode("utf-8")
    assert "第二章 高潮" in ch2_text
    zf.close()


def test_chapter_level_2_splits_h2_too(tmp_path: Path) -> None:
    body = (
        "# 第一章 {#ch-1}\n\n第一段。\n\n"
        "## 第一节 {#s1}\n\n小节。\n\n"
        "## 第二节 {#s2}\n\n小节二。\n"
    )
    out, zf = _epub_text(tmp_path, body,
                         meta=BookMetadata(title="x", chapter_level=2, toc_depth=2))
    entries = _entries(zf)
    xhtml = sorted(e for e in entries if e.endswith(".xhtml") and "ch00" in e)
    # With split-level=2, H1 and H2 each get their own file → >=2 chunks.
    assert len(xhtml) >= 2, entries
    zf.close()


# --- Anchor preservation --------------------------------------------------

def test_ch_anchor_ids_preserved(tmp_path: Path) -> None:
    out, zf = _epub_text(tmp_path, "# 第一章 {#ch-1}\n\n正文。\n")
    all_text = "\n".join(
        zf.read(e).decode("utf-8", errors="ignore")
        for e in _entries(zf) if e.endswith(".xhtml")
    )
    assert 'id="ch-1"' in all_text
    zf.close()


# --- CSS ------------------------------------------------------------------

def test_default_css_embedded(tmp_path: Path) -> None:
    out, zf = _epub_text(tmp_path, "# 章 {#ch-1}\n\n正文。\n")
    entries = _entries(zf)
    css_entries = [e for e in entries if e.endswith(".css")]
    assert len(css_entries) >= 1, entries
    css_text = zf.read(css_entries[0]).decode("utf-8")
    # Our kindle.css fingerprints.
    assert "line-height" in css_text
    assert "text-indent" in css_text
    # xhtml links the stylesheet.
    xhtml = [e for e in entries if e.endswith(".xhtml") and "ch00" in e]
    if xhtml:
        body = zf.read(xhtml[0]).decode("utf-8")
        assert "stylesheet" in body
    zf.close()


def test_custom_css_used(tmp_path: Path) -> None:
    custom = tmp_path / "custom.css"
    custom.write_text("body { color: red; }\n", encoding="utf-8")
    out, zf = _epub_text(tmp_path, "# 章 {#ch-1}\n\n正文。\n", css=custom)
    css_entries = [e for e in _entries(zf) if e.endswith(".css")]
    merged = "\n".join(zf.read(c).decode("utf-8") for c in css_entries)
    assert "color: red" in merged
    zf.close()


# --- Image embedding ------------------------------------------------------

def test_relative_image_resolved_and_embedded(tmp_path: Path) -> None:
    # Create an image under work_dir/images/ (as images.extract_images would).
    import fitz
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    img_path = img_dir / "p0_e0.png"
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10))
    pix.save(str(img_path))
    pix = None

    body = f"# 章 {{#ch-1}}\n\n![](images/p0_e0.png)\n"
    out, zf = _epub_text(tmp_path, body)
    entries = _entries(zf)
    # Pandoc copies referenced images into EPUB/media/ (or similar).
    media = [e for e in entries if "media" in e.lower() or e.endswith(".png")]
    assert len(media) >= 1, entries
    zf.close()


# --- Cover ----------------------------------------------------------------

def test_cover_image_embedded(tmp_path: Path) -> None:
    import fitz
    cover = tmp_path / "cover.png"
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 150))
    pix.save(str(cover))
    pix = None

    out, zf = _epub_text(
        tmp_path, "# 章 {#ch-1}\n\n正文。\n",
        meta=BookMetadata(title="x"),
        cover=cover,
    )
    entries = _entries(zf)
    # Cover image lands in EPUB/media or as a known cover entry.
    images = [e for e in entries if e.endswith((".png", ".jpg", ".jpeg"))]
    assert len(images) >= 1, entries
    opf = [e for e in entries if e.endswith(".opf")][0]
    opf_text = zf.read(opf).decode("utf-8")
    assert "cover" in opf_text.lower()
    zf.close()


# --- Abstraction / factory ------------------------------------------------

def test_epub_builder_is_abstract() -> None:
    with pytest.raises(TypeError):
        EpubBuilder()  # type: ignore[abstract]


def test_make_epub_builder_returns_pandoc() -> None:
    b = make_epub_builder()
    assert isinstance(b, PandocBuilder)


# --- Cleanup --------------------------------------------------------------

def test_meta_md_written_alongside_book(tmp_path: Path) -> None:
    out, zf = _epub_text(tmp_path, "# 章 {#ch-1}\n\n正文。\n")
    assert (tmp_path / "meta.md").exists()
    meta_text = (tmp_path / "meta.md").read_text(encoding="utf-8")
    assert meta_text.startswith("---")
    assert "title:" in meta_text
    zf.close()
