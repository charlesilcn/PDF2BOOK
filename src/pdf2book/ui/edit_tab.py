"""Edit tab: inspect/edit stage-1 Markdown (book.md + meta.md) before EPUB build.

Maps to the two-stage pipeline: ``run_to_markdown`` (stage 1) produces
``workspace/{stem}/book.md`` + ``meta.md``; the user previews/edits them here,
then clicks "构建 EPUB" to run ``build_epub`` (stage 2).

Pure helpers (``list_workspace_books``, ``load_book_md``, ``save_book_md``,
etc.) are unit-tested without gradio. The gradio UI builder is a thin wrapper.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pdf2book.config import AppConfig


# --- Pure helpers (testable without gradio) --------------------------------


def list_workspace_books(cfg: AppConfig) -> list[str]:
    """Return sorted book stems that have a ``book.md`` in ``workspace/{stem}/``.

    Each entry is the stem (directory name), suitable for a dropdown label.
    """
    ws = cfg.work_dir
    if not ws.exists():
        return []
    books: list[str] = []
    for child in sorted(ws.iterdir()):
        if child.is_dir() and (child / "book.md").exists():
            books.append(child.name)
    return books


def load_book_md(work_dir: Path) -> str:
    """Read ``book.md`` from ``work_dir``. Returns empty string if missing."""
    path = work_dir / "book.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def load_meta_md(work_dir: Path) -> str:
    """Read ``meta.md`` from ``work_dir``. Returns empty string if missing."""
    path = work_dir / "meta.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_book_md(work_dir: Path, content: str) -> None:
    """Write ``content`` back to ``book.md`` in ``work_dir``."""
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "book.md").write_text(content, encoding="utf-8")


def save_meta_md(work_dir: Path, content: str) -> None:
    """Write ``content`` back to ``meta.md`` in ``work_dir``."""
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "meta.md").write_text(content, encoding="utf-8")


def work_dir_for_book(cfg: AppConfig, book_stem: str) -> Path:
    """Return ``workspace/{book_stem}/`` for a given book stem."""
    return cfg.work_dir / book_stem


# --- Gradio UI builder (needs gradio, called only when GUI launches) -------


def build_edit_tab(cfg: AppConfig, config_path: Path | None = None):  # type: ignore[no-untyped-def]
    """Build the edit tab UI.

    Layout:
      1. Book selector dropdown (from workspace/)
      2. Load button → fills the editors
      3. Side-by-side: book.md editor (left) + meta.md editor (right)
      4. Preview pane (renders book.md as Markdown)
      5. Save button + Build EPUB button

    Returns a dict of Gradio component references.
    """
    import gradio as gr

    book_choices = list_workspace_books(cfg)

    with gr.Tab("编辑") as tab:
        gr.Markdown("## Markdown 预览与编辑")

        with gr.Row():
            book_dropdown = gr.Dropdown(
                label="选择书目（workspace/）",
                choices=book_choices,
                interactive=True,
            )
            load_btn = gr.Button("📂 加载")
            refresh_btn = gr.Button("🔄 刷新列表")

        with gr.Row():
            book_editor = gr.Textbox(
                label="book.md",
                lines=20,
                interactive=True,
                placeholder="选择书目后点击「加载」...",
            )
            meta_editor = gr.Textbox(
                label="meta.md",
                lines=20,
                interactive=True,
                placeholder="元数据 YAML...",
            )

        with gr.Row():
            preview_btn = gr.Button("👁 刷新预览")
            save_btn = gr.Button("💾 保存修改", variant="primary")
            build_btn = gr.Button("📖 构建 EPUB", variant="secondary")

        preview_md = gr.Markdown("", label="Markdown 预览")
        status_md = gr.Markdown("")
        build_output = gr.File(label="构建产物", visible=False)

    def _on_load(book_stem: str) -> tuple[str, str, str]:
        if not book_stem:
            return "", "", "❌ 请先选择一个书目"
        wd = work_dir_for_book(cfg, book_stem)
        if not (wd / "book.md").exists():
            return "", "", f"❌ 未找到 {wd}/book.md"
        return load_book_md(wd), load_meta_md(wd), f"✅ 已加载 {book_stem}"

    load_btn.click(
        fn=_on_load,
        inputs=book_dropdown,
        outputs=[book_editor, meta_editor, status_md],
    )

    def _on_refresh_list() -> gr.Dropdown:  # type: ignore[valid-type]
        return gr.Dropdown(choices=list_workspace_books(cfg))

    refresh_btn.click(fn=_on_refresh_list, outputs=book_dropdown)

    def _on_preview(book_content: str) -> str:
        return book_content if book_content else "*（无内容）*"

    preview_btn.click(fn=_on_preview, inputs=book_editor, outputs=preview_md)

    def _on_save(book_stem: str, book_content: str, meta_content: str) -> str:
        if not book_stem:
            return "❌ 请先选择一个书目"
        wd = work_dir_for_book(cfg, book_stem)
        save_book_md(wd, book_content)
        if meta_content:
            save_meta_md(wd, meta_content)
        return f"✅ 已保存到 {wd}/"

    save_btn.click(
        fn=_on_save,
        inputs=[book_dropdown, book_editor, meta_editor],
        outputs=status_md,
    )

    def _on_build(book_stem: str, book_content: str):
        """Build EPUB from the (possibly edited) book.md, yield progress."""
        if not book_stem:
            yield "❌ 请先选择一个书目", None
            return
        wd = work_dir_for_book(cfg, book_stem)
        # Save latest edits before building
        if book_content:
            save_book_md(wd, book_content)
        book_md = wd / "book.md"
        if not book_md.exists():
            yield "❌ book.md 不存在", None
            return

        run_cfg = cfg.model_copy(deep=True)
        output_path = run_cfg.output_dir / f"{book_stem}.epub"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            from pdf2book.pipeline import ConversionPipeline

            pipeline = ConversionPipeline(
                run_cfg, logging.getLogger("pdf2book.gui")
            )
            result = pipeline.build_epub(book_md, output_path)
            yield f"✅ EPUB 已构建: {result}", str(result)
        except Exception as exc:  # noqa: BLE001
            yield f"❌ 构建失败: {type(exc).__name__}: {exc}", None

    build_btn.click(
        fn=_on_build,
        inputs=[book_dropdown, book_editor],
        outputs=[status_md, build_output],
    )

    return {
        "tab": tab,
        "book_dropdown": book_dropdown,
        "book_editor": book_editor,
        "meta_editor": meta_editor,
        "preview_md": preview_md,
        "save_btn": save_btn,
        "build_btn": build_btn,
        "build_output": build_output,
    }


__all__ = [
    "build_edit_tab",
    "list_workspace_books",
    "load_book_md",
    "load_meta_md",
    "save_book_md",
    "save_meta_md",
    "work_dir_for_book",
]
