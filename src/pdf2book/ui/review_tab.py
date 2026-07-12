"""Review tab: AI correction before/after comparison.

Shows low-confidence OCR blocks (the ``>[low-confidence]`` markers that AI
review targets) and lets the user re-run AI review to see a diff of the
corrections. Uses ``difflib`` (stdlib) for the diff so the pure helper
``format_unified_diff`` is unit-testable without gradio.

Pure helpers:
  - ``extract_low_confidence_blocks(md_text)`` — find ``>[low-confidence]`` lines
  - ``format_unified_diff(before, after)`` — unified diff via ``difflib``

The gradio UI builder re-runs ``AIClient.review_markdown`` on a copy of
``book.md`` and displays the before/after diff.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from pdf2book.config import AppConfig

# Matches lines like:  >[low-confidence] 这是OCR识别的文本
_LOW_CONF_RE = re.compile(r"^(>\[low-confidence\]\s*)(.*)$", re.MULTILINE)


# --- Pure helpers (testable without gradio) --------------------------------


def extract_low_confidence_blocks(md_text: str) -> list[tuple[int, str]]:
    """Find all ``>[low-confidence]`` blocks in ``md_text``.

    Returns a list of ``(line_number, text)`` where ``line_number`` is 1-based
    and ``text`` is the line content (with the marker stripped). Empty when no
    low-confidence markers remain (i.e. AI review already cleaned them).
    """
    blocks: list[tuple[int, str]] = []
    for i, line in enumerate(md_text.splitlines(), 1):
        m = _LOW_CONF_RE.match(line)
        if m:
            blocks.append((i, m.group(2)))
    return blocks


def format_unified_diff(before: str, after: str, context_lines: int = 2) -> str:
    """Return a unified diff string between ``before`` and ``after``.

    Uses ``difflib.unified_diff`` (stdlib). Returns an empty string when the
    texts are identical (no changes). Line-level diff; suitable for showing
    AI review corrections in the UI.
    """
    if before == after:
        return ""
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="修正前",
        tofile="修正后",
        n=context_lines,
    )
    return "".join(diff)


def has_low_confidence_markers(md_text: str) -> bool:
    """Quick check: does ``md_text`` still contain ``>[low-confidence]`` markers?"""
    return bool(_LOW_CONF_RE.search(md_text))


def count_low_confidence_blocks(md_text: str) -> int:
    """Count the number of low-confidence blocks in ``md_text``."""
    return len(extract_low_confidence_blocks(md_text))


# --- Gradio UI builder (needs gradio, called only when GUI launches) -------


def build_review_tab(cfg: AppConfig, config_path: Path | None = None):  # type: ignore[no-untyped-def]
    """Build the AI review comparison tab UI.

    Layout:
      1. Book selector dropdown (from workspace/)
      2. Load button → fills the before/after editors + low-confidence summary
      3. Before (current book.md) / After (re-reviewed) side-by-side
      4. "运行 AI 审查" button → re-runs review on a copy, shows diff
      5. Diff output area

    Returns a dict of Gradio component references.
    """
    import gradio as gr

    from pdf2book.ui.edit_tab import list_workspace_books, work_dir_for_book

    book_choices = list_workspace_books(cfg)

    with gr.Tab("AI 审查对比") as tab:
        gr.Markdown("## AI 修正前后对比")

        with gr.Row():
            book_dropdown = gr.Dropdown(
                label="选择书目（workspace/）",
                choices=book_choices,
                interactive=True,
            )
            load_btn = gr.Button("📂 加载")
            review_btn = gr.Button("🤖 运行 AI 审查", variant="primary")

        summary_md = gr.Markdown("")

        with gr.Row():
            before_text = gr.Textbox(
                label="修正前（当前 book.md）",
                lines=20,
                interactive=False,
            )
            after_text = gr.Textbox(
                label="修正后（AI 审查后）",
                lines=20,
                interactive=False,
            )

        diff_text = gr.Textbox(
            label="差异（Unified Diff）",
            lines=15,
            interactive=False,
            placeholder="点击「运行 AI 审查」后此处显示差异...",
        )

    def _on_load(book_stem: str) -> tuple[str, str, str]:
        if not book_stem:
            return "", "", "❌ 请先选择一个书目"
        wd = work_dir_for_book(cfg, book_stem)
        book_md = wd / "book.md"
        if not book_md.exists():
            return "", "", f"❌ 未找到 {book_md}"
        content = book_md.read_text(encoding="utf-8")
        lc_count = count_low_confidence_blocks(content)
        if lc_count > 0:
            summary = f"⚠️ 发现 **{lc_count}** 处低置信度标记待修正。"
        else:
            summary = "✅ 无低置信度标记（AI 审查已完成或无需修正）。"
        return content, "", summary

    load_btn.click(
        fn=_on_load,
        inputs=book_dropdown,
        outputs=[before_text, after_text, summary_md],
    )

    def _on_review(book_stem: str, before_content: str):
        """Re-run AI review on a copy, yield (after_text, diff_text, summary)."""
        if not book_stem:
            yield "", "", "❌ 请先选择一个书目"
            return
        if not before_content:
            yield "", "", "❌ 请先点击「加载」"
            return

        if not cfg.ai_review.enabled:
            yield before_content, "", "❌ AI 审查未启用。请在「初始设置」中配置 API Key。"
            return

        # Save a temporary copy to run review on
        wd = work_dir_for_book(cfg, book_stem)
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_book = Path(tmpdir) / "book.md"
            tmp_meta = Path(tmpdir) / "meta.md"
            tmp_book.write_text(before_content, encoding="utf-8")
            # Copy meta.md if it exists
            orig_meta = wd / "meta.md"
            if orig_meta.exists():
                tmp_meta.write_text(orig_meta.read_text(encoding="utf-8"), encoding="utf-8")
            else:
                tmp_meta.write_text("---\ntitle: Unknown\n---\n", encoding="utf-8")

            try:
                from pdf2book.epub.metadata import read_meta_yaml
                from pdf2book.review.ai_client import AIClient

                meta = read_meta_yaml(tmp_meta)
                client = AIClient(cfg.ai_review, work_dir=wd)
                client.review_markdown(tmp_book, tmp_meta, meta)
                after_content = tmp_book.read_text(encoding="utf-8")
                diff = format_unified_diff(before_content, after_content)
                if not diff:
                    summary = "✅ AI 审查完成，无修改。"
                else:
                    lc_before = count_low_confidence_blocks(before_content)
                    lc_after = count_low_confidence_blocks(after_content)
                    summary = (
                        f"✅ AI 审查完成。低置信度标记：{lc_before} → {lc_after}。"
                    )
                yield after_content, diff, summary
            except Exception as exc:  # noqa: BLE001
                yield "", "", f"❌ AI 审查失败: {type(exc).__name__}: {exc}"

    review_btn.click(
        fn=_on_review,
        inputs=[book_dropdown, before_text],
        outputs=[after_text, diff_text, summary_md],
    )

    return {
        "tab": tab,
        "book_dropdown": book_dropdown,
        "before_text": before_text,
        "after_text": after_text,
        "diff_text": diff_text,
        "review_btn": review_btn,
    }


__all__ = [
    "build_review_tab",
    "count_low_confidence_blocks",
    "extract_low_confidence_blocks",
    "format_unified_diff",
    "has_low_confidence_markers",
]
