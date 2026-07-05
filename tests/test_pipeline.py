"""Tests for the conversion pipeline (T11).

Uses a FakeOCRBackend (no paddleocr dependency) to exercise the full
orchestration: PDF render -> OCR -> cache -> postprocess -> markdown -> EPUB.
Also covers resume behavior and the CLI wiring.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import fitz
import pytest
from typer.testing import CliRunner

from pdf2book.cli import app
from pdf2book.config import AppConfig, OCRConfig
from pdf2book.ocr.base import OCRBackend
from pdf2book.ocr.models import BBox, Element, PageResult
from pdf2book.pipeline import ConversionPipeline


# --- Fake OCR backend -----------------------------------------------------

class FakeOCRBackend(OCRBackend):
    """Deterministic OCR backend for testing. Returns a chapter + body per page.

    `raw_json` is a self-describing JSON that `from_json` can rebuild exactly,
    so resume round-trips losslessly.
    """

    def __init__(self, cfg: OCRConfig) -> None:
        super().__init__(cfg)
        self.recognize_calls = 0

    def initialize(self) -> None:
        pass

    def close(self) -> None:
        pass

    def recognize(self, image: Path, page_index: int) -> PageResult:
        self.recognize_calls += 1
        elements = [
            Element(
                type="paragraph_title",
                text=f"第{page_index + 1}章",
                bbox=BBox(x1=50, y1=40, x2=400, y2=90),
                order_index=0,
            ),
            Element(
                type="text",
                text=f"这是第{page_index + 1}页的正文内容。",
                bbox=BBox(x1=50, y1=120, x2=750, y2=140),
                order_index=1,
            ),
        ]
        payload = {
            "page_index": page_index,
            "elements": [
                {"type": e.type, "text": e.text, "order_index": e.order_index}
                for e in elements
            ],
        }
        return PageResult(
            page_index=page_index,
            width=800.0,
            height=1000.0,
            elements=elements,
            raw_json=json.dumps(payload, ensure_ascii=False),
        )

    def from_json(self, page_json: str, page_index: int) -> PageResult:
        data = json.loads(page_json)
        elements = [
            Element(
                type=e["type"],
                text=e["text"],
                bbox=BBox(x1=0, y1=0, x2=100, y2=20),
                order_index=e["order_index"],
            )
            for e in data["elements"]
        ]
        return PageResult(
            page_index=page_index,
            width=800.0,
            height=1000.0,
            elements=elements,
            raw_json=page_json,
        )


# --- Fixtures --------------------------------------------------------------

@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """A 3-page PDF with chapter titles + body text."""
    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Chapter {i + 1}", fontsize=24, fontname="helv")
        page.insert_text((72, 160), f"Body of page {i + 1}.", fontsize=12, fontname="helv")
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    """AppConfig with work_dir + cache in tmp, low DPI for speed."""
    work = tmp_path / "work"
    return AppConfig(
        ocr=OCRConfig(dpi=150),
        work_dir=work,
        cache_db=work / "cache.db",
    )


def _make_pipeline(cfg: AppConfig, *, ocr: OCRBackend | None = None) -> ConversionPipeline:
    return ConversionPipeline(cfg, ocr=ocr or FakeOCRBackend(cfg.ocr))


# --- End-to-end ------------------------------------------------------------

def test_full_conversion_produces_epub(sample_pdf: Path, cfg: AppConfig, tmp_path: Path) -> None:
    out = tmp_path / "out.epub"
    fake = FakeOCRBackend(cfg.ocr)
    pipeline = _make_pipeline(cfg, ocr=fake)

    result = pipeline.run(sample_pdf, out)

    assert result == out
    assert out.exists()
    assert out.stat().st_size > 0
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "mimetype" in names
    assert any(n.endswith(".opf") for n in names)
    # 3 pages -> 3 chapters -> 3 split xhtml files.
    chapters = sorted(n for n in names if "ch00" in n and n.endswith(".xhtml"))
    assert len(chapters) == 3, names


def test_cache_populated_after_run(sample_pdf: Path, cfg: AppConfig, tmp_path: Path) -> None:
    fake = FakeOCRBackend(cfg.ocr)
    pipeline = _make_pipeline(cfg, ocr=fake)
    pipeline.run(sample_pdf, tmp_path / "out.epub")

    assert fake.recognize_calls == 3
    # book.md written to work_dir.
    assert (cfg.work_dir / "book.md").exists()
    # meta.md written alongside.
    assert (cfg.work_dir / "meta.md").exists()


def test_markdown_contains_chapters_and_body(sample_pdf: Path, cfg: AppConfig, tmp_path: Path) -> None:
    pipeline = _make_pipeline(cfg)
    pipeline.run(sample_pdf, tmp_path / "out.epub")
    md = (cfg.work_dir / "book.md").read_text(encoding="utf-8")
    assert "# 第1章" in md
    assert "# 第2章" in md
    assert "# 第3章" in md
    assert "正文内容" in md


# --- Resume ----------------------------------------------------------------

def test_resume_skips_cached_pages(sample_pdf: Path, cfg: AppConfig, tmp_path: Path) -> None:
    fake = FakeOCRBackend(cfg.ocr)
    pipeline = _make_pipeline(cfg, ocr=fake)

    # First run: OCRs all 3 pages.
    pipeline.run(sample_pdf, tmp_path / "out1.epub")
    first_calls = fake.recognize_calls
    assert first_calls == 3

    # Second run with resume: all pages cached, recognize not called.
    fake.recognize_calls = 0
    pipeline.run(sample_pdf, tmp_path / "out2.epub", resume=True)
    assert fake.recognize_calls == 0


def test_resume_partial_cache_ocrs_missing(sample_pdf: Path, cfg: AppConfig, tmp_path: Path) -> None:
    fake = FakeOCRBackend(cfg.ocr)
    pipeline = _make_pipeline(cfg, ocr=fake)

    # First run.
    pipeline.run(sample_pdf, tmp_path / "out1.epub")
    assert fake.recognize_calls == 3

    # Simulate page 1 missing from cache by deleting its row via the Cache API.
    from pdf2book.utils.cache import Cache, cfg_hash, pdf_sha1
    ph = pdf_sha1(sample_pdf)
    ch = cfg_hash(cfg.ocr)
    with Cache(cfg.cache_db) as c:
        c._require().execute(
            "DELETE FROM page_cache WHERE page_index=?", (1,)
        )
        c._require().commit()

    # Resume: page 0 and 2 cached, page 1 re-OCR'd.
    fake.recognize_calls = 0
    pipeline.run(sample_pdf, tmp_path / "out2.epub", resume=True)
    assert fake.recognize_calls == 1  # only page 1


def test_no_resume_re_ocrs_all(sample_pdf: Path, cfg: AppConfig, tmp_path: Path) -> None:
    fake = FakeOCRBackend(cfg.ocr)
    pipeline = _make_pipeline(cfg, ocr=fake)

    pipeline.run(sample_pdf, tmp_path / "out1.epub")
    fake.recognize_calls = 0
    # Without resume, cache is ignored → all re-OCR'd.
    pipeline.run(sample_pdf, tmp_path / "out2.epub", resume=False)
    assert fake.recognize_calls == 3


# --- Postprocess integration ----------------------------------------------

def test_header_footer_dropped_in_pipeline(sample_pdf: Path, cfg: AppConfig, tmp_path: Path) -> None:
    """A 'header'-typed element should be dropped and absent from markdown."""
    class HeaderFakeOCR(FakeOCRBackend):
        def recognize(self, image: Path, page_index: int) -> PageResult:
            self.recognize_calls += 1
            elements = [
                Element(type="header", text="RUNNING HEAD", bbox=BBox(x1=0, y1=0, x2=100, y2=20), order_index=0),
                Element(type="text", text=f"正文{page_index}.", bbox=BBox(x1=0, y1=400, x2=100, y2=420), order_index=1),
            ]
            payload = {"page_index": page_index, "elements": [
                {"type": e.type, "text": e.text, "order_index": e.order_index} for e in elements
            ]}
            return PageResult(page_index=page_index, width=800, height=1000,
                              elements=elements, raw_json=json.dumps(payload, ensure_ascii=False))

    pipeline = _make_pipeline(cfg, ocr=HeaderFakeOCR(cfg.ocr))
    pipeline.run(sample_pdf, tmp_path / "out.epub")
    md = (cfg.work_dir / "book.md").read_text(encoding="utf-8")
    assert "RUNNING HEAD" not in md
    assert "正文" in md


# --- CLI -------------------------------------------------------------------

def test_cli_convert_invokes_pipeline(sample_pdf: Path, cfg: AppConfig, tmp_path: Path, monkeypatch) -> None:
    """The CLI should wire config + pipeline and produce an EPUB.

    Monkeypatches `make_ocr_backend` so the CLI doesn't require paddleocr.
    """
    # Force the CLI's AppConfig to use tmp work_dir by writing a config file.
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"work_dir: {cfg.work_dir}\n"
        f"cache_db: {cfg.cache_db}\n"
        f"ocr:\n  dpi: 150\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.epub"

    monkeypatch.setattr(
        "pdf2book.pipeline.make_ocr_backend",
        lambda ocr_cfg: FakeOCRBackend(ocr_cfg),
    )

    runner = CliRunner()
    result = runner.invoke(app, [
        "convert", str(sample_pdf),
        "-o", str(out),
        "--config", str(cfg_path),
    ])
    assert result.exit_code == 0, result.output
    assert out.exists()


def test_cli_help_lists_convert() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "convert" in result.output
    assert "ocr" in result.output
    assert "epub" in result.output


def test_cli_resume_flag_accepted(sample_pdf: Path, cfg: AppConfig, tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"work_dir: {cfg.work_dir}\n"
        f"cache_db: {cfg.cache_db}\n"
        f"ocr:\n  dpi: 150\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.epub"
    monkeypatch.setattr(
        "pdf2book.pipeline.make_ocr_backend",
        lambda ocr_cfg: FakeOCRBackend(ocr_cfg),
    )
    runner = CliRunner()
    result = runner.invoke(app, [
        "convert", str(sample_pdf),
        "-o", str(out),
        "--config", str(cfg_path),
        "--resume",
    ])
    assert result.exit_code == 0, result.output
    assert out.exists()


# --- Two-stage: run_to_markdown + build_epub -------------------------------


def test_run_to_markdown_writes_book_and_meta(sample_pdf: Path, cfg: AppConfig, tmp_path: Path) -> None:
    """Stage 1 should produce both book.md and meta.md in work_dir."""
    pipeline = _make_pipeline(cfg)
    book_md = pipeline.run_to_markdown(sample_pdf)

    assert book_md == cfg.work_dir / "book.md"
    assert book_md.exists()
    meta_md = cfg.work_dir / "meta.md"
    assert meta_md.exists()
    # meta.md contains a YAML block with title/author.
    meta_text = meta_md.read_text(encoding="utf-8")
    assert "title:" in meta_text
    assert "author:" in meta_text


def test_build_epub_from_markdown_and_meta(sample_pdf: Path, cfg: AppConfig, tmp_path: Path) -> None:
    """Stage 2 should build an EPUB from book.md + meta.md (no PDF needed)."""
    pipeline = _make_pipeline(cfg)
    book_md = pipeline.run_to_markdown(sample_pdf)

    out = tmp_path / "two_stage.epub"
    result = pipeline.build_epub(book_md, out)

    assert result == out
    assert out.exists() and out.stat().st_size > 0
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "mimetype" in names
    assert any(n.endswith(".opf") for n in names)


def test_two_stage_produces_valid_epub_chapters(sample_pdf: Path, cfg: AppConfig, tmp_path: Path) -> None:
    """Two-stage flow should still split chapters correctly."""
    pipeline = _make_pipeline(cfg)
    book_md = pipeline.run_to_markdown(sample_pdf)
    out = tmp_path / "out.epub"
    pipeline.build_epub(book_md, out)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    chapters = sorted(n for n in names if "ch00" in n and n.endswith(".xhtml"))
    assert len(chapters) == 3  # FakeOCRBackend: 3 pages → 3 chapters


def test_build_epub_with_explicit_meta_path(sample_pdf: Path, cfg: AppConfig, tmp_path: Path) -> None:
    """build_epub should accept an explicit --meta path."""
    pipeline = _make_pipeline(cfg)
    pipeline.run_to_markdown(sample_pdf)

    # Write a custom meta file.
    custom_meta = tmp_path / "custom_meta.md"
    custom_meta.write_text(
        "---\ntitle: 自定义标题\nauthor: 自定义作者\nlang: zh-CN\n---\n",
        encoding="utf-8",
    )

    out = tmp_path / "out.epub"
    pipeline.build_epub(cfg.work_dir / "book.md", out, meta_path=custom_meta)

    # Verify the custom title made it into the EPUB's OPF.
    with zipfile.ZipFile(out) as zf:
        opf = [n for n in zf.namelist() if n.endswith(".opf")][0]
        opf_text = zf.read(opf).decode("utf-8")
    assert "自定义标题" in opf_text


def test_build_epub_no_meta_uses_defaults(sample_pdf: Path, cfg: AppConfig, tmp_path: Path) -> None:
    """No meta.md → default metadata (title=Untitled), not an error."""
    pipeline = _make_pipeline(cfg)
    # Create a standalone markdown without meta.md.
    standalone_md = tmp_path / "standalone.md"
    standalone_md.write_text("# Chapter\n\nBody text.\n", encoding="utf-8")

    out = tmp_path / "out.epub"
    pipeline.build_epub(standalone_md, out)
    assert out.exists() and out.stat().st_size > 0


# --- CLI: ocr and epub subcommands ------------------------------------------


def test_cli_ocr_subcommand(sample_pdf: Path, cfg: AppConfig, tmp_path: Path, monkeypatch) -> None:
    """`pdf2book ocr` should generate book.md + meta.md without building EPUB."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"work_dir: {cfg.work_dir}\n"
        f"cache_db: {cfg.cache_db}\n"
        f"ocr:\n  dpi: 150\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "pdf2book.pipeline.make_ocr_backend",
        lambda ocr_cfg: FakeOCRBackend(ocr_cfg),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["ocr", str(sample_pdf), "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert (cfg.work_dir / "book.md").exists()
    assert (cfg.work_dir / "meta.md").exists()
    # No EPUB should be produced.
    assert not any(tmp_path.glob("*.epub"))


def test_cli_epub_subcommand(sample_pdf: Path, cfg: AppConfig, tmp_path: Path, monkeypatch) -> None:
    """`pdf2book epub` should build EPUB from existing book.md + meta.md."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"work_dir: {cfg.work_dir}\n"
        f"cache_db: {cfg.cache_db}\n"
        f"ocr:\n  dpi: 150\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "pdf2book.pipeline.make_ocr_backend",
        lambda ocr_cfg: FakeOCRBackend(ocr_cfg),
    )
    runner = CliRunner()

    # Stage 1: ocr.
    result = runner.invoke(app, ["ocr", str(sample_pdf), "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output

    # Stage 2: epub.
    out = tmp_path / "out.epub"
    book_md = str(cfg.work_dir / "book.md")
    result = runner.invoke(app, [
        "epub", book_md,
        "-o", str(out),
        "--config", str(cfg_path),
    ])
    assert result.exit_code == 0, result.output
    assert out.exists() and out.stat().st_size > 0


def test_cli_epub_with_meta_flag(sample_pdf: Path, cfg: AppConfig, tmp_path: Path, monkeypatch) -> None:
    """`pdf2book epub --meta` should override the default meta.md."""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"work_dir: {cfg.work_dir}\n"
        f"cache_db: {cfg.cache_db}\n"
        f"ocr:\n  dpi: 150\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "pdf2book.pipeline.make_ocr_backend",
        lambda ocr_cfg: FakeOCRBackend(ocr_cfg),
    )
    runner = CliRunner()
    runner.invoke(app, ["ocr", str(sample_pdf), "--config", str(cfg_path)])

    custom_meta = tmp_path / "custom.md"
    custom_meta.write_text(
        "---\ntitle: CLI自定义标题\nauthor: CLI作者\nlang: zh-CN\n---\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.epub"
    result = runner.invoke(app, [
        "epub", str(cfg.work_dir / "book.md"),
        "-o", str(out),
        "--meta", str(custom_meta),
        "--config", str(cfg_path),
    ])
    assert result.exit_code == 0, result.output
    with zipfile.ZipFile(out) as zf:
        opf = [n for n in zf.namelist() if n.endswith(".opf")][0]
        opf_text = zf.read(opf).decode("utf-8")
    assert "CLI自定义标题" in opf_text
