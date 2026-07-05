"""Tests for PDFExtractor (T3)."""

from pathlib import Path

import fitz
import pytest

from pdf2book.config import OCRConfig
from pdf2book.pdf.extractor import PDFExtractor


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Create a 3-page sample PDF for testing."""
    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Chapter {i + 1}", fontsize=24, fontname="helv")
        page.insert_text((72, 160), f"Body text of page {i + 1}.", fontsize=12, fontname="helv")
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


def test_page_count(sample_pdf: Path) -> None:
    ext = PDFExtractor(OCRConfig(dpi=300))
    assert ext.page_count(sample_pdf) == 3


def test_metadata(sample_pdf: Path) -> None:
    ext = PDFExtractor(OCRConfig())
    meta = ext.metadata(sample_pdf)
    assert isinstance(meta, dict)


def test_render_pages(sample_pdf: Path, tmp_path: Path) -> None:
    cfg = OCRConfig(dpi=150)
    ext = PDFExtractor(cfg)
    out_dir = tmp_path / "pages"
    pages = list(ext.render_pages(sample_pdf, out_dir))
    assert len(pages) == 3
    for i, p in enumerate(pages):
        assert p.index == i
        assert p.path.exists()
        assert p.dpi == 150
        assert p.width > 0
        assert p.height > 0


def test_render_pages_generator_releases_memory(sample_pdf: Path, tmp_path: Path) -> None:
    """Generator should yield one page at a time (memory-efficient for big books)."""
    ext = PDFExtractor(OCRConfig(dpi=150))
    out_dir = tmp_path / "pages_gen"
    gen = ext.render_pages(sample_pdf, out_dir)
    first = next(gen)
    assert first.index == 0
    assert first.path.exists()
    # Consume the rest
    rest = list(gen)
    assert len(rest) == 2
