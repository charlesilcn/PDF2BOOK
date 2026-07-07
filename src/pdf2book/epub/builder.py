"""EPUB builder abstraction + Pandoc-backed implementation (T10)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import pypandoc

from pdf2book.epub.metadata import BookMetadata, write_meta_yaml
from pdf2book.epub.templates import default_css_path
from pdf2book.utils.logger import get_logger

_log = get_logger()


class EpubBuilder(ABC):
    """Abstract EPUB builder: markdown + metadata -> .epub."""

    @abstractmethod
    def build(
        self,
        markdown: Path,
        meta: BookMetadata,
        out: Path,
        cover: Path | None = None,
        css: Path | None = None,
    ) -> Path:
        """Render `markdown` into `out` (.epub). Returns `out`.

        `cover` (optional image) and `css` (optional stylesheet) override
        any defaults the implementation may pick.
        """
        ...


class PandocBuilder(EpubBuilder):
    """Default EPUB builder backed by Pandoc (via pypandoc_binary).

    Pipeline:
      1. `write_meta_yaml` emits `meta.md` (a YAML metadata block) next to
         `book.md`.
      2. Pandoc reads `[meta.md, book.md]` as markdown inputs (meta first
         so its YAML block becomes document metadata), with the working
         directory set to `book.md`'s parent so relative image paths
         (`images/pN_eM.png`) resolve.
      3. `--split-level={meta.chapter_level}` splits the EPUB into one XHTML
         file per H1 (the Kindle page-break mechanism).
      4. Falls back to the bundled `kindle.css` when no `css` is given.
    """

    def build(
        self,
        markdown: Path,
        meta: BookMetadata,
        out: Path,
        cover: Path | None = None,
        css: Path | None = None,
    ) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        work_dir = markdown.parent

        meta_md = write_meta_yaml(meta, work_dir)
        css_path = css if css is not None else default_css_path()

        # sort_files=False so meta.md stays before book.md. Without this
        # pypandoc alphabetizes inputs and book.md would precede meta.md,
        # putting the YAML metadata block after the body (Pandoc still
        # parses it, but meta-first is the idiomatic, robust order).
        # Resolve to absolute paths: pypandoc._identify_path rejects relative.
        inputs = [str(meta_md.resolve()), str(markdown.resolve())]

        args: list[str] = [
            "--standalone",
            "--toc",
            f"--toc-depth={meta.toc_depth}",
            # `--split-level` replaces the deprecated `--epub-chapter-level`
            # in pandoc >= 3.x; same semantics (split into one XHTML file per
            # header at this level). This is the Kindle page-break mechanism.
            f"--split-level={meta.chapter_level}",
            f"--css={css_path}",
        ]
        if cover is not None:
            args.append(f"--epub-cover-image={cover}")

        _log.info(
            "Pandoc: %s + %s -> %s (toc-depth=%d, chapter-level=%d)",
            meta_md.name,
            markdown.name,
            out.name,
            meta.toc_depth,
            meta.chapter_level,
        )
        pypandoc.convert_file(
            inputs,
            "epub",
            format="markdown",
            outputfile=str(out),
            extra_args=args,
            sort_files=False,
            cworkdir=str(work_dir.resolve()),
        )
        return out


def make_epub_builder() -> EpubBuilder:
    """Factory: return the default Pandoc-backed builder."""
    return PandocBuilder()


__all__ = ["EpubBuilder", "PandocBuilder", "make_epub_builder"]
