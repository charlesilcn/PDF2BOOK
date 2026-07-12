"""Batch PDF conversion (Phase 4).

Converts a directory of PDFs to EPUBs in parallel using a process pool.
Each PDF gets an isolated ``work_dir/{stem}/`` subdirectory and its own
SQLite cache so concurrent workers never contend on the same files.

The worker function ``convert_single_pdf`` is module-level so it pickles
cleanly for ``ProcessPoolExecutor``. It rebuilds ``AppConfig`` from a plain
dict (pydantic models don't pickle across processes reliably), adjusts
``work_dir`` / ``cache_db`` to per-PDF subpaths, then runs the standard
``ConversionPipeline``.

``BatchProcessor.run`` catches exceptions per-PDF so one failure doesn't
abort the whole batch; it returns the list of successfully generated EPUB
paths and logs failures at WARNING level.
"""

from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from rich.progress import track  # noqa: F401  (kept for backward-compat imports)

from pdf2book.config import AppConfig, isolate_work_dir
from pdf2book.pipeline import ConversionPipeline
from pdf2book.progress import NullReporter, ProgressReporter
from pdf2book.utils.logger import setup_logger


def convert_single_pdf(
    pdf_str: str,
    cfg_dict: dict,
    out_str: str,
    resume: bool,
) -> str:
    """Worker entry point: runs in a subprocess, converts one PDF to EPUB.

    Returns the output EPUB path on success. Raises on failure (caller
    ``BatchProcessor.run`` catches and logs).

    ``cfg_dict`` is a plain dict (``AppConfig.model_dump()`` output) so it
    pickles cleanly across process boundaries. We rebuild ``AppConfig``
    inside the worker, then isolate ``work_dir`` and ``cache_db`` under a
    per-PDF subdirectory to avoid concurrent-write contention.
    """
    cfg = AppConfig.model_validate(cfg_dict)
    pdf_path = Path(pdf_str)
    out_path = Path(out_str)
    stem = pdf_path.stem

    # Isolate work_dir + cache_db per PDF (avoid cross-PDF file contention
    # when running with workers > 1).
    isolate_work_dir(cfg, stem)

    log = setup_logger("INFO")
    pipeline = ConversionPipeline(cfg, log)
    pipeline.run(pdf_path, out_path, resume=resume)
    return str(out_path)


class BatchProcessor:
    """Converts a list of PDFs to EPUBs in parallel."""

    def __init__(
        self,
        cfg: AppConfig,
        max_workers: int,
        log: logging.Logger | None = None,
    ) -> None:
        self._cfg = cfg
        self._max_workers = max(1, max_workers)
        self._log = log or setup_logger("INFO")

    def run(
        self,
        pdf_paths: list[Path],
        output_dir: Path,
        resume: bool = False,
        reporter: ProgressReporter | None = None,
    ) -> list[Path]:
        """Convert all ``pdf_paths`` to EPUBs in ``output_dir``.

        Returns the list of successfully generated EPUB paths. Failures are
        logged at WARNING level and don't abort the batch.

        ``reporter`` is optional: when None, a ``NullReporter`` is used (no
        progress output) — this preserves backward compatibility for existing
        callers/tests. When a reporter is supplied (e.g. ``RichReporter`` from
        the CLI batch command), it drives a book-level progress bar (one tick
        per completed PDF). Subprocess workers always use the default
        ``NullReporter`` (rich/gradio objects are not picklable across process
        boundaries), so fine-grained per-page progress is only visible in
        single-book runs.
        """
        r = reporter or NullReporter()
        if not pdf_paths:
            self._log.warning("batch: no PDFs to convert")
            return []

        output_dir.mkdir(parents=True, exist_ok=True)
        cfg_dict = self._cfg.model_dump(mode="json")

        # Build (pdf, output) pairs so the worker function is self-contained.
        jobs = [
            (str(pdf), str(output_dir / f"{pdf.stem.rstrip()}.epub"))
            for pdf in pdf_paths
        ]

        succeeded: list[Path] = []
        r.start("book", "批量转换", len(jobs))
        # Use ProcessPoolExecutor even when max_workers=1 to keep the code
        # path identical (and to avoid loading OCR models in the parent).
        with ProcessPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {
                pool.submit(convert_single_pdf, pdf_str, cfg_dict, out_str, resume): (
                    Path(pdf_str),
                    Path(out_str),
                )
                for pdf_str, out_str in jobs
            }
            for future in as_completed(futures):
                pdf_path, out_path = futures[future]
                try:
                    future.result()
                    succeeded.append(out_path)
                    self._log.info("batch: OK %s -> %s", pdf_path.name, out_path.name)
                except Exception as e:  # noqa: BLE001 - log + continue
                    self._log.warning(
                        "batch: FAIL %s (%s: %s)", pdf_path.name, type(e).__name__, e
                    )
                r.advance("book", message=pdf_path.name)
        r.finish("book")
        return succeeded


__all__ = ["BatchProcessor", "convert_single_pdf"]
