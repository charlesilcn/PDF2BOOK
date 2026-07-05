"""End-to-end integration tests (T12).

Two tiers:
  1. Realistic simulation E2E (always run, fast): an enhanced FakeOCRBackend
     simulates a 5-page book with chapter titles, body text, page numbers
     (bottom margin), running heads (top margin), and a cross-page paragraph
     split. Verifies the full pipeline produces correct markdown and a
     correctly split EPUB.
  2. Real OCR smoke test (marked ``slow``): uses the real PaddlePPBackend on a
     small generated PDF. Skipped unless paddleocr is installed and
     ``-m slow`` is explicitly requested. Downloads model weights on first run.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import fitz
import pytest

from pdf2book.config import AppConfig, OCRConfig
from pdf2book.ocr.base import OCRBackend
from pdf2book.ocr.models import BBox, Element, PageResult
from pdf2book.pipeline import ConversionPipeline

# --- Helpers ---------------------------------------------------------------

PAGE_W = 800.0
PAGE_H = 1000.0


def _el(type_: str, text: str, y1: float, y2: float, order: int) -> Element:
    """Build an element with a full-width bbox at the given vertical position."""
    return Element(
        type=type_,
        text=text,
        bbox=BBox(x1=50, y1=y1, x2=750, y2=y2),
        order_index=order,
    )


def _running_head(text: str, order: int = 0) -> Element:
    """Top-margin running head (cy=40, 40/1000=0.04 < 0.08 top margin)."""
    return _el("text", text, y1=30, y2=50, order=order)


def _page_number(n: int, order: int = 99) -> Element:
    """Bottom-margin page number (cy=960, 960/1000=0.96 > 0.92 bottom)."""
    return _el("text", str(n), y1=950, y2=970, order=order)


def _title(text: str, order: int = 1) -> Element:
    """Chapter title in the body area (not in margin)."""
    return _el("paragraph_title", text, y1=100, y2=160, order=order)


def _body(text: str, order: int = 2) -> Element:
    """Body paragraph in the safe middle zone."""
    return _el("text", text, y1=400, y2=420, order=order)


def _extract_epub_text(epub_path: Path) -> str:
    """Concatenate text from all XHTML/HTML files in an EPUB (tags stripped)."""
    parts: list[str] = []
    with zipfile.ZipFile(epub_path) as zf:
        for name in sorted(zf.namelist()):
            if name.endswith((".xhtml", ".html", ".htm")):
                raw = zf.read(name).decode("utf-8", errors="ignore")
                parts.append(re.sub(r"<[^>]+>", " ", raw))
    return " ".join(parts)


# --- Realistic simulation E2E ----------------------------------------------


class BookFakeOCR(OCRBackend):
    """Deterministic 5-page book simulator for E2E testing.

    Page layout (width=800, height=1000):
      Page 0: running head + "第一章 开端" (H1) + body (complete sentence)
      Page 1: running head + body "故事继续发展，" (no terminator → merge)
      Page 2: running head + body "主人公遇到了挑战。" (merge target)
      Page 3: running head + "第二章 转折" (H1) + body (complete sentence)
      Page 4: running head + body (complete sentence)

    Every page also has a numeric page number at the bottom.

    Expected post-processing:
      - Running head "书名作者" dropped (5 pages ≥ 3, top margin)
      - Page numbers "1".."5" dropped (numeric, bottom margin)
      - Page 1 + Page 2 bodies merged into one paragraph
      - Two H1 chapters → two EPUB XHTML files (ch001, ch002)
    """

    def __init__(self, cfg: OCRConfig) -> None:
        super().__init__(cfg)
        self.recognize_calls = 0

    def initialize(self) -> None:
        pass

    def close(self) -> None:
        pass

    def _page_elements(self, page_index: int) -> list[Element]:
        rh = _running_head("书名作者", order=0)
        pn = _page_number(page_index + 1, order=99)

        if page_index == 0:
            return [
                rh,
                _title("第一章 开端", order=1),
                _body("这是第一章的正文内容，讲述了一个故事的开始。", order=2),
                pn,
            ]
        if page_index == 1:
            return [rh, _body("故事继续发展，", order=2), pn]
        if page_index == 2:
            return [rh, _body("主人公遇到了挑战。", order=2), pn]
        if page_index == 3:
            return [
                rh,
                _title("第二章 转折", order=1),
                _body("新的章节开始了。", order=2),
                pn,
            ]
        if page_index == 4:
            return [rh, _body("故事在这里结束。", order=2), pn]
        return [rh, pn]

    def recognize(self, image: Path, page_index: int) -> PageResult:
        self.recognize_calls += 1
        elements = self._page_elements(page_index)
        payload = {
            "page_index": page_index,
            "elements": [
                {"type": e.type, "text": e.text, "order_index": e.order_index}
                for e in elements
            ],
        }
        return PageResult(
            page_index=page_index,
            width=PAGE_W,
            height=PAGE_H,
            elements=elements,
            raw_json=json.dumps(payload, ensure_ascii=False),
        )

    def from_json(self, page_json: str, page_index: int) -> PageResult:
        data = json.loads(page_json)
        elements = [
            Element(
                type=e["type"],
                text=e["text"],
                bbox=BBox(x1=50, y1=0, x2=750, y2=20),
                order_index=e["order_index"],
            )
            for e in data["elements"]
        ]
        return PageResult(
            page_index=page_index,
            width=PAGE_W,
            height=PAGE_H,
            elements=elements,
            raw_json=page_json,
        )


@pytest.fixture
def book_pdf(tmp_path: Path) -> Path:
    """A 5-page PDF (content is irrelevant — FakeOCR ignores the image)."""
    pdf_path = tmp_path / "book.pdf"
    doc = fitz.open()
    for i in range(5):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Page {i + 1}", fontsize=24, fontname="helv")
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def book_cfg(tmp_path: Path) -> AppConfig:
    work = tmp_path / "work"
    return AppConfig(
        ocr=OCRConfig(dpi=150),
        work_dir=work,
        cache_db=work / "cache.db",
    )


def test_e2e_realistic_book_conversion(
    book_pdf: Path, book_cfg: AppConfig, tmp_path: Path
) -> None:
    """Full pipeline on a simulated 5-page book.

    Verifies:
      - EPUB is valid (mimetype + OPF)
      - 2 chapters split into ch001/ch002 XHTML files
      - Running head "书名作者" removed
      - Page numbers "1".."5" removed
      - Cross-page paragraph merged
      - Body text survives
    """
    fake = BookFakeOCR(book_cfg.ocr)
    pipeline = ConversionPipeline(book_cfg, ocr=fake)
    out = tmp_path / "book.epub"

    pipeline.run(book_pdf, out)

    # EPUB structure.
    assert out.exists() and out.stat().st_size > 0
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "mimetype" in names
    assert any(n.endswith(".opf") for n in names)

    # 2 H1 chapters → 2 split XHTML files.
    chapters = sorted(n for n in names if "ch00" in n and n.endswith(".xhtml"))
    assert len(chapters) == 2, f"Expected 2 chapter files, got: {chapters}"

    # Markdown content.
    md = (book_cfg.work_dir / "book.md").read_text(encoding="utf-8")

    # Chapters present with correct anchors.
    assert "# 第一章 开端 {#ch-1}" in md
    assert "# 第二章 转折 {#ch-2}" in md

    # Running head removed.
    assert "书名作者" not in md

    # Page numbers removed: no line is a bare number.
    assert not re.search(
        r"^\s*\d{1,4}\s*$", md, re.MULTILINE
    ), "Standalone page number found in markdown"

    # Cross-page merge: page 1 + page 2 bodies joined.
    assert "故事继续发展，主人公遇到了挑战。" in md

    # Body text survives.
    assert "这是第一章的正文内容" in md
    assert "新的章节开始了" in md
    assert "故事在这里结束" in md


def test_e2e_epub_text_contains_chapters(
    book_pdf: Path, book_cfg: AppConfig, tmp_path: Path
) -> None:
    """The EPUB's XHTML content should contain chapter titles and merged body."""
    fake = BookFakeOCR(book_cfg.ocr)
    pipeline = ConversionPipeline(book_cfg, ocr=fake)
    out = tmp_path / "book.epub"
    pipeline.run(book_pdf, out)

    text = _extract_epub_text(out)
    assert "第一章" in text
    assert "第二章" in text
    # Running head must not leak into the EPUB.
    assert "书名作者" not in text
    # Merged cross-page paragraph survives into the EPUB.
    assert "故事继续发展" in text
    assert "主人公遇到了挑战" in text


