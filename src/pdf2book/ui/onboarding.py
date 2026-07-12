"""First-run onboarding page for the pdf2book Web UI.

Shows the user a setup checklist (OCR engine, Pandoc, Gradio, API key) with
install instructions, lets them configure the AI review API key (persisted to
``.env``), and gates entry to the main UI until the core path is ready.

Pure logic (status formatting, setup-step determination, .env writing) lives
in module-level functions that are unit-tested without gradio. The Gradio UI
builder ``build_onboarding`` is a thin wrapper that imports gradio lazily.
"""

from __future__ import annotations

from pathlib import Path

from pdf2book.config import AppConfig
from pdf2book.ui.detect import SystemStatus, system_status
from pdf2book.utils.env_writer import save_to_env


# --- Pure helpers (testable without gradio) --------------------------------


def env_path_for_config(config_path: Path | None) -> Path:
    """Return the ``.env`` path that corresponds to the config file location.

    Mirrors ``AppConfig.load`` which calls ``load_dotenv(Path(path).parent / ".env")``.
    When ``config_path`` is None (default config discovery), use cwd.
    """
    if config_path is not None:
        return Path(config_path).parent / ".env"
    return Path(".env")


def format_status_html(status: SystemStatus) -> str:
    """Format a ``SystemStatus`` as an HTML string with ✓/✗ badges.

    Uses the ``status-ok`` / ``status-fail`` CSS classes from the Glass theme
    so badges are color-coded (green/red).
    """
    def badge(dep_name: str, available: bool, detail: str) -> str:
        mark = '<span class="status-ok">✓</span>' if available else '<span class="status-fail">✗</span>'
        extra = f" — {detail}" if detail else ""
        return f"<p>{mark} <strong>{dep_name}</strong>{extra}</p>"

    parts = [
        badge(status.gradio.name, status.gradio.available, status.gradio.detail),
        badge(status.ocr.name, status.ocr.available, status.ocr.detail),
        badge(status.pandoc.name, status.pandoc.available, status.pandoc.detail),
        badge(status.ai_review.name, status.ai_review.available, status.ai_review.detail),
    ]
    return "\n".join(parts)


def determine_setup_steps(status: SystemStatus) -> list[str]:
    """Return human-readable setup instructions for incomplete components.

    Each entry is one actionable step. Returns an empty list when everything
    is ready (``all_ok()``). When only AI review is missing, returns a single
    step but the core path is still usable (``ready`` is True).
    """
    steps: list[str] = []
    if not status.gradio.available:
        steps.append("安装 Web UI 依赖：pip install 'pdf2book[gui]'")
    if not status.ocr.available:
        steps.append(f"安装 OCR 引擎：{status.ocr.detail}")
    if not status.pandoc.available:
        steps.append("安装 Pandoc：pip install pypandoc_binary")
    if not status.ai_review.available:
        # DependencyStatus.detail already carries the specific reason
        # ("未启用…", "已启用但未配置 api_key", "缺少 httpx…", etc.)
        steps.append(f"（可选）AI 审查：{status.ai_review.detail}")
    return steps


def save_api_key(env_path: Path, api_key: str, api_url: str = "") -> None:
    """Persist the API key (and optional URL) to ``.env``.

    Empty values are skipped by ``save_to_env`` so existing secrets aren't
    clobbered. The env var names (``PDF2BOOK_API_KEY``, ``PDF2BOOK_API_URL``)
    match the ``${...}`` references in ``config.yaml``.
    """
    mapping: dict[str, str] = {}
    if api_key:
        mapping["PDF2BOOK_API_KEY"] = api_key
    if api_url:
        mapping["PDF2BOOK_API_URL"] = api_url
    if mapping:
        save_to_env(env_path, mapping)


def reload_config(config_path: Path | None) -> AppConfig:
    """Reload config after .env changes so the new API key is picked up."""
    if config_path is not None:
        return AppConfig.load(config_path)
    cwd_config = Path("config.yaml")
    if cwd_config.exists():
        return AppConfig.load(cwd_config)
    return AppConfig.default()


