"""Async conversion manager for the web UI.

Runs PDF→EPUB conversions in a background thread and tracks status via an
in-memory dict. A custom ``WebReporter`` receives pipeline progress events
and updates the status, which the frontend polls via ``GET /api/convert/{stem}/status``.

This module is a web-only extension: it imports the same pipeline/config
code as the CLI but never modifies CLI behavior. The CLI's synchronous
``pdf2book convert`` command is completely unaffected.
"""

from __future__ import annotations

import datetime as _dt
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pdf2book.config import AppConfig, isolate_work_dir
from pdf2book.pipeline import ConversionPipeline
from pdf2book.progress import ProgressReporter


class ConversionStatus:
    """Immutable snapshot of a conversion's current state."""

    def __init__(self) -> None:
        self.status: str = "pending"  # pending | running | completed | failed
        self.stage: str = ""  # ocr | postprocess | markdown | ai_review | epub | done
        self.progress: int = 0  # 0–100 (approximate)
        self.message: str = ""
        self.error: str | None = None
        self.started_at: str = ""
        self.completed_at: str = ""
        self.log_lines: list[str] = []

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "logs": self.log_lines[-50:],  # last 50 lines
        }


class WebReporter:
    """ProgressReporter that updates a ConversionStatus in real time.

    Implements the ProgressReporter protocol so the pipeline can emit
    stage/advance/finish/log events that the web frontend can poll.
    """

    # Rough weight per stage for overall progress estimation (sums to ~100).
    _STAGE_WEIGHTS: dict[str, int] = {
        "ocr": 40,
        "postprocess": 15,
        "markdown": 15,
        "ai_review": 15,
        "epub": 15,
    }
    # Cumulative baseline before each stage starts.
    _STAGE_BASE: dict[str, int] = {
        "ocr": 0,
        "postprocess": 40,
        "markdown": 55,
        "ai_review": 70,
        "epub": 85,
    }

    def __init__(self, status: ConversionStatus, lock: threading.Lock) -> None:
        self._status = status
        self._lock = lock
        self._stage_totals: dict[str, int | None] = {}

    def start(self, stage: str, description: str, total: int | None = None) -> None:
        self._stage_totals[stage] = total
        base = self._STAGE_BASE.get(stage, 0)
        with self._lock:
            self._status.stage = stage
            self._status.message = description
            self._status.progress = base
            self._status.log_lines.append(f"[{stage}] {description}")

    def advance(self, stage: str, n: int = 1, message: str = "") -> None:
        total = self._stage_totals.get(stage)
        base = self._STAGE_BASE.get(stage, 0)
        weight = self._STAGE_WEIGHTS.get(stage, 0)
        if total and total > 0:
            # Estimate within-stage progress
            with self._lock:
                # We don't track absolute completed count, just bump progress
                self._status.progress = min(base + weight, max(self._status.progress, base + 1))
                if message:
                    self._status.message = message
                    self._status.log_lines.append(f"[{stage}] {message}")

    def finish(self, stage: str, message: str = "") -> None:
        base = self._STAGE_BASE.get(stage, 0)
        weight = self._STAGE_WEIGHTS.get(stage, 0)
        with self._lock:
            self._status.progress = base + weight
            if message:
                self._status.message = message
                self._status.log_lines.append(f"[{stage}] done: {message}")
            else:
                self._status.log_lines.append(f"[{stage}] done")

    def log(self, message: str) -> None:
        with self._lock:
            self._status.log_lines.append(message)


class ConvertManager:
    """Manages background conversions with thread-safe status tracking."""

    def __init__(self) -> None:
        self._statuses: dict[str, ConversionStatus] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="convert")

    def get_status(self, stem: str) -> dict | None:
        """Return the current status snapshot for ``stem``, or None."""
        with self._global_lock:
            status = self._statuses.get(stem)
            lock = self._locks.get(stem)
        if status is None:
            return None
        with lock:
            return status.to_dict()

    def is_running(self, stem: str) -> bool:
        """Check if a conversion is currently running for ``stem``."""
        with self._global_lock:
            status = self._statuses.get(stem)
        if status is None:
            return False
        with self._locks.get(stem, threading.Lock()):
            return status.status == "running"

    def start_conversion(
        self,
        cfg: AppConfig,
        stem: str,
        resume: bool = False,
        ai_review: bool = True,
    ) -> dict:
        """Start a background conversion. Returns the initial status.

        Raises ``ValueError`` if the PDF is not found or already running.
        """
        pdf_path = cfg.input_dir / f"{stem}.pdf"
        if not pdf_path.exists():
            raise ValueError(f"PDF '{stem}.pdf' not found in inbox/")

        with self._global_lock:
            if stem in self._statuses:
                with self._locks[stem]:
                    if self._statuses[stem].status == "running":
                        raise ValueError(f"Conversion already running for '{stem}'")

            status = ConversionStatus()
            status.status = "running"
            status.stage = "starting"
            status.progress = 0
            status.message = "Starting conversion..."
            status.started_at = _dt.datetime.now().isoformat()
            self._statuses[stem] = status
            self._locks[stem] = threading.Lock()

        # Submit to thread pool
        self._executor.submit(self._run, cfg, stem, pdf_path, resume, ai_review)
        return status.to_dict()

    def _run(
        self,
        cfg: AppConfig,
        stem: str,
        pdf_path: Path,
        resume: bool,
        ai_review: bool,
    ) -> None:
        """Background worker: runs the conversion pipeline."""
        status = self._statuses[stem]
        lock = self._locks[stem]
        reporter = WebReporter(status, lock)
        log = logging.getLogger("pdf2book.web")

        try:
            # Configure for this conversion
            if not ai_review:
                cfg.ai_review.enabled = False
            isolate_work_dir(cfg, stem)
            cfg.input_dir.mkdir(parents=True, exist_ok=True)
            cfg.output_dir.mkdir(parents=True, exist_ok=True)
            cfg.work_dir.mkdir(parents=True, exist_ok=True)

            output = cfg.output_dir / f"{stem}.epub"
            output.parent.mkdir(parents=True, exist_ok=True)

            with lock:
                status.message = f"Converting {pdf_path.name}..."

            pipeline = ConversionPipeline(cfg, log, reporter=reporter)
            out = pipeline.run(pdf_path, output, resume=resume)

            with lock:
                status.status = "completed"
                status.stage = "done"
                status.progress = 100
                status.message = f"Conversion complete: {out.name}"
                status.completed_at = _dt.datetime.now().isoformat()
                status.log_lines.append(f"Done: {out}")

        except Exception as e:
            log.exception("Conversion failed for %s", stem)
            with lock:
                status.status = "failed"
                status.stage = "error"
                status.error = str(e)
                status.message = f"Error: {e}"
                status.completed_at = _dt.datetime.now().isoformat()
                status.log_lines.append(f"ERROR: {e}")


# Module-level singleton — shared across all requests.
_manager = ConvertManager()


def get_convert_manager() -> ConvertManager:
    """Return the singleton ConvertManager instance."""
    return _manager
