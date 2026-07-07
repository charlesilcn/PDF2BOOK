"""Prompt builders for the four AI review tasks.

Each builder takes a slice of the `review.json` payload and returns a
self-contained prompt string for an OpenAI-compatible chat completion.
The prompts are designed for *text-only* LLMs (no vision) — all inputs are
serialized as structured text, and outputs are expected as JSON.

Design principles:
  * **Single-task per prompt** — each call asks for one JSON object, not a
    multi-part response. This concentrates the model's attention and makes
    parsing trivial.
  * **Constraints embedded** — low-confidence correction prompts include the
    extracted constraints (max_length, preserved_chars, edit_distance) so
    the model knows the rules before answering.
  * **`[UNCLEAR]` refusal protocol** — when the model is uncertain, it must
    output `[UNCLEAR]` instead of guessing. The applier marks these for
    manual review.
  * **Minimal modification** — for all correction tasks, the model is told
    to change as little as possible (anti-hallucination).

Output JSON schemas (one per task):
  * metadata: {"title": str, "author": str, "lang": str, "date": str|null}
  * low_confidence: [{"id": str, "corrected": str, "confidence": float}]
  * titles: [{"id": str, "level": int}]
  * page_types: [{"page_index": int, "page_type": str}]
"""

from __future__ import annotations

import json
from textwrap import dedent

# ---------------------------------------------------------------------------
# Shared header
# ---------------------------------------------------------------------------

_SYSTEM_HEADER = dedent(
    """\
    你是图书 OCR 校对与排版的专家助手。你的任务是审查 OCR 后处理流水线
    产生的结构化清单，并按规则返回 JSON 修正结果。

    ## 通用规则
    1. **最小修改原则**：只修正明确错误的部分，不重写正常文本。
    2. **不确定时拒绝**：若任何条目你无法确定正确答案，输出 "[UNCLEAR]"
       而非猜测。系统会标记 [需校对] 交人工处理。
    3. **严格遵守约束**：低置信度校对任务中的 max_length / preserved_chars /
       max_edit_distance 约束是硬性限制，违规的修正会被拒绝并要求重试。
    4. **只输出 JSON**：不要包含 Markdown 代码块标记、解释性文字或前缀。
       响应必须是可直接解析的 JSON。
    """
)


# ---------------------------------------------------------------------------
# Task 1: Metadata extraction
# ---------------------------------------------------------------------------


def build_metadata_prompt(
    current: dict,
    candidates: list[dict],
) -> str:
    """Build prompt for AI metadata extraction from cover/copyright page text.

    Used when CIP rule-based extraction failed or returned default
    "Untitled"/"Unknown". The model extracts title/author/lang/date from
    the candidate page texts.
    """
    if not candidates:
        # No candidates → nothing to extract. Return a no-op prompt.
        return _build_noop_prompt("metadata", "无候选页面文本可供提取元数据。")

    payload = {
        "current_metadata": current,
        "candidate_pages": candidates,
    }

    return _SYSTEM_HEADER + dedent(
        f"""\
        ## 任务：元数据提取

        下方 JSON 包含当前元数据（可能为空或默认值）和候选页面文本
        （封面/版权页/扉页的 OCR 结果）。请从候选文本中提取书名、作者、
        语言、出版日期。

        ## 提取规则
        - 优先从版权页的"图书在版编目(CIP)数据"格式提取。
        - 书名通常是封面/扉页的最大字号文本，或 CIP 数据中"(/ 之前"的部分。
        - 作者格式：中文"著"前的姓名，或英文 "by" 后的姓名。
        - 语言：中文书默认 "zh-CN"，英文书 "en"，其他根据文本判断。
        - 日期格式：YYYY 或 YYYY-MM 或 YYYY-MM-DD。
        - 若任何字段无法确定，设为 null（不要使用 "Unknown" 或空字符串）。

        ## 输出格式
        返回单个 JSON 对象：
        {{"title": str|null, "author": str|null, "lang": str, "date": str|null}}

        ## 输入数据
        {json.dumps(payload, ensure_ascii=False, indent=2)}
        """
    )


# ---------------------------------------------------------------------------
# Task 2: Low-confidence text correction
# ---------------------------------------------------------------------------