def test_e2e_resume_preserves_output(
    book_pdf: Path, book_cfg: AppConfig, tmp_path: Path
) -> None:
    """Resume after a full run produces equivalent markdown (all cached)."""
    fake = BookFakeOCR(book_cfg.ocr)
    pipeline = ConversionPipeline(book_cfg, ocr=fake)

    pipeline.run(book_pdf, tmp_path / "out1.epub")
    md1 = (book_cfg.work_dir / "book.md").read_text(encoding="utf-8")
    first_calls = fake.recognize_calls
    assert first_calls == 5

    # Second run with resume: all pages cached, recognize not called.
    fake.recognize_calls = 0
    pipeline.run(book_pdf, tmp_path / "out2.epub", resume=True)
    assert fake.recognize_calls == 0
    md2 = (book_cfg.work_dir / "book.md").read_text(encoding="utf-8")
    assert md1 == md2


# --- Real OCR smoke test (slow) --------------------------------------------


@pytest.mark.slow
def test_real_ocr_smoke(tmp_path: Path) -> None:
    """Smoke test with the real PaddlePPBackend on a small generated PDF.

    Requires paddleocr (optional dependency). Skipped otherwise.
    First run downloads model weights (~100 MB) and is very slow.
    Run explicitly::

        pytest -m slow tests/test_e2e.py::test_real_ocr_smoke
    """
    pytest.importorskip("paddleocr")

    from pdf2book.ocr.paddle_pp import PaddlePPBackend

    # Generate a 1-page PDF with clear, large English text (helv font = ASCII).
    pdf_path = tmp_path / "smoke.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 120), "Chapter 1", fontsize=28, fontname="helv")
    page.insert_text(
        (72, 200), "This is the body text for smoke testing.", fontsize=14, fontname="helv"
    )
    doc.save(str(pdf_path))
    doc.close()

    work = tmp_path / "work"
    cfg = AppConfig(
        ocr=OCRConfig(dpi=150),
        work_dir=work,
        cache_db=work / "cache.db",
    )
    pipeline = ConversionPipeline(cfg, ocr=PaddlePPBackend(cfg.ocr))
    out = tmp_path / "smoke.epub"

    pipeline.run(pdf_path, out)

    # EPUB produced and valid.
    assert out.exists() and out.stat().st_size > 0
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "mimetype" in names
    assert any(n.endswith(".opf") for n in names)

    # Non-empty text content (OCR should recognize something).
    text = _extract_epub_text(out)
    assert len(text.strip()) > 0
