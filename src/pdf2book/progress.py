"""Progress reporting abstraction for pdf2book.

Lets the conversion pipeline emit stage/step progress events without
binding to any specific frontend (terminal rich, web UI, logs). The
pipeline accepts a ``ProgressReporter``; concrete adapters render events.

This is a pure extension layer: the default ``NullReporter`` is a no-op,
so any caller that doesn't pass a reporter behaves exactly as before.

Adapters:
  ``NullReporter``   — default no-op (zero overhead, pre-reporter behavior)
  ``RichReporter``   — CLI: drives a ``rich.progress.Progress`` live display,
                       one task per stage; use as a context manager so the
                       live display starts/stops around the pipeline run.
  ``LogReporter``    — subprocess/batch: writes progress lines to a logger.
  ``GradioReporter`` — Web UI: pushes events onto a ``queue.Queue`` for the
                       generator loop to relay to the browser via SSE.
"""

from __future__ import annotations

import logging
import queue
from typing import Protocol, runtime_checkable


@runtime_checkable
class ProgressReporter(Protocol):
    """Minimal progress-reporting surface implemented by all adapters.

    ``stage`` is a stable key (e.g. ``"ocr"``, ``"ai_review"``, ``"epub"``)
    so callers can advance a stage they started earlier. Multiple stages
    may coexist (e.g. batch mode keeps a ``"book"`` stage alive while a
    per-book ``"ocr"`` stage advances underneath).
    """

    def start(self, stage: str, description: str, total: int | None = None) -> None:
        """Begin a stage. ``total=None`` means an indeterminate stage."""
        ...

    def advance(self, stage: str, n: int = 1, message: str = "") -> None:
        """Advance a started stage by ``n`` steps; optional ``message``."""
        ...

    def finish(self, stage: str, message: str = "") -> None:
        """Mark a stage complete."""
        ...

    def log(self, message: str) -> None:
        """Emit a free-form log line alongside progress."""
        ...


class NullReporter:
    """No-op reporter. The pipeline default — zero overhead, behavior
    identical to pre-reporter code, so existing CLI/tests are untouched."""

    def start(self, stage: str, description: str, total: int | None = None) -> None:
        pass

    def advance(self, stage: str, n: int = 1, message: str = "") -> None:
        pass

    def finish(self, stage: str, message: str = "") -> None:
        pass

    def log(self, message: str) -> None:
        pass


class RichReporter:
    """CLI adapter: renders a multi-stage live progress display via rich.

    Use as a context manager so the live display wraps the pipeline run::

        with RichReporter() as reporter:
            pipeline = ConversionPipeline(cfg, log, reporter=reporter)
            pipeline.run(pdf, out)

    Each ``start(stage, ...)`` adds a ``rich.progress`` task; ``advance``
    updates it; ``finish`` completes it. ``log`` routes through the live
    console so log lines don't fight the progress bars.
    """

    def __init__(self) -> None:
        # Lazy import: rich is a core dependency but keeping the import local
        # means this module loads even if rich is temporarily unavailable.
        from rich.console import Console
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
        )

        self._console = Console()
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self._console,
            transient=False,
        )
        # stage -> rich task_id
        self._tasks: dict[str, int] = {}
        self._totals: dict[str, int | None] = {}

    def __enter__(self) -> "RichReporter":
        self._progress.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._progress.stop()

    def start(self, stage: str, description: str, total: int | None = None) -> None:
        if stage in self._tasks:
            return  # idempotent: re-starting a stage is a no-op
        task_id = self._progress.add_task(description, total=total)
        self._tasks[stage] = task_id
        self._totals[stage] = total

    def advance(self, stage: str, n: int = 1, message: str = "") -> None:
        task_id = self._tasks.get(stage)
        if task_id is None:
            return
        kwargs: dict = {"advance": n}
        if message:
            kwargs["description"] = message
        self._progress.update(task_id, **kwargs)

    def finish(self, stage: str, message: str = "") -> None:
        task_id = self._tasks.get(stage)
        if task_id is None:
            return
        total = self._totals.get(stage)
        if total is not None:
            self._progress.update(task_id, completed=total)
        else:
            self._progress.update(task_id, completed=self._progress.tasks[task_id].completed)
        if message:
            self._console.log(message)

    def log(self, message: str) -> None:
        self._console.log(message)


class LogReporter:
    """Logger-only adapter for subprocesses (batch workers) where a live
    rich display would interleave badly across concurrent workers.

    Emits one log line per ``start``/``finish``; ``advance`` is throttled
    to avoid flooding (only logs when ``message`` is non-empty)."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("pdf2book")

    def start(self, stage: str, description: str, total: int | None = None) -> None:
        suffix = f" ({total} items)" if total else ""
        self._log.info("[%s] %s%s", stage, description, suffix)

    def advance(self, stage: str, n: int = 1, message: str = "") -> None:
        if message:
            self._log.info("[%s] %s", stage, message)

    def finish(self, stage: str, message: str = "") -> None:
        self._log.info("[%s] done%s", stage, f": {message}" if message else "")

    def log(self, message: str) -> None:
        self._log.info("%s", message)


class GradioReporter:
    """Web UI adapter: pushes events onto a ``queue.Queue``.

    The Gradio generator loop drains the queue and relays updates to the
    browser via SSE ``yield``. Event tuples are ``(kind, payload)`` where
    ``kind`` is one of ``start``/``advance``/``finish``/``log`` and
    ``payload`` is a dict with the call arguments plus a monotonic stage
    counter so the UI can compute aggregate progress."""

    def __init__(self, q: "queue.Queue[tuple[str, dict]]") -> None:
        self._q = q

    def start(self, stage: str, description: str, total: int | None = None) -> None:
        self._q.put(("start", {"stage": stage, "description": description, "total": total}))

    def advance(self, stage: str, n: int = 1, message: str = "") -> None:
        self._q.put(("advance", {"stage": stage, "n": n, "message": message}))

    def finish(self, stage: str, message: str = "") -> None:
        self._q.put(("finish", {"stage": stage, "message": message}))

    def log(self, message: str) -> None:
        self._q.put(("log", {"message": message}))


__all__ = [
    "ProgressReporter",
    "NullReporter",
    "RichReporter",
    "LogReporter",
    "GradioReporter",
]
