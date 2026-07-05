"""Main conversion pipeline: PDF -> OCR -> postprocess -> markdown -> EPUB.

Orchestrates the stages implemented in T3-T10 in two separable phases:

  Stage 1 — ``run_to_markdown``:
    1. PDF render (PyMuPDF)  — ``PDFExtractor.render_pages``
    2. OCR (PaddlePPBackend)  — per page, with SQLite cache + resume
    3. Post-process           — header/footer -> merge -> title levels -> images
    4. Markdown assembly      — ``structure.to_markdown`` -> ``book.md``
    5. Metadata export        — ``write_meta_yaml`` -> ``meta.md``

  Stage 2 — ``build_epub``:
    6. EPUB build             — ``PandocBuilder`` via pypandoc

The two stages can be invoked independently so the user can preview/edit
``book.md`` before committing to EPUB. ``run`` chains both for one-shot mode.

The OCR stage is the only one that touches the cache: post-processing is
cheap and deterministic, so we re-run it on every resume from the cached
raw PP-Structure JSON.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.progress import track

from pdf2book.config import AppConfig
from pdf2book.epub.builder import EpubBuilder, make_epub_builder
from pdf2book.epub.metadata import BookMetadata, read_meta_yaml, write_meta_yaml
from pdf2book.ocr.base import OCRBackend, make_ocr_backend
from pdf2book.ocr.models import PageResult
from pdf2book.pdf.extractor import PDFExtractor
from pdf2book.postprocess.processor import PostProcessor
from pdf2book.utils.cache import Cache, cfg_hash, pdf_sha1
from pdf2book.utils.logger import get_logger


class ConversionPipeline:
    """Two-stage PDF -> Markdown -> EPUB conversion.

    Stage 1 (``run_to_markdown``): PDF -> OCR -> postprocess -> book.md + meta.md
    Stage 2 (``build_epub``): book.md + meta.md -> .epub via Pandoc

    ``run`` chains both stages for one-shot backward compatibility.
    """

    def __init__(
        self,
        cfg: AppConfig,
        log: logging.Logger | None = None,
        *,
        ocr: OCRBackend | None = None,
        epub: EpubBuilder | None = None,
        cache: Cache | None = None,
    ) -> None:
        self._cfg = cfg
        self._log = log or get_logger()
        self._pdf = PDFExtractor(cfg.ocr)
        # `ocr`/`epub` injection seams for tests; defaults created here.
        self._ocr = ocr or make_ocr_backend(cfg.ocr)
        self._epub = epub or make_epub_builder()
        self._post = PostProcessor(cfg)
        self._cache = cache
        self._owns_cache = cache is None

    # ------------------------------------------------------------------
    # Stage 1: PDF -> Markdown
    # ------------------------------------------------------------------

    def run_to_markdown(
        self,
        pdf_path: Path,
        resume: bool = False,
    ) -> Path:
        """Stage 1: PDF -> OCR -> postprocess -> ``work_dir/book.md``.

        Also writes ``work_dir/meta.md`` with book metadata extracted from
        the PDF, so ``build_epub`` can run later without the original PDF.

        When ``resume`` is True, cached pages (by pdf_sha1 + dpi + cfg_hash)
        are loaded from SQLite and skipped; remaining pages are OCR'd and
        cached. Post-processing always re-runs.

        Returns the path to ``work_dir/book.md``.
        """
        pdf_path = Path(pdf_path)
        self._log.info("OCR stage: %s", pdf_path)

        meta = self._pdf.metadata(pdf_path)
        total = self._pdf.page_count(pdf_path)
        ph = pdf_sha1(pdf_path)
        ch = cfg_hash(self._cfg.ocr)
        dpi = self._cfg.ocr.dpi
        self._log.info(
            "PDF: %d pages, dpi=%d, sha1=%s...", total, dpi, ph[:8]
        )

        cache = self._cache or Cache(self._cfg.cache_db)
        if self._owns_cache:
            cache.open()
        try:
            cache.set_job_state(ph, total)
            done = cache.done_pages(ph, dpi, ch) if resume else set()
            if resume:
                self._log.info("Resume: %d cached pages", len(done))

            page_results = self._ocr_phase(
                pdf_path, ph, ch, dpi, total, done, cache
            )

        finally:
            if self._owns_cache:
                cache.close()

        # Post-processing is cheap + deterministic; always re-run.
        self._log.info("Post-processing %d pages", len(page_results))
        page_results = self._post.run(page_results, meta)

        # Markdown assembly.
        self._cfg.work_dir.mkdir(parents=True, exist_ok=True)
        book_md = self._post.to_markdown(page_results, meta, self._cfg.work_dir)
        self._log.info("Markdown written: %s", book_md)

        # Export metadata so build_epub can run without the original PDF.
        book_meta = BookMetadata.from_pdf_meta(meta, self._cfg.epub)
        meta_path = write_meta_yaml(book_meta, self._cfg.work_dir)
        self._log.info("Metadata written: %s", meta_path)

        return book_md

    # ------------------------------------------------------------------
    # Stage 2: Markdown -> EPUB
    # ------------------------------------------------------------------

    def build_epub(
        self,
        md_path: Path,
        out_path: Path,
        meta_path: Path | None = None,
        cover: Path | None = None,
        css: Path | None = None,
    ) -> Path:
        """Stage 2: Markdown + metadata -> EPUB via Pandoc.

        ``meta_path`` defaults to ``md_path.parent / "meta.md"``. If not
        found, falls back to default ``BookMetadata``. ``css`` defaults to
        ``self._cfg.epub.css_path`` (which itself defaults to the bundled
        ``kindle.css`` in ``PandocBuilder``).
        """
        md_path = Path(md_path)
        out_path = Path(out_path)
        self._log.info("EPUB stage: %s -> %s", md_path, out_path)

        # Load metadata: explicit path > sibling meta.md > defaults.
        if meta_path is not None:
            book_meta = read_meta_yaml(Path(meta_path))
        else:
            sibling_meta = md_path.parent / "meta.md"
            if sibling_meta.exists():
                book_meta = read_meta_yaml(sibling_meta)
            else:
                self._log.warning("No meta.md found; using default metadata")
                book_meta = BookMetadata()

        # toc_depth and chapter_level are EPUB build parameters (not
        # serialized in meta.md); always apply from config so the user can
        # tweak them between the ocr and epub stages without re-running OCR.
        book_meta.toc_depth = self._cfg.epub.toc_depth
        book_meta.chapter_level = self._cfg.epub.chapter_level

        css_path = css if css is not None else self._cfg.epub.css_path
        self._epub.build(md_path, book_meta, out_path, cover=cover, css=css_path)
        self._log.info("EPUB written: %s", out_path)
        return out_path

    # ------------------------------------------------------------------
    # One-shot: both stages
    # ------------------------------------------------------------------

    def run(
        self,
        pdf_path: Path,
        out_path: Path,
        resume: bool = False,
        cover: Path | None = None,
    ) -> Path:
        """One-shot: ``run_to_markdown`` + ``build_epub``. Returns ``out_path``."""
        self._log.info("Starting conversion: %s -> %s", pdf_path, out_path)
        book_md = self.run_to_markdown(pdf_path, resume=resume)
        return self.build_epub(book_md, out_path, cover=cover)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ocr_phase(
        self,
        pdf_path: Path,
        ph: str,
        ch: str,
        dpi: int,
        total: int,
        done: set[int],
        cache: Cache,
    ) -> list[PageResult]:
        """Render + OCR all pages, using cache for resume. Returns PageResults.

        Pages in the skip range (``skip_first_pages`` / ``skip_last_pages``)
        are still rendered to disk (so ``--cover`` can use page 0) but are
        NOT OCR'd or included in the results.
        """
        pages_dir = self._cfg.work_dir / "pages"
        results: list[PageResult] = []

        skip_first = self._cfg.postprocess.skip_first_pages
        skip_last = self._cfg.postprocess.skip_last_pages
        last_ocr_index = total - skip_last if skip_last > 0 else total

        with self._ocr:
            pages_iter = self._pdf.render_pages(pdf_path, pages_dir)
            for pg in track(
                pages_iter, description="OCR", total=total, transient=True
            ):
                # Skip pages outside the OCR range (still rendered above).
                if pg.index < skip_first or pg.index >= last_ocr_index:
                    continue

                if pg.index in done:
                    cached = cache.load(ph, pg.index, dpi, ch)
                    if cached is not None:
                        results.append(self._ocr.from_json(cached, pg.index))
                        continue
                    # Cached page missing from DB (rare); fall through to OCR.
                    self._log.warning(
                        "page %d marked done but not in cache; re-OCR", pg.index
                    )

                pr = self._ocr.recognize(pg.path, pg.index)
                if pr.raw_json is not None:
                    cache.save(ph, pg.index, dpi, ch, pr.raw_json)
                results.append(pr)

        return results


__all__ = ["ConversionPipeline"]
