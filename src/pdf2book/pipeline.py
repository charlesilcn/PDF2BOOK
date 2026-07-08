"""Main conversion pipeline: PDF -> OCR -> postprocess -> markdown -> AI review -> EPUB.

Orchestrates the stages in two separable phases:

  Stage 1 — ``run_to_markdown``:
    1. PDF render (PyMuPDF)  — ``PDFExtractor.render_pages``
    2. OCR (PaddlePPBackend)  — per page, with SQLite cache + resume
    3. Post-process           — header/footer -> merge -> title levels -> images
    4. CIP extraction         — rule-based metadata from copyright page
    5. Page classification    — cover/copyright/toc/body/... via heuristics
    6. Markdown assembly      — ``structure.to_markdown`` -> ``book.md``
    7. Metadata export        — ``write_meta_yaml`` -> ``meta.md``
    8. AI review (optional)   — review ``book.md`` + ``meta.md`` and fix issues
                                (low-confidence text, title issues, metadata)

  Stage 2 — ``build_epub``:
    9. EPUB build             — ``PandocBuilder`` via pypandoc

The two stages can be invoked independently so the user can preview/edit
``book.md`` before committing to EPUB. ``run`` chains both for one-shot mode.

The OCR stage is the only one that touches the cache: post-processing is
cheap and deterministic, so we re-run it on every resume from the cached
raw PP-Structure JSON.

AI review (step 8) is gated by ``cfg.ai_review.enabled``. It runs AFTER
markdown generation so the AI sees the actual structure that will become
the EPUB. When disabled, low-confidence texts keep their
``>[low-confidence]`` markers in book.md for manual proofreading, and
metadata falls back to CIP extraction (or PDF embedded metadata when CIP
also fails).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from rich.progress import track

from pdf2book.config import AppConfig
from pdf2book.epub.builder import EpubBuilder, make_epub_builder
from pdf2book.epub.metadata import (
    BookMetadata,
    BookStructure,
    BookStructureEntry,
    read_meta_yaml,
    write_meta_yaml,
)
from pdf2book.epub.toc_links import linkify_toc_entries
from pdf2book.ocr.base import OCRBackend, make_ocr_backend
from pdf2book.ocr.models import PageResult
from pdf2book.pdf.extractor import PDFExtractor
from pdf2book.postprocess.cip_extractor import extract_metadata as extract_cip_meta
from pdf2book.postprocess.page_classifier import DECORATIVE_TYPES, PageType, classify_pages
from pdf2book.postprocess.processor import PostProcessor
from pdf2book.postprocess.structure import infer_title_levels
from pdf2book.utils.cache import Cache, cfg_hash, pdf_sha1
from pdf2book.utils.logger import get_logger

# Each entry is (pattern, replacement). Patterns are applied in order.
_AI_MARKER_PATTERNS = [
    # Low-confidence blockquote prefix: ">[low-confidence] {text}" -> "{text}"
    # Strip prefix, keep the original OCR text.
    (re.compile(r"^\s*>\[low-confidence\]\s*(.*)$", re.MULTILINE), r"\1"),
    # Trailing [UNCLEAR] marker (AI couldn't determine correction) -> remove
    (re.compile(r"\s*\[UNCLEAR\]\s*$", re.MULTILINE), ""),
    # Trailing [需校对] marker (AI correction failed validation) -> remove
    (re.compile(r"\s*\[需校对\]\s*$", re.MULTILINE), ""),
    # Page boundary markers (multimodal review): <!-- page: N --> -> remove
    (re.compile(r"^<!--\s*page:\s*\d+\s*-->\s*$", re.MULTILINE), ""),
]

# Detects unresolved low-confidence markers in book.md. Used by `build_epub`
# to decide whether to supplement AI review when the OCR stage ran without it.
# Idempotent: after AI review (or convert one-shot path) the markers are gone.
_LOW_CONF_MARKER_RE = re.compile(r"^\s*>\[low-confidence\]", re.MULTILINE)


def _strip_ai_markers(md_text: str) -> str:
    """Remove AI review markers from markdown text.

    Strips:
      - ``>[low-confidence]`` blockquote prefix (keeps original OCR text)
      - Trailing ``[UNCLEAR]`` markers
      - Trailing ``[需校对]`` markers
      - ``<!-- page: N -->`` page boundary markers (multimodal review)

    This ensures the final EPUB contains only readable text, not
    intermediate review artifacts. Called before passing book.md to Pandoc.
    """
    for pattern, replacement in _AI_MARKER_PATTERNS:
        md_text = pattern.sub(replacement, md_text)
    return md_text


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
        self._log.info("PDF: %d pages, dpi=%d, sha1=%s...", total, dpi, ph[:8])

        cache = self._cache or Cache(self._cfg.cache_db)
        if self._owns_cache:
            cache.open()
        try:
            cache.set_job_state(ph, total)
            done = cache.done_pages(ph, dpi, ch) if resume else set()
            if resume:
                self._log.info("Resume: %d cached pages", len(done))

            page_results = self._ocr_phase(pdf_path, ph, ch, dpi, total, done, cache)

        finally:
            if self._owns_cache:
                cache.close()

        # Post-processing is cheap + deterministic; always re-run.
        self._log.info("Post-processing %d pages", len(page_results))
        page_results = self._post.run(page_results, meta)

        # CIP metadata extraction + page classification (Phase 6).
        # Always runs — these are rule-based and feed into AI review.
        book_meta = self._extract_metadata_and_classify(page_results, meta)

        # Markdown assembly (with low-confidence markers + title issues).
        # AI review runs AFTER this stage so it sees the actual structure.
        # When multimodal review is enabled, emit page boundary markers so
        # the review stage can map issues back to source page images.
        self._cfg.work_dir.mkdir(parents=True, exist_ok=True)
        emit_markers = (
            self._cfg.ai_review.enabled and self._cfg.ai_review.multimodal
        )
        book_md = self._post.to_markdown(
            page_results, meta, self._cfg.work_dir, emit_page_markers=emit_markers
        )
        self._log.info("Markdown written: %s", book_md)

        # Export metadata so build_epub can run without the original PDF.
        meta_path = write_meta_yaml(book_meta, self._cfg.work_dir)
        self._log.info("Metadata written: %s", meta_path)

        # AI review stage: read book.md + meta.md, fix issues, write back.
        # No-op when cfg.ai_review.enabled=False. On failure, book.md keeps
        # its low-confidence markers for manual proofreading.
        book_meta = self._ai_markdown_review_stage(book_md, meta_path, book_meta)

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
                cip_meta.title,
                cip_meta.author,
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

        # Build book_structure from classification results for AI verification.
        cip_meta.book_structure = self._build_book_structure(pages)

        return cip_meta

    def _build_book_structure(self, pages: list[PageResult]) -> BookStructure:
        """Build a ``BookStructure`` summary from page classification results.

        Records each page's type and render mode (image vs text), the
        detected ordering of distinct page types, missing expected types,
        and structural anomalies (e.g. copyright page appearing after the
        body instead of in the front matter).
        """
        expected_order = [
            "cover",
            "frontispiece",
            "copyright",
            "preface",
            "toc",
            "body",
            "back_cover",
        ]
        detected_pages: list[BookStructureEntry] = []
        detected_order: list[str] = []
        for p in sorted(pages, key=lambda x: x.page_index):
            page_type = p.page_type
            rendered_as = "image" if page_type in DECORATIVE_TYPES else "text"
            detected_pages.append(
                BookStructureEntry(
                    page_index=p.page_index,
                    page_type=page_type,
                    rendered_as=rendered_as,
                )
            )
            if page_type != "unknown" and page_type not in detected_order:
                detected_order.append(page_type)

        missing = [t for t in expected_order if t not in detected_order]
        anomalies = self._detect_structure_anomalies(detected_order)

        return BookStructure(
            order=detected_order,
            pages=detected_pages,
            missing=missing,
            anomalies=anomalies,
        )

    @staticmethod
    def _detect_structure_anomalies(order: list[str]) -> list[str]:
        """Detect structural anomalies like a copyright page at the end."""
        anomalies: list[str] = []
        if "copyright" in order and "body" in order:
            if order.index("copyright") > order.index("body"):
                anomalies.append("copyright_at_end")
        if "back_cover" in order and "body" in order:
            if order.index("back_cover") < order.index("body"):
                anomalies.append("back_cover_before_body")
        return anomalies

    def _ai_markdown_review_stage(
        self,
        md_path: Path,
        meta_path: Path,
        rule_meta: BookMetadata,
    ) -> BookMetadata:
        """Run AI review on the generated ``book.md`` and ``meta.md``.

        No-op when ``cfg.ai_review.enabled=False``. Returns the (possibly
        AI-updated) BookMetadata. On AI failure (network error, etc.),
        logs a warning and returns the rule metadata unchanged — the
        pipeline still produces book.md with ``>[low-confidence]`` markers.
        """
        cfg = self._cfg.ai_review
        if not cfg.enabled:
            return rule_meta

        # Lazy import to avoid loading review module when disabled.
        from pdf2book.review import AIClient

        self._log.info("AI markdown review stage (model=%s)", cfg.model)
        client = AIClient(cfg, work_dir=md_path.parent)
        try:
            updated_meta = client.review_markdown(md_path, meta_path, rule_meta)
        except Exception as exc:
            self._log.warning("AI markdown review failed (%s); using rule-based results", exc)
            return rule_meta
        finally:
            client.close()

        self._log.info("AI markdown review done")
        return updated_meta

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
        # Keep the resolved meta path so the supplemental AI review stage
        # (below) can pass it to `_ai_markdown_review_stage`.
        if meta_path is not None:
            meta_path_resolved: Path | None = Path(meta_path)
            book_meta = read_meta_yaml(meta_path_resolved)
        else:
            sibling_meta = md_path.parent / "meta.md"
            if sibling_meta.exists():
                meta_path_resolved = sibling_meta
                book_meta = read_meta_yaml(sibling_meta)
            else:
                meta_path_resolved = None
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

        # Strip AI review markers before EPUB build. Markers like
        # ``>[low-confidence]``, ``[UNCLEAR]``, ``[需校对]`` are intermediate
        # review artifacts that must not leak into the final EPUB. We write a
        # cleaned copy to a temp file (book.epub.md) so the original book.md
        # retains the markers for debugging/manual review.
        original_text = md_path.read_text(encoding="utf-8")
        # Supplemental AI review: when enabled and book.md still carries
        # ``>[low-confidence]`` markers (i.e. the OCR stage ran without AI
        # review, or AI review failed and fell back), run it now so the EPUB
        # benefits from corrections. Idempotent — if the OCR stage already
        # cleaned the markers (convert one-shot path, or prior epub run),
        # the regex finds nothing and this block is skipped.
        if self._cfg.ai_review.enabled and _LOW_CONF_MARKER_RE.search(original_text):
            self._log.info("build_epub: 检测到低置信度标记，补做 AI review")
            book_meta = self._ai_markdown_review_stage(md_path, meta_path_resolved, book_meta)
            original_text = md_path.read_text(encoding="utf-8")
        cleaned_text = _strip_ai_markers(original_text)
        # TOC linkification fallback: convert "标题／页码" paragraphs into a
        # clickable vertical list. Idempotent — skips when AI review already
        # produced a `::: {.toc-list}` block or no TOC region is found.
        cleaned_text = linkify_toc_entries(cleaned_text)
        if cleaned_text != original_text:
            cleaned_md = md_path.with_suffix(".epub.md")
            cleaned_md.write_text(cleaned_text, encoding="utf-8")
            self._log.info("Stripped AI markers + linkified TOC; using cleaned markdown for EPUB")
            build_md = cleaned_md
        else:
            build_md = md_path

        self._epub.build(build_md, book_meta, out_path, cover=cover, css=css_path)
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
            for pg in track(pages_iter, description="OCR", total=total, transient=True):
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
                    self._log.warning("page %d marked done but not in cache; re-OCR", pg.index)

                pr = self._ocr.recognize(pg.path, pg.index)
                pr.page_image_path = pg.path
                if pr.raw_json is not None:
                    cache.save(ph, pg.index, dpi, ch, pr.raw_json)
                results.append(pr)

        return results


__all__ = ["ConversionPipeline"]
