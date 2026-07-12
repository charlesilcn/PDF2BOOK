"""Convert tab: PDF -> EPUB conversion with live progress.

Runs ``ConversionPipeline`` in-process (serial, single book) so
``GradioReporter`` events flow back to the browser via a queue+generator
bridge. The pipeline runs in a background thread; the Gradio generator
drains the queue and yields ``(progress_text, output_file)`` tuples via SSE.

Pure helpers (``list_inbox_pdfs``, ``process_event``, ``format_progress_text``)
are unit-tested without gradio. The gradio UI builder and generator are thin
wrappers that import gradio lazily.
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

from pdf2book.config import AppConfig, isolate_work_dir
from pdf2book.pipeline import ConversionPipeline
from pdf2book.progress import GradioReporter


# --- Pure helpers (testable without gradio) --------------------------------

# Stage display order for consistent progress layout.
_STAGE_ORDER = ["ocr", "classify", "postprocess", "markdown", "ai_review", "epub"]


def list_inbox_pdfs(cfg: AppConfig) -> list[Path]:
    """Return sorted PDFs in the inbox directory (may be empty)."""
    if not cfg.input_dir.exists():
        return []
    return sorted(p for p in cfg.input_dir.rglob("*.pdf") if p.is_file())


def process_event(
    event: tuple[str, dict],
    stage_stats: dict[str, dict],
) -> str:
    """Process one GradioReporter event, mutate ``stage_stats``, return text.

    ``stage_stats`` maps stage key -> ``{description, total, completed, done}``.
    Called by the generator loop for each event dequeued from the reporter.
    """
    kind, payload = event
    stage = payload.get("stage", "")

    if kind == "start":
        stage_stats[stage] = {
            "description": payload.get("description", stage),
            "total": payload.get("total"),
            "completed": 0,
            "done": False,
        }
    elif kind == "advance":
        if stage in stage_stats:
            stage_stats[stage]["completed"] += payload.get("n", 1)
    elif kind == "finish":
        if stage in stage_stats:
            stage_stats[stage]["done"] = True
            total = stage_stats[stage]["total"]
            if total is not None:
                stage_stats[stage]["completed"] = total
    # "log" events don't update stage stats

    return format_progress_text(stage_stats)


def format_progress_text(stage_stats: dict[str, dict]) -> str:
    """Format current progress as a multi-line string for the UI textbox.

    Stages are ordered by ``_STAGE_ORDER`` so the display is stable. Unknown
    stages (not in the order list) appear at the end alphabetically.
    """
    if not stage_stats:
        return "等待开始..."

    def sort_key(stage: str) -> tuple[int, str]:
        try:
            return (0, str(_STAGE_ORDER.index(stage)))
        except ValueError:
            return (1, stage)

    lines: list[str] = []
    for stage in sorted(stage_stats, key=sort_key):
        stats = stage_stats[stage]
        mark = "✅" if stats["done"] else "⏳"
        total = stats["total"]
        completed = stats["completed"]
        desc = stats["description"]
        if total is not None:
            lines.append(f"{mark} {desc}: {completed}/{total}")
        else:
            lines.append(f"{mark} {desc}: {completed} 项")
    return "\n".join(lines)


# --- Thread bridge (testable: uses stdlib queue/threading, no gradio) ------


def _run_conversion(
    cfg: AppConfig,
    pdf_path: Path,
    output_path: Path,
    resume: bool,
    cover: Path | None,
    q: "queue.Queue[tuple[str, dict]]",
    log: logging.Logger | None = None,
) -> None:
    """Thread target: runs the pipeline with GradioReporter, pushes to queue.

    Catches all exceptions and pushes an ``("error", ...)`` event so the
    generator can surface the message to the user instead of silently dying.
    """
    try:
        reporter = GradioReporter(q)
        pipeline = ConversionPipeline(cfg, log or logging.getLogger("pdf2book"), reporter=reporter)
        result = pipeline.run(pdf_path, output_path, resume=resume, cover=cover)
        q.put(("done", {"path": str(result)}))
    except Exception as exc:  # noqa: BLE001 - surface to UI
        q.put(("error", {"message": f"{type(exc).__name__}: {exc}"}))


def conversion_generator(
    cfg: AppConfig,
    pdf_path: Path,
    output_path: Path,
    resume: bool,
    cover: Path | None = None,
    log: logging.Logger | None = None,
):
    """Generator that yields ``(progress_text, output_path_or_none)`` tuples.

    Starts the pipeline in a background thread, then drains the GradioReporter
    queue and yields after each event. Terminates on ``done`` or ``error``.

    Used as the Gradio ``fn`` for the convert button: ``demo.queue()`` relays
    each yield to the browser via SSE.
    """
    q: "queue.Queue[tuple[str, dict]]" = queue.Queue()
    thread = threading.Thread(
        target=_run_conversion,
        args=(cfg, pdf_path, output_path, resume, cover, q, log),
        daemon=True,
    )
    thread.start()

    stage_stats: dict[str, dict] = {}
    output_file: str | None = None

    while thread.is_alive() or not q.empty():
        try:
            event = q.get(timeout=0.15)
        except queue.Empty:
            continue

        kind, payload = event
        if kind == "done":
            output_file = payload["path"]
            yield format_progress_text(stage_stats) + "\n\n✅ 转换完成！", output_file
            break
        if kind == "error":
            yield (
                format_progress_text(stage_stats)
                + f"\n\n❌ 错误: {payload['message']}",
                None,
            )
            break
        text = process_event(event, stage_stats)
        yield text, output_file

    thread.join(timeout=2.0)


# --- Gradio UI builder (needs gradio, called only when GUI launches) -------


def build_convert_tab(cfg: AppConfig, config_path: Path | None = None):  # type: ignore[no-untyped-def]
    """Build the convert tab UI.

    Returns a dict of Gradio component references. Imports gradio lazily.
    Callback wiring is self-contained: the convert button triggers the
    ``conversion_generator`` which yields live progress + the output EPUB.
    """
    import gradio as gr

    inbox_pdfs = list_inbox_pdfs(cfg)
    inbox_choices = [str(p) for p in inbox_pdfs]

    with gr.Tab("转换") as tab:
        gr.Markdown("## PDF → EPUB 转换")

        with gr.Row():
            pdf_upload = gr.File(
                label="上传 PDF 文件",
                file_types=[".pdf"],
            )
            inbox_dropdown = gr.Dropdown(
                label="或从 inbox/ 选择",
                choices=inbox_choices,
                interactive=True,
            )

        with gr.Row():
            resume_chk = gr.Checkbox(label="断点续传（跳过已 OCR 的页）", value=False)
            cover_input = gr.File(
                label="封面图片（可选）",
                file_types=[".png", ".jpg", ".jpeg"],
            )

        convert_btn = gr.Button("🚀 开始转换", variant="primary")
        progress_text = gr.Textbox(
            label="转换进度",
            lines=8,
            interactive=False,
            placeholder="点击「开始转换」后此处显示实时进度...",
        )
        output_file = gr.File(label="输出 EPUB")

    def _resolve_pdf(upload, inbox_sel) -> Path | None:
        """Determine the PDF path from upload or inbox selection."""
        if upload is not None:
            return Path(upload)
        if inbox_sel:
            return Path(inbox_sel)
        return None

    def _on_convert(
        upload, inbox_sel, resume, cover, progress=gr.Progress(track_tqdm=False)
    ):
        """Generator callback: yields (progress_text, output_file) via SSE."""
        import logging as _logging

        pdf_path = _resolve_pdf(upload, inbox_sel)
        if pdf_path is None or not Path(pdf_path).exists():
            yield "❌ 请先选择或上传一个 PDF 文件", None
            return

        # Deep-copy cfg so isolate_work_dir doesn't mutate the shared instance.
        run_cfg = cfg.model_copy(deep=True)
        isolate_work_dir(run_cfg, Path(pdf_path).stem)
        run_cfg.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = run_cfg.output_dir / f"{Path(pdf_path).stem.rstrip()}.epub"

        cover_path = Path(cover) if cover else None
        log = _logging.getLogger("pdf2book.gui")

        yield from conversion_generator(
            run_cfg, Path(pdf_path), output_path, resume, cover_path, log
        )

    convert_btn.click(
        fn=_on_convert,
        inputs=[pdf_upload, inbox_dropdown, resume_chk, cover_input],
        outputs=[progress_text, output_file],
    )

    return {
        "tab": tab,
        "pdf_upload": pdf_upload,
        "inbox_dropdown": inbox_dropdown,
        "convert_btn": convert_btn,
        "progress_text": progress_text,
        "output_file": output_file,
    }


__all__ = [
    "build_convert_tab",
    "conversion_generator",
    "format_progress_text",
    "list_inbox_pdfs",
    "process_event",
]
