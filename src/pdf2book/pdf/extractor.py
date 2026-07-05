"""PDF rendering layer using PyMuPDF (fitz)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import fitz
from pydantic import BaseModel

from pdf2book.config import OCRConfig


class PageImage(BaseModel):
    """A rendered page image."""

    index: int
    path: Path
    dpi: int
    width: float
    height: float


class PDFExtractor:
    """Extract pages from PDF as images using PyMuPDF.

    Renders each page to PNG at the configured DPI. Uses a generator so
    large books do not hold all page pixmaps in memory at once.
    """

    def __init__(self, cfg: OCRConfig) -> None:
        self._dpi = cfg.dpi

    def metadata(self, pdf: Path) -> dict:
        """Return PDF metadata (title, author, subject, etc.)."""
        doc = fitz.open(str(pdf))
        try:
            return dict(doc.metadata or {})
        finally:
            doc.close()

    def page_count(self, pdf: Path) -> int:
        """Return number of pages."""
        doc = fitz.open(str(pdf))
        try:
            return doc.page_count
        finally:
            doc.close()

    def render_pages(self, pdf: Path, out_dir: Path) -> Iterator[PageImage]:
        """Render each page to PNG. Generator yields one PageImage at a time."""
        out_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(str(pdf))
        try:
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=self._dpi)
                img_path = out_dir / f"page_{i:04d}.png"
                pix.save(str(img_path))
                yield PageImage(
                    index=i,
                    path=img_path,
                    dpi=self._dpi,
                    width=float(pix.width),
                    height=float(pix.height),
                )
                pix = None
        finally:
            doc.close()
