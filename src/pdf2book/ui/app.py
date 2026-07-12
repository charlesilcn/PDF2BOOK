"""Assemble all Web UI tabs into a single ``gr.Blocks`` application.

This is the top-level entry point for the ``pdf2book gui`` CLI subcommand.
It wires the onboarding page (first-run setup) and four main tabs (convert,
edit, review, library) together, applies the Glass theme + CSS, and returns
a ``gr.Blocks`` ready for ``.launch()``.

Design constraints (pure extension layer):
  - Gradio is imported lazily here; this module fails to import cleanly when
    gradio is absent, and ``cli.py`` catches the ``ImportError``.
  - No ``ConversionPipeline`` or ``AppConfig`` behavior is modified — the UI
    only *calls* existing APIs.
  - All tab builders return component dicts so callbacks can be wired here
    without each tab needing cross-tab knowledge.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pdf2book.config import AppConfig
from pdf2book.ui.detect import system_status
from pdf2book.ui.theme import build_theme, glass_css


def build_app(cfg: AppConfig, log: logging.Logger, config_path: Path | None = None):
    """Build and return the ``gr.Blocks`` application.

    Parameters
    ----------
    cfg
        Loaded application config. The UI reads paths (input_dir, output_dir,
        work_dir) and AI review settings from it.
    log
        Logger for the UI (passed through to conversion threads).
    config_path
        Path to the config YAML (used for .env path resolution and config
        reload after onboarding changes). ``None`` means default discovery.

    Returns
    -------
    gr.Blocks
        The assembled Blocks app. Theme and CSS are attached as
        ``demo._pdf2book_theme`` / ``demo._pdf2book_css`` so the CLI can
        pass them to ``launch()`` (Gradio 6.0+ moved theme/css from the
        Blocks constructor to launch()).
    """
    import gradio as gr

    # Late imports of tab builders — keeps gradio import centralized here so
    # the other ui/*.py modules stay unit-testable without the gui extra.
    from pdf2book.ui.convert_tab import build_convert_tab
    from pdf2book.ui.edit_tab import build_edit_tab
    from pdf2book.ui.library_tab import build_library_tab
    from pdf2book.ui.onboarding import build_onboarding
    from pdf2book.ui.review_tab import build_review_tab

    status = system_status(cfg)
    theme = build_theme()
    css = glass_css()

    with gr.Blocks(
        title="pdf2book — PDF 转 EPUB",
        analytics_enabled=False,
    ) as demo:
        gr.Markdown("# 📚 pdf2book")

        # --- Onboarding layer (visible first, hidden after entry) ---
        onboarding = build_onboarding(cfg, config_path)

        # --- Main UI layer (hidden until "进入主界面" clicked) ---
        # Always starts hidden — onboarding's "进入主界面" button reveals it.
        # The button's `interactive` state is driven by `status.ready` so
        # the user can only enter when the core path is functional.
        with gr.Column(visible=False) as main_block:
            gr.Markdown("## 转换与管理工作台")

            with gr.Tabs():
                with gr.Tab("转换"):
                    build_convert_tab(cfg, config_path)
                with gr.Tab("编辑 / 预览"):
                    build_edit_tab(cfg, config_path)
                with gr.Tab("AI 审查对比"):
                    build_review_tab(cfg, config_path)
                with gr.Tab("书库"):
                    build_library_tab(cfg, config_path)

        # --- Wire onboarding -> main visibility toggle ---
        # The enter button is always interactive; the click handler checks
        # readiness to gate entry (Gradio 6.0's `interactive` param wraps
        # buttons in a hidden container, so we avoid it for compat).
        enter_msg = gr.Markdown("", visible=False)

        def _enter_main() -> dict:
            """Hide onboarding, show main block. Gate on system readiness."""
            current_status = system_status(cfg)
            if not current_status.ready:
                return {
                    enter_msg: gr.Markdown(
                        "⚠️ 核心组件未就绪，请先完成上方步骤并点击「刷新状态」。",
                        visible=True,
                    ),
                    onboarding["block"]: gr.Column(visible=True),
                    main_block: gr.Column(visible=False),
                }
            return {
                enter_msg: gr.Markdown("", visible=False),
                onboarding["block"]: gr.Column(visible=False),
                main_block: gr.Column(visible=True),
            }

        onboarding["enter_btn"].click(
            fn=_enter_main,
            outputs=[enter_msg, onboarding["block"], main_block],
        )

    # Attach theme/css for the caller to pass to launch() (Gradio 6.0+
    # moved these from the Blocks constructor to launch()).
    demo._pdf2book_theme = theme  # type: ignore[attr-defined]
    demo._pdf2book_css = css  # type: ignore[attr-defined]
    return demo


__all__ = ["build_app"]
