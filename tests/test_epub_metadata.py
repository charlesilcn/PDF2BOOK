"""Tests for EPUB metadata model + YAML writer (T10)."""

from __future__ import annotations

from pathlib import Path

import yaml

from pdf2book.config import EpubConfig
from pdf2book.epub.metadata import BookMetadata, read_meta_yaml, write_meta_yaml


# --- BookMetadata ---------------------------------------------------------

def test_defaults() -> None:
    m = BookMetadata()
    assert m.title == "Untitled"
    assert m.author == "Unknown"
    assert m.lang == "zh-CN"
    assert m.toc_depth == 2
    assert m.chapter_level == 1
    assert m.date is None
    assert m.rights is None


def test_from_pdf_meta_surfaces_title_author() -> None:
    m = BookMetadata.from_pdf_meta({"title": "红楼梦", "author": "曹雪芹"})
    assert m.title == "红楼梦"
    assert m.author == "曹雪芹"
    assert m.lang == "zh-CN"
    assert m.date is not None  # auto-filled with today


def test_from_pdf_meta_defaults_on_empty() -> None:
    m = BookMetadata.from_pdf_meta({})
    assert m.title == "Untitled"
    assert m.author == "Unknown"


def test_from_pdf_meta_strips_whitespace() -> None:
    m = BookMetadata.from_pdf_meta({"title": "  书名  ", "author": "\t作者\n"})
    assert m.title == "书名"
    assert m.author == "作者"


def test_from_pdf_meta_respects_epub_config() -> None:
    cfg = EpubConfig(toc_depth=3, chapter_level=2)
    m = BookMetadata.from_pdf_meta({"title": "x"}, cfg)
    assert m.toc_depth == 3
    assert m.chapter_level == 2


def test_from_pdf_meta_default_config_when_none() -> None:
    m = BookMetadata.from_pdf_meta({"title": "x"}, None)
    assert m.toc_depth == EpubConfig().toc_depth
    assert m.chapter_level == EpubConfig().chapter_level


# --- write_meta_yaml ------------------------------------------------------

def test_write_meta_yaml_produces_yaml_block(tmp_path: Path) -> None:
    m = BookMetadata(title="测试书", author="作者", lang="zh-CN", date="2026-07-06")
    path = write_meta_yaml(m, tmp_path)
    assert path == tmp_path / "meta.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert text.rstrip().endswith("---")
    # The body between the delimiters is valid YAML.
    body = text.strip().strip("-").strip()
    parsed = yaml.safe_load(body)
    assert parsed["title"] == "测试书"
    assert parsed["author"] == "作者"
    assert parsed["lang"] == "zh-CN"
    assert parsed["date"] == "2026-07-06"


def test_write_meta_yaml_omits_none_fields(tmp_path: Path) -> None:
    m = BookMetadata(title="x", date=None, rights=None)
    path = write_meta_yaml(m, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "date" not in text
    assert "rights" not in text


def test_write_meta_yaml_includes_rights_when_set(tmp_path: Path) -> None:
    m = BookMetadata(title="x", rights="All rights reserved")
    text = write_meta_yaml(m, tmp_path).read_text(encoding="utf-8")
    assert "rights" in text
    assert "All rights reserved" in text


def test_write_meta_yaml_creates_work_dir(tmp_path: Path) -> None:
    work = tmp_path / "nested" / "work"
    m = BookMetadata(title="x")
    path = write_meta_yaml(m, work)
    assert path.exists()
    assert work.is_dir()


def test_write_meta_yaml_unicode_preserved(tmp_path: Path) -> None:
    m = BookMetadata(title="日本語の本", author="著者")
    text = write_meta_yaml(m, tmp_path).read_text(encoding="utf-8")
    assert "日本語の本" in text
    assert "著者" in text
    # No escape sequences for CJK.
    assert "\\u" not in text


# --- read_meta_yaml -------------------------------------------------------


def test_read_meta_yaml_round_trip(tmp_path: Path) -> None:
    """write_meta_yaml -> read_meta_yaml preserves serialized fields.

    toc_depth and chapter_level are build parameters (not serialized in
    meta.md), so they fall back to defaults on read.
    """
    original = BookMetadata(
        title="测试书", author="作者", lang="zh-CN", date="2026-07-06",
        rights="CC-BY", toc_depth=3, chapter_level=2,
    )
    path = write_meta_yaml(original, tmp_path)
    loaded = read_meta_yaml(path)
    assert loaded.title == original.title
    assert loaded.author == original.author
    assert loaded.lang == original.lang
    assert loaded.date == original.date
    assert loaded.rights == original.rights
    # Build params are NOT serialized → defaults on read.
    assert loaded.toc_depth == BookMetadata().toc_depth
    assert loaded.chapter_level == BookMetadata().chapter_level


def test_read_meta_yaml_defaults_on_empty_block(tmp_path: Path) -> None:
    """A YAML block with no keys returns default BookMetadata."""
    path = tmp_path / "meta.md"
    path.write_text("---\n---\n", encoding="utf-8")
    loaded = read_meta_yaml(path)
    assert loaded.title == "Untitled"
    assert loaded.author == "Unknown"


def test_read_meta_yaml_defaults_on_missing_file(tmp_path: Path) -> None:
    """No file → default BookMetadata (not an error)."""
    loaded = read_meta_yaml(tmp_path / "nonexistent.md")
    assert loaded.title == "Untitled"
    assert loaded.author == "Unknown"


def test_read_meta_yaml_partial_fields(tmp_path: Path) -> None:
    """Only some fields present → defaults for the rest."""
    path = tmp_path / "meta.md"
    path.write_text("---\ntitle: 仅标题\n---\n", encoding="utf-8")
    loaded = read_meta_yaml(path)
    assert loaded.title == "仅标题"
    assert loaded.author == "Unknown"
    assert loaded.lang == "zh-CN"
