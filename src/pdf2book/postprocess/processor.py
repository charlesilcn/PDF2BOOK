"""Post-processing orchestrator (T11).

Chains the four post-process stages in the fixed order documented in
`postprocess/__init__.py`:

  1. header_footer.remove   — drop headers/footers + page numbers + running heads
  2. merger.merge_paragraphs — stitch cross-page paragraphs
  3. structure.infer_title_levels — assign H1/H2/H3 from literary rules
  4. images.extract_images   — copy cropped images into work_dir/images/

Then `to_markdown` assembles the final `book.md`. All stages mutate
PageResult/Element in place and return the same list for chaining.
"""

from __future__ import annotations

from pathlib import Path

from pdf2book.config import AppConfig
from pdf2book.ocr.models import PageResult
from pdf2book.postprocess import header_footer, images, merger, structure


class PostProcessor:
    """Runs the post-process pipeline and assembles markdown."""

    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg.postprocess
        self._work_dir = cfg.work_dir

    def run(
        self, pages: list[PageResult], meta: dict | None = None
    ) -> list[PageResult]:
        """Execute the four post-process stages in order. Returns `pages`.

        Each stage is gated by its config flag so users can disable it
        (e.g. `merge_cross_page: false`).
        """
        if not pages:
            return pages

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