def build_low_confidence_prompt(items: list[dict]) -> str:
    """Build prompt for AI correction of low-confidence OCR text.

    Each item includes the original text, surrounding context, and
    structural constraints. The model must return a correction that passes
    `validate_correction` (length / preserved chars / edit distance).
    """
    if not items:
        return _build_noop_prompt("low_confidence", "无低置信度文本需要校对。")

    # Trim context to keep prompt focused (already bounded by extract_context,
    # but double-cap here for safety).
    trimmed = []
    for item in items:
        trimmed.append(
            {
                "id": item["id"],
                "original_text": item["original_text"],
                "context_before": item.get("context_before", ""),
                "context_after": item.get("context_after", ""),
                "constraints": item["constraints"],
            }
        )

    return _SYSTEM_HEADER + dedent(
        f"""\
        ## 任务：低置信度 OCR 文本校对

        下方 JSON 数组包含若干低置信度文本条目。每个条目有：
        - `original_text`：OCR 识别结果（可能含通配符 * ? □）
        - `context_before` / `context_after`：前后完整句子上下文
        - `constraints`：硬性约束（必须满足）

        ## 校对规则
        1. **只填充通配符位置**：constraints.preserved_chars 列出的字符
           必须原样保留，不允许替换。只能修改通配符（* ? □ ○）位置。
        2. **字数限制**：corrected 的字符数必须 ≤ constraints.max_length。
        3. **编辑距离**：corrected 与 original_text 的编辑距离必须
           ≤ constraints.max_edit_distance。
        4. **上下文参考**：根据 context_before/after 判断通配符处应填什么字。
           若上下文不足以判断，输出 "[UNCLEAR]"。
        5. **最小修改**：只改必要的，不要"优化"正常文本。

        ## 输出格式
        返回 JSON 数组，每个元素对应一个输入条目：
        [{{"id": str, "corrected": str, "confidence": float}}]

        - `corrected`：校对后的文本，或 "[UNCLEAR]" 表示无法确定。
        - `confidence`：你对这次校对的把握（0.0-1.0）。

        ## 输入数据
        {json.dumps(trimmed, ensure_ascii=False, indent=2)}
        """
    )


# ---------------------------------------------------------------------------
# Task 3: Title level confirmation
# ---------------------------------------------------------------------------


def build_title_prompt(titles: list[dict]) -> str:
    """Build prompt for AI confirmation of title hierarchy levels.

    The rule-based `infer_title_levels` assigns H1/H2/H3 using keyword and
    font-size heuristics. The AI reviews the assignments for literary books
    where heuristics may fail (e.g., unusual chapter naming).
    """
    if not titles:
        return _build_noop_prompt("titles", "无标题候选需要确认。")

    return _SYSTEM_HEADER + dedent(
        f"""\
        ## 任务：标题层级确认

        下方 JSON 数组是规则推断的标题层级。请审查每个标题的 current_level
        是否合理，必要时调整。

        ## 层级规则
        - Level 1 (H1)：章/回/卷/篇/部分 等顶级章节标题。
        - Level 2 (H2)：节/小节 等子标题。
        - Level 3 (H3)：更小的标题（如段首小标题）。
        - 顺序约束：H2 必须在 H1 之后，H3 必须在 H2 之后。
          若规则推断跳级（如 H1 后直接 H3），请降级为最近的合法层级。

        ## 调整原则
        - 只在明显错误时调整，不要过度干预。
        - 文学书籍常见：第X章 → H1，第X节 → H2。
        - 若标题文本不含章节关键词但字号明显大，保持 H1。
        - 若无法确定，保持 current_level 不变。

        ## 输出格式
        返回 JSON 数组：
        [{{"id": str, "level": int}}]

        ## 输入数据
        {json.dumps(titles, ensure_ascii=False, indent=2)}
        """
    )


# ---------------------------------------------------------------------------
# Task 4: Page type confirmation
# ---------------------------------------------------------------------------


def build_page_type_prompt(candidates: list[dict]) -> str:
    """Build prompt for AI classification of uncertain pages.

    Pages with `page_type='unknown'` are sent for AI classification when
    the rule-based classifier (page_classifier.py) couldn't decide.
    """
    if not candidates:
        return _build_noop_prompt("page_types", "无页面需要分类确认。")

    return _SYSTEM_HEADER + dedent(
        f"""\
        ## 任务：页面分类确认

        下方 JSON 数组是规则分类器标记为 unknown 的页面。请根据文本样本
        判断每页的类型。

        ## 页面类型
        - `cover`：封面（书名、作者、出版社 logo）
        - `frontispiece`：扉页（书名、作者、出版信息）
        - `copyright`：版权页（ISBN、CIP 数据、出版日期）
        - `toc`：目录（章节标题 + 页码）
        - `preface`：前言/序言/后记
        - `body`：正文（叙述性内容）
        - `illustration`：插图页（图片为主，少量文字）
        - `appendix`：附录/索引/参考文献

        ## 判断规则
        - ISBN/CIP 关键词 → copyright
        - "目录"/"目次" + 页码模式 → toc
        - "前言"/"序"/"后记" 标题 → preface
        - 叙述性长文本（有句号、段落）→ body
        - 若无法确定，保持 "unknown"

        ## 输出格式
        返回 JSON 数组：
        [{{"page_index": int, "page_type": str}}]

        ## 输入数据
        {json.dumps(candidates, ensure_ascii=False, indent=2)}
        """
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_noop_prompt(task_name: str, reason: str) -> str:
    """Build a no-op prompt for tasks with no input items.

    The model is told there's nothing to do and should return an empty
    result. This keeps the calling code uniform (always 4 prompts built)
    without special-casing empty inputs in the AI client.
    """
    return _SYSTEM_HEADER + dedent(
        f"""\
        ## 任务：{task_name}

        {reason}

        ## 输出格式
        返回空结果：
        {{"items": []}}
        """
    )


__all__ = [
    "build_metadata_prompt",
    "build_low_confidence_prompt",
    "build_title_prompt",
    "build_page_type_prompt",
]