# --- Gradio UI builder (needs gradio, called only when GUI launches) -------


def build_onboarding(cfg: AppConfig, config_path: Path | None = None):  # type: ignore[no-untyped-def]
    """Build the onboarding UI block.

    Returns a dict of Gradio component references for ``app.py`` to wire up
    callbacks. Imports gradio lazily so this module loads without the gui extra.

    Layout:
      1. Title + intro
      2. Status checklist (HTML, refreshable)
      3. Setup steps (HTML, derived from status)
      4. API key input + save button
      5. AI review toggle
      6. "进入主界面" button (gates entry)
    """
    import gradio as gr

    status = system_status(cfg)
    env_path = env_path_for_config(config_path)

    with gr.Column(visible=True) as onboarding_block:
        gr.Markdown("# pdf2book 初始设置")
        gr.Markdown(
            "首次使用前，请确认 OCR 引擎与 Pandoc 已安装。"
            "AI 审查为可选功能，可稍后配置。"
        )

        status_html = gr.HTML(value=format_status_html(status))
        steps_html = gr.HTML(
            value=_format_steps(determine_setup_steps(status))
        )

        with gr.Accordion("AI 审查配置（可选）", open=not status.ai_review.available):
            api_key_input = gr.Textbox(
                label="API Key",
                type="password",
                placeholder="sk-...",
                value=cfg.ai_review.api_key or "",
            )
            api_url_input = gr.Textbox(
                label="API URL（可选，留空使用默认）",
                value=cfg.ai_review.api_url or "",
                placeholder="https://api.openai.com/v1",
            )
            ai_toggle = gr.Checkbox(
                label="启用 AI 审查",
                value=cfg.ai_review.enabled,
            )
            save_btn = gr.Button("保存到 .env", variant="primary")
            save_status = gr.Markdown("")

        refresh_btn = gr.Button("🔄 刷新状态")
        enter_btn = gr.Button(
            "进入主界面 →",
            variant="primary",
        )

    # --- Callbacks ---
    # NOTE: actual callback wiring happens in app.py which has access to the
    # main UI block for visibility toggling. Here we only define the save
    # handler which is self-contained.

    def _on_save(key: str, url: str, enabled: bool) -> str:
        try:
            save_api_key(env_path, key, url)
            # Also persist the enabled flag — but that goes in config.yaml,
            # not .env. For simplicity we only persist key/url to .env here;
            # the toggle takes effect on next config reload.
            return "✅ 已保存到 .env。请点击「刷新状态」以应用更改。"
        except Exception as exc:  # noqa: BLE001
            return f"❌ 保存失败：{exc}"

    save_btn.click(
        fn=_on_save,
        inputs=[api_key_input, api_url_input, ai_toggle],
        outputs=save_status,
    )

    def _on_refresh() -> tuple[str, str]:
        reloaded = reload_config(config_path)
        new_status = system_status(reloaded)
        return (
            format_status_html(new_status),
            _format_steps(determine_setup_steps(new_status)),
        )

    refresh_btn.click(
        fn=_on_refresh,
        outputs=[status_html, steps_html],
    )

    return {
        "block": onboarding_block,
        "enter_btn": enter_btn,
        "refresh_btn": refresh_btn,
    }


def _format_steps(steps: list[str]) -> str:
    """Render setup steps as an HTML list."""
    if not steps:
        return '<p class="status-ok">✅ 所有组件已就绪！</p>'
    items = "".join(f"<li>{s}</li>" for s in steps)
    return f"<p><strong>待完成步骤：</strong></p><ul>{items}</ul>"


__all__ = [
    "build_onboarding",
    "determine_setup_steps",
    "env_path_for_config",
    "format_status_html",
    "reload_config",
    "save_api_key",
]
