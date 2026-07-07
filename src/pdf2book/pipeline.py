"""Main conversion pipeline: PDF -> OCR -> postprocess -> review -> markdown -> EPUB.

Orchestrates the stages implemented in T3-T10 plus the Phase 5-6 AI review
loop in two separable phases:

  Stage 1 — ``run_to_markdown``:
    1. PDF render (PyMuPDF)  — ``PDFExtractor.render_pages``
    2. OCR (PaddlePPBackend)  — per page, with SQLite cache + resume
    3. Post-process           — header/footer -> merge -> title levels -> images
    4. CIP extraction         — rule-based metadata from copyright page
    5. Page classification    — cover/copyright/toc/body/... via heuristics
    6. AI review (optional)   — collect -> constrain -> prompt -> retry -> apply
    7. Markdown assembly      — ``structure.to_markdown`` -> ``book.md``
    8. Metadata export        — ``write_meta_yaml`` -> ``meta.md``

  Stage 2 — ``build_epub``:
    9. EPUB build             — ``PandocBuilder`` via pypandoc

The two stages can be invoked independently so the user can preview/edit
``book.md`` before committing to EPUB. ``run`` chains both for one-shot mode.

The OCR stage is the only one that touches the cache: post-processing is
cheap and deterministic, so we re-run it on every resume from the cached
raw PP-Structure JSON.

AI review (step 6) is gated by ``cfg.ai_review.enabled``. When disabled,
low-confidence texts keep their ``>[low-confidence]`` markers in book.md
for manual proofreading, and metadata falls back to CIP extraction (or
PDF embedded metadata when CIP also fails).
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
from pdf2book.postprocess.cip_extractor import extract_metadata as extract_cip_meta
from pdf2book.postprocess.page_classifier import DECORATIVE_TYPES, PageType, classify_pages
from pdf2book.postprocess.processor import PostProcessor
from pdf2book.postprocess.structure import infer_title_levels
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
        # Cover page image path detected during `_extract_metadata_and_classify`
        # (the page classified as PageType.COVER). Used by `build_epub` when
        # no explicit `--cover` is provided, so the EPUB cover matches the
        # actual cover page rather than a hardcoded `page_0000.png`.
        self._detected_cover: Path | None = None

    # ------------------------------------------------------------------
    # Stage 1: PDF -> Markdown
    # ------------------------------------------------------------------

    def run_to_markdown(
        self,
        pdf_path: Path,
        resume: bool = False,
    ) -> Path:
        """Stage 1: PDF -> OCR -> postprocess -> review -> ``work_dir/book.md``.

        Also writes ``work_dir/meta.md`` with book metadata (CIP-extracted,
        AI-corrected when enabled, or PDF-fallback), so ``build_epub`` can
        run later without the original PDF.

        When ``resume`` is True, cached pages (by pdf_sha1 + dpi + cfg_hash)
        are loaded from SQLite and skipped; remaining pages are OCR'd and
        cached. Post-processing, CIP extraction, page classification, and
        AI review always re-run (they're cheap/non-network except for AI).

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

        # CIP metadata extraction + page classification (Phase 6).
        # Always runs — these are rule-based and feed into AI review.
        book_meta = self._extract_metadata_and_classify(page_results, meta)

        # AI review stage (Phase 6). No-op when cfg.ai_review.enabled=False.
        book_meta = self._ai_review_stage(page_results, book_meta)

        # Markdown assembly (uses AI-corrected texts via Element.ai_corrected).
        self._cfg.work_dir.mkdir(parents=True, exist_ok=True)
        book_md = self._post.to_markdown(page_results, meta, self._cfg.work_dir)
        self._log.info("Markdown written: %s", book_md)

        # Export metadata so build_epub can run without the original PDF.
        meta_path = write_meta_yaml(book_meta, self._cfg.work_dir)
        self._log.info("Metadata written: %s", meta_path)

        return book_md

    # ------------------------------------------------------------------
    # Phase 6: CIP extraction + page classification + AI review
    # ------------------------------------------------------------------

    def _extract_metadata_and_classify(
        self,
        pages: list[PageResult],
        pdf_meta: dict | None,
    ) -> BookMetadata:
        """Run CIP metadata extraction and page classification.

        Always runs (even when AI review is disabled) because:
          1. CIP extraction is rule-based and feeds metadata into meta.md
             even without AI.
          2. Page classification drives decorative-vs-content page split
             in the EPUB builder (Phase 7 will use page_type to decide
             whether to embed the PDF original image or use Pandoc-rendered
             XHTML).

        Falls back to PDF embedded metadata when CIP extraction fails.
        """
        # CIP extraction (rule-based, from copyright page OCR text).
        cip_meta = extract_cip_meta(pages)
        if cip_meta is not None:
            self._log.info(
                "CIP metadata extracted: title=%r, author=%r",
                cip_meta.title, cip_meta.author,
            )
            # Apply EPUB config defaults (toc_depth, chapter_level).
            cip_meta.toc_depth = self._cfg.epub.toc_depth
            cip_meta.chapter_level = self._cfg.epub.chapter_level
        else:
            self._log.info("CIP extraction failed; falling back to PDF metadata")
            cip_meta = BookMetadata.from_pdf_meta(pdf_meta or {}, self._cfg.epub)

        # Page classification (uses metadata to identify cover/frontispiece).
        classify_pages(pages, cip_meta)
        type_counts: dict[str, int] = {}
        for p in pages:
            type_counts[p.page_type] = type_counts.get(p.page_type, 0) + 1
        self._log.info("Page classification: %s", type_counts)

        # Store the cover page image path for `build_epub` to use as
        # --epub-cover-image. Falls back to page_0000.png in `build_epub`
        # when no cover page was classified (e.g. all-body PDFs).
        self._detected_cover = None
        for p in pages:
            if p.page_type == PageType.COVER.value and p.page_image_path:
                self._detected_cover = Path(p.page_image_path)
                self._log.info("Cover page detected: %s", self._detected_cover)
                break

        # Re-run title level inference with page-type awareness: skip
        # decorative + TOC pages whose OCR "titles" (e.g. a misread "目录"
        # → "水*CONTENTS") would pollute the H1 hierarchy. The first call
        # (in PostProcessor.run) runs before classification, so all title
        # elements were inferred including noise from decorative/TOC pages.
        # This second call overwrites `inferred_level` with clean results.
        if self._cfg.postprocess.infer_title_level:
            skip_types = DECORATIVE_TYPES | {PageType.TOC}
            infer_title_levels(pages, self._cfg.postprocess, skip_page_types=skip_types)

        return cip_meta

    def _ai_review_stage(
        self,
        pages: list[PageResult],
        rule_meta: BookMetadata,
    ) -> BookMetadata:
        """Run the AI review loop: collect -> review -> apply.

        No-op when ``cfg.ai_review.enabled=False``. Returns the (possibly
        AI-updated) BookMetadata. On AI failure (network error, etc.),
        logs a warning and returns the rule metadata unchanged — the
        pipeline still produces book.md with ``>[low-confidence]`` markers.
        """
        cfg = self._cfg.ai_review
        if not cfg.enabled:
            return rule_meta

        # Lazy import to avoid loading review module when disabled.
        from pdf2book.review import (
            AIClient,
            apply_review_results,
            collect_review_items,
        )

        self._log.info("AI review stage (model=%s)", cfg.model)
        review_items = collect_review_items(pages, rule_meta)
        self._log.info(
            "Review items: %d low-conf, %d titles, %d page-types, meta_candidates=%d",
            len(review_items["low_confidence_texts"]),
            len(review_items["title_candidates"]),
            len(review_items["page_type_candidates"]),
            len(review_items["metadata"]["candidates"]),
        )

        client = AIClient(cfg)
        try:
            review_result = client.review_all(review_items)
        except Exception as exc:
            self._log.warning(
                "AI review failed (%s); using rule-based results", exc
            )
            return rule_meta
        finally:
            client.close()

        self._log.info(
            "AI review done: %d corrections, %d titles, %d page-types, meta=%s",
            len(review_result.low_confidence),
            len(review_result.titles),
            len(review_result.page_types),
            "updated" if review_result.metadata else "unchanged",
        )

        _, final_meta = apply_review_results(pages, rule_meta, review_result)
        return final_meta

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

        # Auto-detect cover image when not explicitly provided. Prefer the
        # cover page detected during `_extract_metadata_and_classify` (the
        # page classified as PageType.COVER); fall back to `page_0000.png`
        # when no cover page was classified or when running `build_epub`
        # standalone (no prior `run_to_markdown`).
        # Resolve to absolute path because Pandoc runs with `cworkdir` set to
        # the markdown's parent, so a relative path won't resolve correctly.
        if cover is None:
            if self._detected_cover and self._detected_cover.exists():
                cover = self._detected_cover.resolve()
                self._log.info("Using detected cover page: %s", cover)
            else:
                auto_cover = md_path.parent / "pages" / "page_0000.png"
                if auto_cover.exists():
                    cover = auto_cover.resolve()
                    self._log.info("Fallback cover (page_0000): %s", cover)

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
                        pr = self._ocr.from_json(cached, pg.index)
                        pr.page_image_path = pg.path
                        results.append(pr)
                        continue
                    # Cached page missing from DB (rare); fall through to OCR.
                    self._log.warning(
                        "page %d marked done but not in cache; re-OCR", pg.index
                    )

                pr = self._ocr.recognize(pg.path, pg.index)
                pr.page_image_path = pg.path
                if pr.raw_json is not None:
                    cache.save(ph, pg.index, dpi, ch, pr.raw_json)
                results.append(pr)

        return results


__all__ = ["ConversionPipeline"]
