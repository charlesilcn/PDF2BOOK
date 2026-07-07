"""Book metadata model + Pandoc YAML metadata writer (T10)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel

from pdf2book.config import EpubConfig
from pdf2book.epub.templates import default_css_path


class BookMetadata(BaseModel):
    """Metadata embedded into the EPUB via a Pandoc YAML metadata block.

    Fields map to Pandoc's metadata variables (`title`, `author`, `lang`,
    `date`, `rights`). `toc_depth` and `chapter_level` are consumed by
    `PandocBuilder` to set `--toc-depth` and `--epub-chapter-level`.
    """

    title: str = "Untitled"
    author: str = "Unknown"
    lang: str = "zh-CN"
    date: str | None = None
    publisher: str | None = None
    rights: str | None = None
    toc_depth: int = 2
    chapter_level: int = 1

    @classmethod
    def from_pdf_meta(
        cls, pdf_meta: dict, epub_cfg: EpubConfig | None = None
    ) -> BookMetadata:
        """Build metadata from PyMuPDF's `doc.metadata` dict + EpubConfig.

        PyMuPDF keys: `title`, `author`, `subject`, `keywords`, `creator`,
        `producer`, `creationDate`, `modDate`. We only surface title/author;
        the rest are noisy for scanned books.
        """
        cfg = epub_cfg or EpubConfig()
        title = (pdf_meta.get("title") or "").strip() or "Untitled"
        author = (pdf_meta.get("author") or "").strip() or "Unknown"
        return cls(
            title=title,
            author=author,
            lang="zh-CN",
            date=date.today().isoformat(),
            toc_depth=cfg.toc_depth,
            chapter_level=cfg.chapter_level,
        )


def read_meta_yaml(path: Path) -> BookMetadata:
    """Read a Pandoc YAML metadata block written by `write_meta_yaml`.

    Returns a `BookMetadata`. Missing keys (or a missing file) fall back to
    the model defaults. Used by the `epub` subcommand to load metadata
    produced by the `ocr` stage without needing the original PDF.
    """
    p = Path(path)
    if not p.exists():
        return BookMetadata()
    text = p.read_text(encoding="utf-8")
    # The file is `---\n<yaml>\n---\n`; extract the YAML block.
    m = re.match(r"^---\s*\n(.*?)\n---\s*$", text, re.DOTALL)
    if not m:
        return BookMetadata()
    data = yaml.safe_load(m.group(1)) or {}
    return BookMetadata.model_validate(data)


def write_meta_yaml(meta: BookMetadata, work_dir: Path) -> Path:
    """Write a Pandoc-readable YAML metadata block to `work_dir/meta.md`.

    The file is a valid Markdown document containing only a YAML metadata
    block (delimited by `---`). Pandoc merges it with `book.md` when both
    are passed as inputs. Returns the path to the written file.

    We use a `.md` extension (not `.yaml`) so pypandoc infers `markdown`
    format consistently across both inputs.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    meta_path = work_dir / "meta.md"

    payload: dict = {
        "title": meta.title,
        "author": meta.author,
        "lang": meta.lang,
    }
    if meta.date:
        payload["date"] = meta.date
    if meta.publisher:
        payload["publisher"] = meta.publisher
    if meta.rights:
        payload["rights"] = meta.rights

    # `default_flow_style=False` produces block-style YAML (one key per line),
    # which is the most Pandoc-compatible and human-readable form.
    body = yaml.safe_dump(
        payload, allow_unicode=True, default_flow_style=False, sort_keys=False
    ).strip()
    meta_path.write_text(f"---\n{body}\n---\n", encoding="utf-8")
    return meta_path


__all__ = [
    "BookMetadata",
    "read_meta_yaml",
    "write_meta_yaml",
    "default_css_path",
]
