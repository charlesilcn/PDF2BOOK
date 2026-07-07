"""Post-processing orchestrator (T11).

Chains the six post-process stages in the fixed order documented in
`postprocess/__init__.py`:

  0. typography.normalize_punctuation — CJK punctuation cleanup
  1. confidence_filter.filter_by_confidence — drop low-confidence text elements
  2. header_footer.remove   — drop headers/footers + page numbers + running heads
  3. merger.merge_paragraphs — stitch cross-page paragraphs
  4. structure.infer_title_levels — assign H1/H2/H3 from literary rules
  5. images.extract_images   — copy cropped images into work_dir/images/

Then `to_markdown` assembles the final `book.md`. All stages mutate
PageResult/Element in place and return the same list for chaining.
"""

from __future__ import annotations

from pathlib import Path

from pdf2book.config import AppConfig
from pdf2book.ocr.models import PageResult
from pdf2book.postprocess import (
    confidence_filter,
    header_footer,
    images,
    merger,
    structure,
    typography,
)


class PostProcessor:
    """Runs the post-process pipeline and assembles markdown."""

    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg.postprocess
        self._work_dir = cfg.work_dir

    def run(
        self, pages: list[PageResult], meta: dict | None = None
    ) -> list[PageResult]:
        """Execute the six post-process stages in order. Returns `pages`.

        Each stage is gated by its config flag so users can disable it
        (e.g. `merge_cross_page: false`). Confidence filtering is gated by
        `min_confidence > 0.0` (0.0 disables).
        """
        if not pages:
            return pages

        if self._cfg.normalize_punctuation:
            typography.normalize_punctuation(pages, self._cfg)
        if self._cfg.min_confidence > 0.0:
            confidence_filter.filter_by_confidence(pages, self._cfg)
        if self._cfg.drop_header_footer:
            header_footer.remove(pages, self._cfg)
        if self._cfg.merge_cross_page:
            merger.merge_paragraphs(pages, self._cfg)
        if self._cfg.infer_title_level:
            structure.infer_title_levels(pages, self._cfg)
        images.extract_images(pages, self._work_dir)
        return pages

    def to_markdown(
        self,
        pages: list[PageResult],
        meta: dict | None,
        work_dir: Path | None = None,
    ) -> Path:
        """Assemble `book.md` from post-processed pages."""
        return structure.to_markdown(pages, meta, work_dir or self._work_dir)


__all__ = ["PostProcessor"]
