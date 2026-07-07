"""Comprehensive end-to-end test for PDF2BOOK.

Runs the full pipeline on a real PDF and records timing, results, and issues
for each stage. Outputs a structured JSON test report.

Usage:
    python scripts/e2e_test.py <pdf_path> [--config CONFIG] [--output OUT] [--report REPORT]

If --config is omitted, uses default AppConfig (no AI review).
If --output is omitted, writes to <work_dir>/output.epub.
If --report is omitted, writes to <work_dir>/test_report.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from pdf2book.config import AppConfig
from pdf2book.pipeline import ConversionPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end test for PDF2BOOK pipeline."
    )
    parser.add_argument("pdf", type=Path, help="Input PDF path")
    parser.add_argument(
        "--config", type=Path, default=None, help="Config YAML path (optional)"
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="Output EPUB path"
    )
    parser.add_argument(
        "--report", type=Path, default=None, help="Report JSON output path"
    )
    args = parser.parse_args()

    pdf_path: Path = args.pdf.resolve()
    if not pdf_path.exists():
        print(f"Error: PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    cfg = AppConfig.load(args.config) if args.config else AppConfig.default()
    work_dir = Path(cfg.work_dir)
    output_epub = args.output or (work_dir / "output.epub")
    report_path = args.report or (work_dir / "test_report.json")

    report: dict = {
        "test_name": "PDF2BOOK End-to-End Test",
        "test_date": datetime.now().isoformat(),
        "pdf_file": str(pdf_path),
        "config_file": str(args.config) if args.config else "default",
        "environment": {
            "python_version": sys.version,
            "platform": sys.platform,
        },
        "stages": [],
        "results": {},
        "issues": [],
        "summary": {},
    }

    import fitz

    doc = fitz.open(pdf_path)
    report["pdf_info"] = {
        "page_count": doc.page_count,
        "metadata": doc.metadata,
        "file_size_mb": round(pdf_path.stat().st_size / 1024 / 1024, 2),
    }
    doc.close()

    pipeline = ConversionPipeline(cfg)

    print("\n" + "=" * 70)
    print("STAGE 1: run_to_markdown (OCR → Postprocess → CIP → Classify → MD → AI)")
    print("=" * 70)
    t0 = time.time()
    try:
        book_md = pipeline.run_to_markdown(pdf_path)
        t1 = time.time()
        elapsed = round(t1 - t0, 2)
        report["stages"].append({
            "name": "run_to_markdown",
            "status": "passed",
            "elapsed_seconds": elapsed,
        })
        print(f"  PASSED in {elapsed}s -> {book_md}")
    except Exception as exc:
        t1 = time.time()
        elapsed = round(t1 - t0, 2)
        report["stages"].append({
            "name": "run_to_markdown",
            "status": "failed",
            "elapsed_seconds": elapsed,
            "error": str(exc),
        })
        report["issues"].append({
            "stage": "run_to_markdown",
            "error": str(exc),
            "type": type(exc).__name__,
        })
        print(f"  FAILED in {elapsed}s: {exc}")
        _save_report(report, report_path)
        sys.exit(1)

    book_content = book_md.read_text(encoding="utf-8")
    book_lines = book_content.splitlines()
    report["results"]["book_md"] = {
        "path": str(book_md),
        "line_count": len(book_lines),
        "char_count": len(book_content),
        "size_kb": round(len(book_content.encode("utf-8")) / 1024, 2),
    }

    h1_lines = [line for line in book_lines if line.startswith("# ")]
    report["results"]["book_md"]["h1_count"] = len(h1_lines)

    lc_lines = [
        line for line in book_lines if line.startswith(">[low-confidence]")
    ]
    report["results"]["book_md"]["low_confidence_remaining"] = len(lc_lines)

    unclear_count = book_content.count("[UNCLEAR]")
    need_review_count = book_content.count("[需校对]")
    report["results"]["book_md"]["unclear_markers"] = unclear_count
    report["results"]["book_md"]["need_review_markers"] = need_review_count

    garbled_chars = set("©®™□○◇●〼")
    garbled_titles = [
        line for line in h1_lines if any(c in garbled_chars for c in line)
    ]
    report["results"]["book_md"]["garbled_titles"] = len(garbled_titles)

    split_titles = 0
    for i, line in enumerate(book_lines):
        if line.startswith("# ") and i + 1 < len(book_lines):
            next_line = book_lines[i + 1].strip()
            is_heading = next_line.startswith("#")
            is_div = next_line.startswith(":::")
            is_image = next_line.startswith("![")
            is_terminator = next_line.endswith(
                ("。", "！", "？", ";", "；", ".")
            )
            if next_line and not is_heading and not is_div and not is_image:
                if len(next_line) <= 30 and not is_terminator:
                    split_titles += 1
    report["results"]["book_md"]["split_titles"] = split_titles

    meta_path = book_md.parent / "meta.md"
    if meta_path.exists():
        meta_content = meta_path.read_text(encoding="utf-8")
        report["results"]["meta_md"] = {
            "path": str(meta_path),
            "content": meta_content,
            "has_book_structure": "book_structure" in meta_content,
        }
    else:
        report["issues"].append({
            "stage": "run_to_markdown",
            "error": "meta.md not found",
        })

    print("\n" + "=" * 70)
    print("STAGE 2: build_epub (Markdown -> EPUB via Pandoc)")
    print("=" * 70)
    t0 = time.time()
    try:
        pipeline.build_epub(book_md, output_epub)
        t1 = time.time()
        elapsed = round(t1 - t0, 2)
        report["stages"].append({
            "name": "build_epub",
            "status": "passed",
            "elapsed_seconds": elapsed,
        })
        print(f"  PASSED in {elapsed}s -> {output_epub}")
    except Exception as exc:
        t1 = time.time()
        elapsed = round(t1 - t0, 2)
        report["stages"].append({
            "name": "build_epub",
            "status": "failed",
            "elapsed_seconds": elapsed,
            "error": str(exc),
        })
        report["issues"].append({
            "stage": "build_epub",
            "error": str(exc),
            "type": type(exc).__name__,
        })
        print(f"  FAILED in {elapsed}s: {exc}")
        _save_report(report, report_path)
        sys.exit(1)

    if output_epub.exists():
        epub_size = output_epub.stat().st_size
        report["results"]["epub"] = {
            "path": str(output_epub),
            "size_mb": round(epub_size / 1024 / 1024, 2),
        }

        import zipfile

        with zipfile.ZipFile(output_epub, "r") as zf:
            names = zf.namelist()
            xhtml_files = [n for n in names if n.endswith((".xhtml", ".html"))]
            image_files = [n for n in names if n.endswith((".png", ".jpg", ".jpeg"))]
            css_files = [n for n in names if n.endswith(".css")]
            report["results"]["epub"]["xhtml_count"] = len(xhtml_files)
            report["results"]["epub"]["image_count"] = len(image_files)
            report["results"]["epub"]["css_count"] = len(css_files)

            opf_files = [n for n in names if n.endswith(".opf")]
            if opf_files:
                opf_content = zf.read(opf_files[0]).decode("utf-8")
                report["results"]["epub"]["has_title"] = "<dc:title" in opf_content
                report["results"]["epub"]["has_author"] = "<dc:creator" in opf_content
                report["results"]["epub"]["has_publisher"] = "<dc:publisher" in opf_content

            full_text = ""
            for xhtml in xhtml_files:
                try:
                    content = zf.read(xhtml).decode("utf-8", errors="replace")
                    import re
                    text = re.sub(r"<[^>]+>", " ", content)
                    full_text += text + "\n"
                except Exception:
                    pass

            report["results"]["epub"]["text_length"] = len(full_text)
            report["results"]["epub"]["has_low_confidence_marker"] = "[low-confidence]" in full_text
            report["results"]["epub"]["has_unclear_marker"] = "[UNCLEAR]" in full_text

    passed = sum(1 for s in report["stages"] if s["status"] == "passed")
    failed = sum(1 for s in report["stages"] if s["status"] == "failed")
    total_time = sum(s["elapsed_seconds"] for s in report["stages"])
    report["summary"] = {
        "total_stages": len(report["stages"]),
        "passed": passed,
        "failed": failed,
        "total_time_seconds": round(total_time, 2),
        "issue_count": len(report["issues"]),
        "overall_status": "PASSED" if failed == 0 else "FAILED",
    }

    _save_report(report, report_path)

    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"  Overall: {report['summary']['overall_status']}")
    print(f"  Stages: {passed} passed, {failed} failed")
    print(f"  Total time: {total_time}s")
    print(f"  Issues: {len(report['issues'])}")
    print(f"  Report: {report_path}")
    print()


def _save_report(report: dict, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()
