"""Markdown-based AI review: collect issues, build prompt, apply corrections.

This module replaces the former PageResult-based review pipeline. Instead of
reviewing OCR elements before markdown generation, it reviews the generated
``book.md`` directly — seeing the actual structure that will become the EPUB.

Three-stage flow:
  1. **Collect** — ``collect_markdown_issues`` scans book.md line by line for:
     - Low-confidence blocks (``>[low-confidence] {text}``)
     - Chapter titles (``# title {#ch-N}``) with issue detection (OCR garbled,
       split titles, too short)
     - Current metadata (from BookMetadata)
  2. **Prompt** — ``build_review_prompt`` builds a single comprehensive prompt
     containing all issues with context. AI returns one JSON response.
  3. **Apply** — ``apply_markdown_corrections`` writes corrections back to
     book.md by line number (descending order to preserve indices) and
     updates meta.md.

The constraint-validation retry loop from the old pipeline is preserved for
low-confidence text: corrections must pass ``validate_correction`` before
being applied. Title fixes have no constraint validation (AI infers from
chapter content context).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from textwrap import dedent

from pdf2book.epub.metadata import BookMetadata, read_meta_yaml, write_meta_yaml
from pdf2book.review.constraints import (
    CorrectionConstraints,
    extract_constraints,
    validate_correction,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Low-confidence block prefix in book.md (from structure.to_markdown).
_LOW_CONF_PREFIX = ">[low-confidence] "

# Page boundary marker: <!-- page: N --> (emitted by structure.to_markdown
# when emit_page_markers=True, for multimodal review page-image mapping).
_PAGE_MARKER_RE = re.compile(r"^<!--\s*page:\s*(\d+)\s*-->\s*$")

# Heading pattern: matches H1-H6 ("# title" through "###### title").
# Captures: group(1)=hashes, group(2)=title, group(3)=anchor (optional).
# H1 retains a dedicated fast-path regex for `_detect_toc_issues` and
# `_detect_chapter_structure_issues` (which only care about H1 titles).
_H1_RE = re.compile(r"^# (.+?)(?:\s+\{#(ch-\d+)\})?\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+\{#(ch-\d+)\})?\s*$")

# Characters that signal OCR garbling in titles.
_GARBLED_CHARS = frozenset("©®™□○◇●〼*?")

# Dash prefixes that indicate a split title continuation.
_SPLIT_DASH_PREFIXES = ("——", "—", "--", "～", "～")

# Maximum chars of chapter context to include per title for AI inference.
_CHAPTER_CONTEXT_LIMIT = 200

# Maximum chars of context around low-confidence blocks.
_CONTEXT_LINES = 3

# Standalone image reference line: `![alt](images/pN_eM.png)`.
# Matches only lines whose entire content is an image reference (no inline
# text), so paragraphs with embedded images are not treated as decorations.
_IMAGE_REF_RE = re.compile(r"^\s*!\[([^\]]*)\]\((images/[^)]+)\)\s*$")

# Window around an image line to search for a nearby H1 chapter title.
# ±3 covers the common `# title\n\n![](image)` pattern (title, blank, image).
_DECORATION_TITLE_WINDOW = 3


# ---------------------------------------------------------------------------
# 1. Collector
# ---------------------------------------------------------------------------


def collect_markdown_issues(
    md_path: Path,
    meta: BookMetadata | None,
    work_dir: Path | None = None,
    max_images: int = 0,
) -> dict:
    """Scan book.md for issues that need AI review.

    Returns a dict with keys:
      - ``low_confidence_texts``: list of items with id, line, text, context, constraints
      - ``title_candidates``: list of items with id, line, title, issue, context
      - ``paragraph_issues``: list of short-line-cluster items (broken paragraphs)
      - ``chapter_structure_issues``: list of empty-chapter / level-jump items
      - ``toc_issues``: list of TOC-vs-H1 mismatch items
      - ``decoration_candidates``: list of image refs near H1 titles (multimodal)
      - ``decoration_review_images``: list of {path} for decoration candidate images
      - ``book_structure``: BookStructure from meta (or None)
      - ``metadata``: dict with ``current`` metadata fields
      - ``review_images``: list of {page_index, path} for multimodal review

    Line numbers are 0-based (matching Python list indices) so the applier
    can directly index into the lines list.

    When ``work_dir`` and ``max_images > 0`` are provided, page boundary
    markers (``<!-- page: N -->``) are parsed to attach ``page_index`` and
    ``page_image_path`` to low-confidence and title items, and a
    ``review_images`` list (deduplicated, limited to ``max_images``) is
    returned for multimodal review. Decoration candidates (images near H1
    titles) are also collected when multimodal is enabled.
    """
    lines = md_path.read_text(encoding="utf-8").splitlines()

    # Pre-scan page markers to build line_index → page_index mapping.
    page_at_line: dict[int, int | None] = {}
    current_page: int | None = None
    for i, line in enumerate(lines):
        m = _PAGE_MARKER_RE.match(line)
        if m:
            current_page = int(m.group(1))
        page_at_line[i] = current_page

    def _resolve_page_image(page_idx: int | None) -> Path | None:
        if page_idx is None or work_dir is None:
            return None
        candidate = work_dir / "pages" / f"page_{page_idx:04d}.png"
        return candidate if candidate.exists() else None

    low_confidence_items: list[dict] = []
    title_items: list[dict] = []

    for i, line in enumerate(lines):
        # Low-confidence blocks: >[low-confidence] {text}
        if line.startswith(_LOW_CONF_PREFIX):
            text = line[len(_LOW_CONF_PREFIX):].strip()
            if not text:
                continue
            context_before = _extract_context(lines, i, direction="before")
            context_after = _extract_context(lines, i, direction="after")
            constraints = extract_constraints(text, context_before, context_after)
            page_idx = page_at_line.get(i)
            low_confidence_items.append(
                {
                    "id": f"LC-{len(low_confidence_items) + 1}",
                    "line": i,
                    "original_text": text,
                    "context_before": context_before,
                    "context_after": context_after,
                    "constraints": {
                        "max_length": constraints.max_length,
                        "preserved_chars": constraints.preserved_chars,
                        "max_edit_distance": constraints.max_edit_distance,
                        "wildcard_count": constraints.wildcard_count,
                    },
                    "page_index": page_idx,
                    "page_image_path": _resolve_page_image(page_idx),
                }
            )

        # Headings of all levels (H1-H6). H1 titles are always collected
        # (chapter structure depends on them). H2-H6 are only collected
        # when they have a detected issue (ocr_error / too_short / split_title)
        # to avoid flooding the AI with hundreds of normal sub-headings.
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            hashes = heading_match.group(1)
            title = heading_match.group(2).strip()
            anchor = heading_match.group(3)
            level = len(hashes)
            issue = _detect_title_issue(title, lines, i)
            if level == 1 or issue != "normal":
                chapter_context = _extract_chapter_context(lines, i)
                page_idx = page_at_line.get(i)
                title_items.append(
                    {
                        "id": f"T-{len(title_items) + 1}",
                        "line": i,
                        "title": title,
                        "anchor": anchor,
                        "level": level,
                        "raw_line": line,
                        "issue": issue,
                        "context": chapter_context,
                        "page_index": page_idx,
                        "page_image_path": _resolve_page_image(page_idx),
                    }
                )

    # Paragraph layout issues: short-line clusters (broken paragraphs).
    paragraph_issues = _detect_paragraph_issues(lines)

    # Chapter structure issues: empty chapters, level jumps.
    chapter_structure_issues = _detect_chapter_structure_issues(lines, title_items)

    # TOC issues: TOC entries vs H1 chapter titles.
    toc_issues = _detect_toc_issues(lines, title_items)

    # TOC linkification data: region lines + H1 anchors for AI to linkify.
    toc_linkification = _collect_toc_linkification(lines, title_items)

    # Decoration candidates: images near H1 titles for multimodal AI judgment.
    decoration_candidates, decoration_review_images = (
        _collect_decoration_candidates(lines, title_items, work_dir, max_images)
    )

    # Collect review images for multimodal review (deduplicated + limited).
    # Priority: low-confidence pages first, then title-issue pages,
    # then decoration candidates (extracted images, not page screenshots).
    review_images: list[dict] = []
    if max_images > 0:
        seen_pages: set[int] = set()
        for item in low_confidence_items:
            pidx = item.get("page_index")
            pimg = item.get("page_image_path")
            if pidx is not None and pimg is not None and pidx not in seen_pages:
                seen_pages.add(pidx)
                review_images.append({"page_index": pidx, "path": pimg})
        for item in title_items:
            if item.get("issue") == "normal":
                continue
            pidx = item.get("page_index")
            pimg = item.get("page_image_path")
            if pidx is not None and pimg is not None and pidx not in seen_pages:
                seen_pages.add(pidx)
                review_images.append({"page_index": pidx, "path": pimg})
        review_images = review_images[:max_images]

    # Metadata
    metadata_current: dict = {}
    book_structure = None
    if meta is not None:
        metadata_current = {
            "title": meta.title,
            "author": meta.author,
            "lang": getattr(meta, "lang", "zh-CN"),
            "date": getattr(meta, "date", None),
            "publisher": getattr(meta, "publisher", None),
            "rights": getattr(meta, "rights", None),
        }
        book_structure = getattr(meta, "book_structure", None)

    return {
        "low_confidence_texts": low_confidence_items,
        "title_candidates": title_items,
        "paragraph_issues": paragraph_issues,
        "chapter_structure_issues": chapter_structure_issues,
        "toc_issues": toc_issues,
        "toc_linkification": toc_linkification,
        "decoration_candidates": decoration_candidates,
        "decoration_review_images": decoration_review_images,
        "book_structure": book_structure,
        "metadata": {"current": metadata_current},
        "review_images": review_images,
    }


def _extract_context(lines: list[str], idx: int, direction: str) -> str:
    """Extract surrounding lines as context for a low-confidence block.

    Skips other low-confidence blocks and empty lines at the boundary.
    """
    context_lines: list[str] = []
    if direction == "before":
        start = max(0, idx - _CONTEXT_LINES)
        for i in range(idx - 1, start - 1, -1):
            line = lines[i].strip()
            if not line or line.startswith(_LOW_CONF_PREFIX):
                break
            context_lines.insert(0, line)
    else:  # after
        end = min(len(lines), idx + _CONTEXT_LINES + 1)
        for i in range(idx + 1, end):
            line = lines[i].strip()
            if not line or line.startswith(_LOW_CONF_PREFIX):
                break
            context_lines.append(line)
    return "\n".join(context_lines)


def _detect_title_issue(title: str, lines: list[str], idx: int) -> str:
    """Detect potential issues in a chapter title.

    Returns one of:
      - ``"normal"``: no issue detected
      - ``"ocr_error"``: contains garbled characters (©, *, ?, etc.)
      - ``"too_short"``: title is ≤2 characters
      - ``"split_title"``: next line is a continuation (starts with dash or short text)
    """
    # Check for garbled characters
    if any(ch in _GARBLED_CHARS for ch in title):
        return "ocr_error"

    # Check for very short titles (likely OCR truncation)
    if len(title.strip()) <= 2:
        return "too_short"

    # Check for split title (next line is a continuation)
    if idx + 1 < len(lines):
        next_line = lines[idx + 1].strip()
        if (
            next_line
            and not next_line.startswith("#")
            and not next_line.startswith(":::")
            and not next_line.startswith("![")
        ):
            if any(next_line.startswith(prefix) for prefix in _SPLIT_DASH_PREFIXES):
                return "split_title"
            # Short line without sentence-ending punctuation might be a continuation
            if len(next_line) <= 30 and not next_line.endswith(
                ("。", "！", "？", ";", "；", ".")
            ):
                return "split_title"

    return "normal"


def _extract_chapter_context(lines: list[str], title_idx: int) -> str:
    """Extract the first ~200 chars of chapter content after the title.

    Skips the title line itself, empty lines, and image references.
    Stops at the next H1 heading or chapter div.
    """
    context_chars: list[str] = []
    total = 0
    for i in range(title_idx + 1, len(lines)):
        line = lines[i].strip()
        if not line:
            continue
        if line.startswith("# ") or line.startswith(":::"):
            break
        if line.startswith("!["):
            continue
        context_chars.append(line)
        total += len(line)
        if total >= _CHAPTER_CONTEXT_LIMIT:
            break
    return "\n".join(context_chars)[:_CHAPTER_CONTEXT_LIMIT]


# Lines that should never be treated as paragraph content.
_NON_BODY_PREFIXES = ("#", "!", ":::", "$$", "<table", "<!--", _LOW_CONF_PREFIX)


def _is_non_body_line(stripped: str) -> bool:
    """Return True for lines that are not paragraph content."""
    if not stripped:
        return True
    return any(stripped.startswith(p) for p in _NON_BODY_PREFIXES)


def _detect_paragraph_issues(lines: list[str]) -> list[dict]:
    """Detect paragraph layout issues: clusters of short lines (broken paragraphs).

    A short-line cluster is ≥3 consecutive non-empty body lines that are each
    ≤30 chars and don't end with sentence-ending punctuation. These likely
    indicate OCR line-break artifacts that should be merged into one paragraph.
    """
    issues: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if _is_non_body_line(line):
            i += 1
            continue

        if len(line) <= 30 and not line.endswith(
            ("。", "！", "？", ";", "；", ".")
        ):
            cluster_start = i
            cluster_count = 1
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if _is_non_body_line(next_line):
                    break
                if len(next_line) <= 30 and not next_line.endswith(
                    ("。", "！", "？", ";", "；", ".")
                ):
                    cluster_count += 1
                    j += 1
                else:
                    break
            if cluster_count >= 3:
                issues.append(
                    {
                        "id": f"P-{len(issues) + 1}",
                        "type": "short_line_cluster",
                        "start_line": cluster_start,
                        "end_line": j - 1,
                        "line_count": cluster_count,
                        "sample": lines[cluster_start].strip(),
                    }
                )
            i = j
        else:
            i += 1
    return issues


def _detect_chapter_structure_issues(
    lines: list[str], title_items: list[dict]
) -> list[dict]:
    """Detect chapter structure issues: empty chapters and heading level jumps.

    - ``empty_chapter``: an H1 immediately followed by another H1 with no
      body content between them.
    - ``level_jump``: an H1 followed by an H3+ (skipping H2). The fix
      targets the H3 (promote to H2), NOT the H1 — demoting H1 would
      destroy chapter structure and collapse all chapters into one EPUB file.

    Scans all heading lines (H1-H6) directly so level jumps across non-H1
    headings are also detected. ``title_items`` (H1-only) is used to
    populate the ``title`` field for empty-chapter reports.
    """
    issues: list[dict] = []
    # Scan all heading lines: collect (line_idx, title, level).
    headings: list[tuple[int, str, int]] = []
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            # Extract title text after the # marks.
            title = stripped[level:].strip()
            # Strip any anchor suffix like {#ch-N}.
            title = re.sub(r"\s+\{#ch-\d+\}\s*$", "", title)
            headings.append((i, title, level))

    # Build a lookup from H1 line_idx to title (from title_items).
    # title_items may contain non-H1 headings (when they have issues), so
    # filter to level==1 only (default to 1 for backward compatibility).
    h1_titles = {
        item["line"]: item["title"]
        for item in title_items
        if item.get("level", 1) == 1
    }

    for i, (line_idx, title, level) in enumerate(headings):
        if i + 1 >= len(headings):
            break
        next_line_idx, _, next_level = headings[i + 1]

        # Empty chapter: H1 followed by H1 with no content between.
        if level == 1 and next_level == 1:
            has_content = any(
                not _is_non_body_line(lines[k].strip())
                for k in range(line_idx + 1, next_line_idx)
            )
            if not has_content:
                issues.append(
                    {
                        "id": f"CS-{len(issues) + 1}",
                        "type": "empty_chapter",
                        "line": line_idx,
                        "title": h1_titles.get(line_idx, title),
                    }
                )

        # Level jump: H1 → H3+ (missing H2). Promote the H3 to H2, NOT
        # demote the H1 — demoting H1 destroys chapter structure (all
        # "第N卷" chapters would collapse to H2, merging into one giant
        # EPUB XHTML file with no chapter splits).
        if level == 1 and next_level >= 3:
            issues.append(
                {
                    "id": f"CS-{len(issues) + 1}",
                    "type": "level_jump",
                    "line": next_line_idx,
                    "from_level": next_level,
                    "to_level": 2,
                    "title": headings[i + 1][1],
                }
            )

    return issues


def _detect_toc_issues(
    lines: list[str], title_items: list[dict]
) -> list[dict]:
    """Detect TOC issues: TOC entries that don't match chapter H1 titles.

    Looks for a TOC section marked by an H3 containing "CONTENTS" or "目录",
    extracts entry titles (stripping trailing page-number patterns), and
    compares them against H1 chapter titles. Reports mismatches when no H1
    title has ≥50% character overlap with a TOC entry.
    """
    issues: list[dict] = []
    toc_entries: list[dict] = []
    in_toc = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("### ") and (
            "CONTENTS" in stripped or "目录" in stripped
        ):
            in_toc = True
            continue
        if in_toc and (
            stripped.startswith("# ")
            or stripped.startswith("::: {.chapter}")
        ):
            in_toc = False
            continue
        if in_toc and stripped:
            toc_title = re.split(
                r"[.．…]+\s*\d|／\d|/\d", stripped
            )[0].strip()
            if toc_title:
                toc_entries.append({"line": i, "title": toc_title})

    if not toc_entries:
        return []

    h1_titles = [
        item["title"]
        for item in title_items
        if item["raw_line"].startswith("# ")
    ]
    mismatches: list[dict] = []
    for entry in toc_entries:
        best_match = None
        best_ratio = 0.0
        for h1 in h1_titles:
            if not entry["title"] or not h1:
                continue
            common = len(set(entry["title"]) & set(h1))
            ratio = common / max(len(entry["title"]), len(h1))
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = h1
        if best_ratio < 0.5:
            mismatches.append(
                {
                    "toc_line": entry["line"],
                    "toc_title": entry["title"],
                    "best_match": best_match,
                    "similarity": round(best_ratio, 2),
                }
            )

    if mismatches:
        issues.append(
            {
                "id": "TOC-1",
                "type": "toc_mismatch",
                "mismatch_count": len(mismatches),
                "mismatches": mismatches[:5],
                "h1_count": len(h1_titles),
                "toc_count": len(toc_entries),
            }
        )

    return issues


# H1 chapter anchor line: `# title {#ch-N}`.
_H1_CHAPTER_ANCHOR_RE = re.compile(r"^#\s+.+\{#ch-\d+\}\s*$")

# Fenced-div sentinel for already-linkified TOC.
_TOC_LIST_SENTINEL = "::: {.toc-list}"


def _collect_toc_linkification(
    lines: list[str], title_items: list[dict]
) -> dict | None:
    """Collect TOC region lines and H1 anchors for AI linkification.

    Locates the TOC region (from a "目录"/"CONTENTS" heading to the first
    H1 chapter line), gathers the region's line numbers + text, and the
    list of H1 chapter anchors (title, anchor id, line).

    Returns None when:
      - No TOC heading is found.
      - No H1 chapter anchor exists.
      - The region already contains a ``::: {.toc-list}`` block (already
        linkified — skip to keep the operation idempotent).
    """
    # Find TOC heading start (reuse the same keyword set as _detect_toc_issues).
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") and (
            "CONTENTS" in stripped or "目录" in stripped
        ):
            start = i
            break
    if start is None:
        return None

    # Find the first H1 chapter anchor line at or after start.
    end = None
    for i in range(start, len(lines)):
        if _H1_CHAPTER_ANCHOR_RE.match(lines[i]):
            end = i
            break
    if end is None or end <= start:
        return None

    # Skip if already linkified.
    for i in range(start, end):
        if _TOC_LIST_SENTINEL in lines[i]:
            return None

    # Collect H1 anchors from title_items (only level-1 items with anchors).
    h1_anchors = []
    for item in title_items:
        if item.get("level", 1) == 1 and item.get("anchor"):
            h1_anchors.append(
                {
                    "title": item["title"],
                    "anchor": item["anchor"],
                    "line": item["line"],
                }
            )
    if not h1_anchors:
        return None

    region_lines = [
        {"line": i, "text": lines[i]} for i in range(start, end)
    ]
    return {
        "start_line": start,
        "end_line": end,
        "lines": region_lines,
        "h1_anchors": h1_anchors,
    }


def _collect_decoration_candidates(
    lines: list[str],
    title_items: list[dict],
    work_dir: Path | None,
    max_images: int,
) -> tuple[list[dict], list[dict]]:
    """Collect image references near chapter titles as decoration candidates.

    Returns ``(candidates, review_images)`` where:
      - ``candidates``: list of dicts with id, line, image_path, nearby_title,
        nearby_title_line, alt_text.
      - ``review_images``: list of ``{"path": Path}`` for the candidate image
        files (deduplicated, limited to ``max_images``), for multimodal AI.

    An image is a candidate when:
      - It is a standalone image line (``![alt](images/pN_eM.png)``).
      - An H1 title exists within ±``_DECORATION_TITLE_WINDOW`` lines.
      - The image file exists in ``work_dir / image_path``.

    Only runs when ``max_images > 0`` (multimodal enabled) — Phase 2 is
    multimodal-only. When disabled, returns empty lists and Phase 1
    (``decorations.strip_decorations``) handles everything deterministically.

    Only H1 titles (level==1) are used as nearby anchors — chapter-divider
    decorations appear beside chapter headings, not sub-section headings.
    """
    if max_images <= 0 or work_dir is None:
        return [], []

    h1_lines = {
        item["line"]: item
        for item in title_items
        if item.get("level", 1) == 1
    }
    if not h1_lines:
        return [], []

    candidates: list[dict] = []
    seen_paths: set[str] = set()
    review_images: list[dict] = []

    for i, line in enumerate(lines):
        m = _IMAGE_REF_RE.match(line)
        if not m:
            continue
        rel_path = m.group(2)
        alt_text = m.group(1)

        # Find a nearby H1 title within ±window lines.
        nearby_title_item: dict | None = None
        for offset in range(
            -_DECORATION_TITLE_WINDOW, _DECORATION_TITLE_WINDOW + 1
        ):
            check_line = i + offset
            if check_line in h1_lines:
                nearby_title_item = h1_lines[check_line]
                break
        if nearby_title_item is None:
            continue

        full_path = work_dir / rel_path
        if not full_path.exists():
            continue

        candidates.append(
            {
                "id": f"DC-{len(candidates) + 1}",
                "line": i,
                "image_path": rel_path,
                "nearby_title": nearby_title_item["title"],
                "nearby_title_line": nearby_title_item["line"],
                "alt_text": alt_text,
            }
        )

        if rel_path not in seen_paths and len(review_images) < max_images:
            seen_paths.add(rel_path)
            review_images.append({"path": full_path})

    return candidates, review_images


# ---------------------------------------------------------------------------
# 2. Prompt builder
# ---------------------------------------------------------------------------


_SYSTEM_HEADER = dedent(
    """\
    你是图书 OCR 校对与排版的专家助手。审查 OCR 后处理生成的 Markdown
    文档并修复问题。

    ## 输出格式（最高优先级，违反即视为失败）
    - **第一个字符必须是 `{`，最后一个字符必须是 `}`**。
    - 不要输出任何前置文字、寒暄、解释、推理、思考过程、代码块标记。
    - 不要复述输入数据，不要描述附图内容，不要解释你的判断依据。
    - 只输出需要修正的条目；正常条目不要列出；空结果输出 `{}`。

    ### 正确输出示例
    {"low_confidence_fixes": [{"id": "LC-1", "corrected": "电线"}], "title_fixes": []}

    ### 严格禁止（以下任何一种都视为失败）
    - "好的，我来帮你..." 或任何寒暄
    - "分析：该低置信度文本..." 或任何推理/思考链
    - "图片显示..." 或任何图像描述（多模态任务除外，且仅作为 classification 字段值）
    - ```json ... ``` 代码块标记
    - 复述原始条目或上下文

    ## 通用规则
    1. **最小修改原则**：只修正明确错误的部分，不重写正常文本。
    2. **不确定时拒绝**：若任何低置信度文本你无法确定正确答案，输出
       "[UNCLEAR]" 而非猜测。系统会标记 [需校对] 交人工处理。
    3. **严格遵守约束**：低置信度校对任务中的 max_length / preserved_chars /
       max_edit_distance 约束是硬性限制。
    4. **禁止幻觉**：所有修正必须基于上下文或附图证据，不得凭空创造。
       - 标题修正必须与章节正文内容相符，不能编造标题。
       - 低置信度校对必须能从上下文推断出原文，不能猜测生僻字。
       - 无法确定时一律输出 "[UNCLEAR]"，由人工处理。
    """
)


def build_review_prompt(issues: dict) -> str:
    """Build a single comprehensive review prompt from collected issues.

    The prompt includes up to nine tasks:
      1. Low-confidence OCR text correction
      2. Chapter title review (garbled / split / too short)
      3. Metadata verification
      4. Paragraph layout fix (short-line clusters)
      5. Chapter structure fix (empty chapters, level jumps)
      6. TOC validation (TOC vs H1 mismatches)
      7. Book structure verification
      8. TOC linkification (convert TOC region to clickable link list)
      9. Decoration identification (multimodal: classify images near titles)

    AI returns a single JSON object with ``low_confidence_fixes``,
    ``title_fixes``, ``metadata``, ``paragraph_fixes``, ``chapter_fixes``,
    ``toc_fixes``, ``book_structure_fixes``, ``toc_linkification``, and
    ``decoration_fixes``.
    """
    low_conf = issues.get("low_confidence_texts", [])
    titles = issues.get("title_candidates", [])
    meta = issues.get("metadata", {}).get("current", {})
    paragraph_issues = issues.get("paragraph_issues", [])
    chapter_issues = issues.get("chapter_structure_issues", [])
    toc_issues = issues.get("toc_issues", [])
    toc_link = issues.get("toc_linkification")
    book_structure = issues.get("book_structure")
    deco_candidates = issues.get("decoration_candidates", [])

    sections: list[str] = [_SYSTEM_HEADER]

    # --- Task 1: Low-confidence correction ---
    if low_conf:
        trimmed = []
        for item in low_conf:
            entry = {
                "id": item["id"],
                "original_text": item["original_text"],
                "context_before": item.get("context_before", ""),
                "context_after": item.get("context_after", ""),
                "constraints": item["constraints"],
            }
            if item.get("page_index") is not None:
                entry["page_index"] = item["page_index"]
            trimmed.append(entry)
        sections.append(
            dedent(
                f"""\
                ## 任务 1：低置信度 OCR 文本校对

                下方 JSON 数组包含低置信度文本条目。每个条目有：
                - `original_text`：OCR 识别结果（可能含通配符 * ? □）
                - `context_before` / `context_after`：前后上下文
                - `constraints`：硬性约束（必须满足）
                - `page_index`：源页面编号（可对照附图识别原文）

                ### 校对规则
                1. 只填充通配符位置，preserved_chars 列出的字符必须原样保留。
                2. corrected 的字符数必须 ≤ constraints.max_length。
                3. 编辑距离必须 ≤ constraints.max_edit_distance。
                4. 根据上下文判断通配符处应填什么字。不确定时输出 "[UNCLEAR]"。
                   如有附图，请结合图片内容判断 OCR 乱码处的正确文字。

                ### 输入数据
                {json.dumps(trimmed, ensure_ascii=False, indent=2)}
                """
            )
        )

    # --- Task 2: Title review ---
    if titles:
        title_payload = []
        for item in titles:
            entry = {
                "id": item["id"],
                "level": item.get("level", 1),
                "title": item["title"],
                "issue": item["issue"],
            }
            if item["issue"] != "normal":
                entry["context"] = item.get("context", "")
            if item.get("page_index") is not None:
                entry["page_index"] = item["page_index"]
            title_payload.append(entry)

        sections.append(
            dedent(
                f"""\
                ## 任务 2：章节标题审查

                下方 JSON 数组是所有章节标题。`level` 字段是标题级别（1=H1, 2=H2, ...）。
                `issue` 字段标记了检测到的问题：
                - `normal`：正常，无需修改
                - `ocr_error`：标题含 OCR 乱码字符，请根据 context 中的章节内容推断正确标题
                - `too_short`：标题过短（≤2字），可能是 OCR 截断或 OCR 误识别
                - `split_title`：标题断行，下一行是续行，需要合并

                ### 修正规则
                1. 只修正标记为非 normal 的标题，正常标题不要修改。
                2. ocr_error：根据章节内容上下文推断正确标题。
                3. too_short：根据 context 中的章节内容推断正确标题。常见 OCR 误识别：
                   - "二录" → "目录"（"目"被误识为"二"）
                   - "木日录" → "目录"（"目"被拆分为"木日"）
                4. split_title：合并标题和续行为一行，输出合并后的完整标题。
                5. 修正时保留原标题的语义，不要创造新内容。

                ### 输入数据
                {json.dumps(title_payload, ensure_ascii=False, indent=2)}
                """
            )
        )

    # --- Task 3: Metadata verification ---
    sections.append(
        dedent(
            f"""\
            ## 任务 3：元数据验证

            当前元数据：
            {json.dumps(meta, ensure_ascii=False, indent=2)}

            请验证元数据是否正确。如需修正，提供修正后的值；无需修改的字段设为 null。
            """
        )
    )

    # --- Task 4: Paragraph layout fix ---
    if paragraph_issues:
        sections.append(
            dedent(
                f"""\
                ## 任务 4：段落排版修复

                下方 JSON 数组包含检测到的段落排版问题：
                - `short_line_cluster`：连续短行（可能是 OCR 断行），需要合并为完整段落。

                ### 修正规则
                1. 只处理标记为 short_line_cluster 的问题。
                2. 合并连续短行为一个完整段落，用空字符串连接（CJK）或空格（英文）。
                3. 保留原文，不修改内容。

                ### 输入数据
                {json.dumps(paragraph_issues, ensure_ascii=False, indent=2)}
                """
            )
        )

    # --- Task 5: Chapter structure fix ---
    if chapter_issues:
        sections.append(
            dedent(
                f"""\
                ## 任务 5：章节结构修复

                下方 JSON 数组包含章节结构问题：
                - `empty_chapter`：空白章节（H1 后紧跟下一个 H1，无内容），建议删除。
                - `level_jump`：标题层级跳级（H1→H3），将跳级标题提升为 H2。

                ### 修正规则
                1. empty_chapter：根据上下文判断是否删除该章节。action="delete"。
                2. level_jump：将跳级标题提升为 H2（保持 H1 章节结构不变）。
                   action="promote", new_level=2。
                3. 不确定时保持原样，不要输出该条目。

                ### 输入数据
                {json.dumps(chapter_issues, ensure_ascii=False, indent=2)}
                """
            )
        )

    # --- Task 6: TOC validation ---
    if toc_issues:
        sections.append(
            dedent(
                f"""\
                ## 任务 6：目录验证

                下方 JSON 包含目录（TOC）与正文章节不匹配的问题。

                ### 修正规则
                1. 检查 TOC 条目与正文章节标题的匹配度。
                2. 对于不匹配的条目，根据正文 H1 标题修正 TOC 条目标题。
                3. 不确定时保持原样。

                ### 输入数据
                {json.dumps(toc_issues, ensure_ascii=False, indent=2)}
                """
            )
        )

    # --- Task 7: Book structure validation ---
    if book_structure is not None:
        bs_data = (
            book_structure.model_dump()
            if hasattr(book_structure, "model_dump")
            else book_structure
        )
        sections.append(
            dedent(
                f"""\
                ## 任务 7：书本结构验证

                下方 JSON 是检测到的书本页面结构。

                ### 验证规则
                1. 检查页面顺序是否合理（封面→扉页→版权页→前言→目录→正文→封底）。
                2. 标记缺失的结构（如缺少版权页）。
                3. 标记异常结构（如版权页在末尾）。
                4. 如需修正，提供修正建议；无需修改则 ai_verified=true 并给出说明。

                ### 输入数据
                {json.dumps(bs_data, ensure_ascii=False, indent=2, default=str)}
                """
            )
        )

    # --- Task 8: TOC linkification ---
    if toc_link:
        sections.append(
            dedent(
                f"""\
                ## 任务 8：目录链接化

                下方 JSON 包含目录区域文本（lines）和正文章节 H1 锚点（h1_anchors）。
                请将目录区域转换为可点击跳转的竖排链接列表。

                ### 链接化规则
                1. 解析目录区域内的 "标题／页码" 或 "标题/页码" 条目（全角／与半角/）。
                2. 每个条目独占一行，格式：`- [标题](#ch-N)`
                3. 将条目标题与 h1_anchors 中的 title 匹配，找到对应锚点 anchor。
                   - 匹配策略：精确 → 去空格 → 子串 → 前缀
                   - OCR 可能导致轻微差异（如多空格、错字），尽量匹配
                4. 无页码条目（如 "第十七卷大荒北经"）仍保留为链接。
                5. 未匹配到锚点的条目保留为纯文本 `- 标题`（无链接）。
                6. "山经"/"海经" 等分类标签作为粗体分隔行 `- **山经**`。
                7. 过滤噪声：目录区域内的散文段落、图片引用 `![...]`、
                   fenced div 标记 `:::` 不是条目。
                8. 输出 replacement 为完整替换内容（含 `::: {{.toc-list}}` 和 `:::` 标记），
                   用 \\n 分隔行。start_line 和 end_line 原样返回。

                ### 输出示例
                replacement 内容示例：
                ```
                ::: {{.toc-list}}
                - **山经**
                - [第一卷南山经](#ch-1)
                - [第二卷西山经](#ch-2)
                - **海经**
                - [第六卷海外南经](#ch-6)
                :::
                ```

                ### 输入数据
                {json.dumps(toc_link, ensure_ascii=False, indent=2)}
                """
            )
        )

    # --- Task 9: Decoration identification (multimodal) ---
    if deco_candidates:
        deco_payload = []
        for cand in deco_candidates:
            deco_payload.append(
                {
                    "id": cand["id"],
                    "image_path": cand["image_path"],
                    "nearby_title": cand["nearby_title"],
                    "alt_text": cand.get("alt_text", ""),
                }
            )
        sections.append(
            dedent(
                f"""\
                ## 任务 9：装饰识别（多模态）

                下方 JSON 列出紧邻章节标题的图片候选。请结合附图判断每张图的角色：
                - `decoration`：装饰花纹、几何图标、章节分隔花纹 → **删除**
                - `chapter_illustration`：章首插图、人物速写、章首大图 → 保留
                - `functional_image`：二维码、条形码、ISBN 码 → 保留
                - `other`：其他内容图 → 保留

                ### 判定规则
                1. 看图本身：装饰花纹通常对称、单色、几何化；插图有内容（人物/场景）。
                2. 看上下文：周围 OCR 文本有「扫码/二维码」→ functional_image。
                3. 不确定时一律判 `other`（保守策略，宁可漏删不误删）。
                4. 只输出需要删除的 `decoration` 条目，其他类型不必输出。
                5. 每张候选图的 `image_path` 字段对应附图中的文件名。

                ### 输入数据
                {json.dumps(deco_payload, ensure_ascii=False, indent=2)}
                """
            )
        )

    # --- Image attachment note (multimodal) ---
    review_images = issues.get("review_images", [])
    deco_review_images = issues.get("decoration_review_images", [])
    if review_images or deco_review_images:
        img_lines = []
        idx = 0
        for img in review_images:
            idx += 1
            filename = Path(img["path"]).name
            img_lines.append(f"| {idx} | {filename} | 第{img['page_index']}页 |")
        for img in deco_review_images:
            idx += 1
            filename = Path(img["path"]).name
            img_lines.append(f"| {idx} | {filename} | 装饰候选 |")
        sections.append(
            dedent(
                f"""\
                ## 附图说明

                本次审查附带 {idx} 张图片（按顺序提供）：
                - 页面截图：用于辅助判断低置信度文本和标题问题
                - 装饰候选图：用于任务 9 的装饰识别

                | 序号 | 文件名 | 用途 |
                |------|--------|------|
                {chr(10).join(img_lines)}

                低置信度文本和标题条目中的 `page_index` 字段对应页面截图。
                装饰候选条目中的 `image_path` 字段对应装饰候选图文件名。
                """
            )
        )

    # --- Output format ---
    sections.append(
        dedent(
            """\
            ## 输出格式

            返回单个 JSON 对象（不要包含代码块标记）：

            {
              "low_confidence_fixes": [
                {"id": "LC-1", "corrected": "修正后的文本 或 [UNCLEAR]"}
              ],
              "title_fixes": [
                {"id": "T-1", "corrected": "修正后的标题", "action": "replace"},
                {"id": "T-2", "corrected": "合并后的完整标题", "action": "merge"}
              ],
              "metadata": {
                "title": "书名 或 null",
                "author": "作者 或 null",
                "publisher": "出版社 或 null",
                "date": "出版日期 或 null"
              },
              "paragraph_fixes": [
                {"id": "P-1", "action": "merge", "merged_text": "合并后的段落"}
              ],
              "chapter_fixes": [
                {"id": "CS-1", "action": "delete", "reason": "空白章节"},
                {"id": "CS-2", "action": "promote", "new_level": 2, "reason": "跳级标题提升为H2"}
              ],
              "toc_fixes": [
                {"line": 18, "old_title": "旧标题", "new_title": "新标题"}
              ],
              "book_structure_fixes": {
                "ai_verified": true,
                "ai_notes": "结构合理 或修正建议"
              },
              "toc_linkification": {
                "start_line": 17,
                "end_line": 30,
                "replacement": "::: {.toc-list}\\n- [标题](#ch-N)\\n:::"
              },
              "decoration_fixes": [
                {"id": "DC-1", "action": "delete"}
              ]
            }

            注意：
            - 低置信度修正需满足约束，不确定时输出 "[UNCLEAR]"
            - title_fixes 中只包含需要修改的标题，正常标题不要列出
            - action "replace" 替换标题行，"merge" 合并标题和下一行
            - metadata 中无需修改的字段设为 null
            - 各 fixes 数组可为空，只包含需要修改的条目
            - toc_linkification 无需修改时设为 null
            - decoration_fixes 只包含判为 decoration 需删除的条目，
              action 固定为 "delete"；其他类型（插图/功能图/其他）不输出
            """
        )
    )

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# 3. Applier
# ---------------------------------------------------------------------------


def apply_markdown_corrections(
    md_path: Path,
    meta_path: Path,
    corrections: dict,
    issues: dict,
) -> BookMetadata:
    """Apply AI corrections to book.md and meta.md.

    Corrections are applied by line number (descending order to preserve
    indices). Low-confidence blocks are replaced with corrected text or
    marked [UNCLEAR]. Title fixes replace or merge title lines. Metadata
    updates are written to meta.md.

    Returns the updated BookMetadata.
    """
    lines = md_path.read_text(encoding="utf-8").splitlines()

    # Build a map: line_index -> new_content (or None to delete line)
    line_fixes: dict[int, str | None] = {}

    # --- Low-confidence fixes ---
    lc_by_id = {item["id"]: item for item in issues.get("low_confidence_texts", [])}
    for fix in corrections.get("low_confidence_fixes", []):
        item = lc_by_id.get(fix.get("id", ""))
        if item is None:
            continue
        line_idx = item["line"]
        corrected = fix.get("corrected", "").strip()

        if "[UNCLEAR]" in corrected:
            # Keep the low-confidence marker, add [UNCLEAR] for manual review
            line_fixes[line_idx] = f"{_LOW_CONF_PREFIX}{item['original_text']} [UNCLEAR]"
        else:
            # Validate against constraints
            constraints = CorrectionConstraints(
                original_text=item["original_text"],
                max_length=item["constraints"]["max_length"],
                preserved_chars=item["constraints"]["preserved_chars"],
                max_edit_distance=item["constraints"]["max_edit_distance"],
                wildcard_count=item["constraints"]["wildcard_count"],
                context_before=item.get("context_before", ""),
                context_after=item.get("context_after", ""),
            )
            is_valid, reason = validate_correction(
                item["original_text"], corrected, constraints
            )
            if is_valid:
                # Replace blockquote with plain paragraph
                line_fixes[line_idx] = corrected
            else:
                # Failed validation: mark for manual review
                line_fixes[line_idx] = f"{_LOW_CONF_PREFIX}{item['original_text']}[需校对]"

    # --- Title fixes ---
    title_by_id = {item["id"]: item for item in issues.get("title_candidates", [])}
    for fix in corrections.get("title_fixes", []):
        item = title_by_id.get(fix.get("id", ""))
        if item is None:
            continue
        line_idx = item["line"]
        corrected_title = fix.get("corrected", "").strip()
        action = fix.get("action", "replace")

        if not corrected_title:
            continue

        # Preserve anchor if present in original
        anchor = item.get("anchor")
        # For merge action, the anchor may be on the continuation line
        # (OCR text contains \n, so to_markdown emits "# title\n——subtitle {#ch-N}").
        # Extract it before deleting the continuation line.
        if action == "merge" and line_idx + 1 < len(lines):
            next_line = lines[line_idx + 1]
            next_anchor_match = re.search(r"\{#(ch-\d+)\}", next_line)
            if next_anchor_match and not anchor:
                anchor = next_anchor_match.group(1)
        anchor_suffix = f" {{#{anchor}}}" if anchor else ""
        # Use the original heading level (H1-H6). Default to 1 for backward
        # compatibility with items collected before `level` was added.
        level = item.get("level", 1)
        hashes = "#" * level
        line_fixes[line_idx] = f"{hashes} {corrected_title}{anchor_suffix}"

        # Merge: delete the next line (continuation)
        if action == "merge":
            if line_idx + 1 < len(lines):
                line_fixes[line_idx + 1] = None  # Delete next line

    # --- Paragraph fixes (merge short line clusters) ---
    paragraph_by_id = {
        item["id"]: item for item in issues.get("paragraph_issues", [])
    }
    for fix in corrections.get("paragraph_fixes", []):
        item = paragraph_by_id.get(fix.get("id", ""))
        if item is None:
            continue
        if fix.get("action") != "merge":
            continue
        merged_text = fix.get("merged_text", "").strip()
        if not merged_text:
            continue
        start = item["start_line"]
        end = item["end_line"]
        line_fixes[start] = merged_text
        for k in range(start + 1, end + 1):
            line_fixes[k] = None

    # --- Chapter structure fixes ---
    # DISABLED: Chapter structure modifications (delete/demote/promote) are
    # intentionally NOT applied. They change heading levels and delete
    # "empty" chapters, which breaks the strict page-by-page ordering
    # between source PDF and output EPUB. Heading levels are finalized
    # in `to_markdown` via `infer_title_levels`; AI should not restructure
    # the chapter hierarchy post-hoc.
    # To re-enable: uncomment the block below.
    # chapter_by_id = {
    #     item["id"]: item
    #     for item in issues.get("chapter_structure_issues", [])
    # }
    # for fix in corrections.get("chapter_fixes", []):
    #     item = chapter_by_id.get(fix.get("id", ""))
    #     if item is None:
    #         continue
    #     action = fix.get("action", "")
    #     line_idx = item["line"]
    #     if action == "delete":
    #         line_fixes[line_idx] = None
    #         if line_idx > 0 and lines[line_idx - 1].strip() == "::: {.chapter}":
    #             line_fixes[line_idx - 1] = None
    #         if (
    #             line_idx + 1 < len(lines)
    #             and lines[line_idx + 1].strip() == ":::"
    #         ):
    #             line_fixes[line_idx + 1] = None
    #     elif action in ("demote", "promote"):
    #         new_level = fix.get("new_level", 2)
    #         title = item["title"]
    #         hashes = "#" * new_level
    #         line_fixes[line_idx] = f"{hashes} {title}"

    # --- TOC fixes ---
    for fix in corrections.get("toc_fixes", []):
        line_idx = fix.get("line")
        if not isinstance(line_idx, int) or line_idx >= len(lines):
            continue
        new_title = fix.get("new_title", "").strip()
        if not new_title:
            continue
        old_line = lines[line_idx]
        page_match = re.search(
            r"[.．…]+\s*\d+$|／\d+$|/\d+$", old_line.strip()
        )
        page_suffix = page_match.group(0) if page_match else ""
        line_fixes[line_idx] = f"{new_title}{page_suffix}"

    # --- Decoration fixes (AI path: multimodal decoration identification) ---
    # AI identifies images near chapter titles as decorations and returns
    # {id, action: "delete"} for each. We delete the corresponding image
    # reference line. Non-decoration classifications (chapter_illustration,
    # functional_image, other) are NOT returned — only deletions.
    deco_by_id = {
        item["id"]: item for item in issues.get("decoration_candidates", [])
    }
    for fix in corrections.get("decoration_fixes", []):
        item = deco_by_id.get(fix.get("id", ""))
        if item is None:
            continue
        if fix.get("action") != "delete":
            continue
        line_fixes[item["line"]] = None  # delete the image reference line

    # --- TOC linkification (AI path) ---
    # AI returns a `toc_linkification` object with `start_line`, `end_line`,
    # and `replacement` (multi-line string). We replace the original TOC
    # region (start_line..end_line, exclusive) with the replacement lines.
    # The replacement is expected to use `::: {.toc-list}` fenced div +
    # `[title](#ch-N)` links, matching the fallback module's format so the
    # same CSS applies and idempotency holds across AI re-runs.
    toc_link_fix = corrections.get("toc_linkification")
    toc_skip_range: tuple[int, int] | None = None
    toc_insert_at: int | None = None
    toc_insert_lines: list[str] | None = None
    if isinstance(toc_link_fix, dict):
        start = toc_link_fix.get("start_line")
        end = toc_link_fix.get("end_line")
        replacement = toc_link_fix.get("replacement", "")
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start < end <= len(lines)
            and isinstance(replacement, str)
            and replacement.strip()
        ):
            toc_skip_range = (start, end)
            toc_insert_at = start
            toc_insert_lines = replacement.splitlines()

    # Apply line fixes in descending order to preserve indices
    new_lines: list[str] = []
    for i, line in enumerate(lines):
        if toc_skip_range and toc_skip_range[0] <= i < toc_skip_range[1]:
            if i == toc_insert_at and toc_insert_lines:
                new_lines.extend(toc_insert_lines)
                toc_insert_lines = None
            continue
        if i in line_fixes:
            fixed = line_fixes[i]
            if fixed is not None:
                new_lines.append(fixed)
            # If None, skip (delete line)
        else:
            new_lines.append(line)

    md_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # --- Metadata fixes ---
    meta = read_meta_yaml(meta_path) if meta_path.exists() else BookMetadata()
    meta_corrections = corrections.get("metadata", {})
    if meta_corrections:
        for key in ("title", "author", "lang", "date", "publisher", "rights"):
            value = meta_corrections.get(key)
            if isinstance(value, str) and value.strip():
                if hasattr(meta, key):
                    setattr(meta, key, value.strip())
        write_meta_yaml(meta, meta_path.parent)

    # --- Book structure fixes (update ai_verified / ai_notes) ---
    bs_fixes = corrections.get("book_structure_fixes", {})
    if bs_fixes and meta.book_structure is not None:
        if "ai_verified" in bs_fixes:
            meta.book_structure.ai_verified = bool(bs_fixes["ai_verified"])
        ai_notes = bs_fixes.get("ai_notes")
        if isinstance(ai_notes, str) and ai_notes.strip():
            meta.book_structure.ai_notes = ai_notes.strip()
        write_meta_yaml(meta, meta_path.parent)

    return meta


# ---------------------------------------------------------------------------
# 4. Batched review (split large reviews into multiple API calls)
# ---------------------------------------------------------------------------

# Maximum items per API call. Empirically, the sensenova flash-lite model
# truncates responses at ~12 title fixes (batch of 15 failed, batch of 12
# succeeded). Setting batch size to 5 gives a safe margin for any model,
# trading more API calls for reliability. With 42 titles → 9 batches.
_TITLE_BATCH_SIZE = 5
_LOW_CONF_BATCH_SIZE = 5


def build_review_batches(
    issues: dict,
    max_images: int = 0,
    multimodal: bool = False,
) -> list[dict]:
    """Split ``issues`` into multiple smaller sub-issues for batched API calls.

    Returns a list of batch dicts, each with:
      - ``issues``: a filtered issues dict (only the tasks this batch covers)
      - ``image_paths``: list of Path for this batch (empty for text-only)
      - ``label``: human-readable batch name for logging

    Batching strategy:
      1. **Low-confidence texts** — split into chunks of
         ``_LOW_CONF_BATCH_SIZE``, each with matching page screenshots
      2. **Title review** — split into chunks of ``_TITLE_BATCH_SIZE`` (text-only)
      3. **Decoration identification** — multimodal, with candidate images
      4. **Structure tasks** — metadata + paragraph + chapter + TOC +
         linkification + book_structure (text-only)

    Each batch produces a subset of correction keys. The caller merges
    all batch responses via ``merge_corrections`` before applying.
    """
    batches: list[dict] = []

    low_conf = issues.get("low_confidence_texts", [])
    titles = issues.get("title_candidates", [])
    deco_candidates = issues.get("decoration_candidates", [])
    deco_images = issues.get("decoration_review_images", [])

    # --- Batch 1..N: Low-confidence texts (chunked, text-only) ---
    # Low-confidence batches are text-only: page screenshots cause the model
    # to generate lengthy image descriptions, consuming the entire output
    # token budget. Text context alone is sufficient for OCR correction.
    # Multimodal is reserved for the decoration identification batch.
    if low_conf:
        total_lc_batches = (len(low_conf) + _LOW_CONF_BATCH_SIZE - 1) // _LOW_CONF_BATCH_SIZE
        for start in range(0, len(low_conf), _LOW_CONF_BATCH_SIZE):
            chunk = low_conf[start:start + _LOW_CONF_BATCH_SIZE]
            batch_num = start // _LOW_CONF_BATCH_SIZE + 1

            batches.append({
                "issues": {
                    "low_confidence_texts": chunk,
                    "title_candidates": [],
                    "metadata": {"current": {}},
                    "review_images": [],
                },
                "image_paths": [],
                "label": f"low-confidence {batch_num}/{total_lc_batches} ({len(chunk)} items)",
            })

    # --- Batch 2..N: Title review (chunked) ---
    for start in range(0, len(titles), _TITLE_BATCH_SIZE):
        chunk = titles[start:start + _TITLE_BATCH_SIZE]
        batch_num = start // _TITLE_BATCH_SIZE + 1
        total_batches = (len(titles) + _TITLE_BATCH_SIZE - 1) // _TITLE_BATCH_SIZE
        batches.append({
            "issues": {
                "low_confidence_texts": [],
                "title_candidates": chunk,
                "metadata": {"current": {}},
                "review_images": [],
            },
            "image_paths": [],
            "label": f"titles {batch_num}/{total_batches} ({len(chunk)} items)",
        })

    # --- Batch: Decoration identification (multimodal) ---
    if deco_candidates:
        batch_images = [item["path"] for item in deco_images] if multimodal else []
        batches.append({
            "issues": {
                "low_confidence_texts": [],
                "title_candidates": [],
                "metadata": {"current": {}},
                "decoration_candidates": deco_candidates,
                "decoration_review_images": deco_images if batch_images else [],
                "review_images": [],
            },
            "image_paths": batch_images,
            "label": f"decoration ({len(deco_candidates)} candidates)",
        })

    # --- Batch: Structure tasks (metadata + paragraph + chapter + TOC + etc.) ---
    structure_issues: dict = {
        "low_confidence_texts": [],
        "title_candidates": [],
        "metadata": issues.get("metadata", {"current": {}}),
        "paragraph_issues": issues.get("paragraph_issues", []),
        "chapter_structure_issues": issues.get("chapter_structure_issues", []),
        "toc_issues": issues.get("toc_issues", []),
        "toc_linkification": issues.get("toc_linkification"),
        "book_structure": issues.get("book_structure"),
        "review_images": [],
    }
    has_structure_content = any([
        structure_issues["paragraph_issues"],
        structure_issues["chapter_structure_issues"],
        structure_issues["toc_issues"],
        structure_issues["toc_linkification"],
        structure_issues["book_structure"] is not None,
    ])
    # Always include this batch (metadata verification should run even
    # when no other structure issues exist).
    batches.append({
        "issues": structure_issues,
        "image_paths": [],
        "label": f"structure{' + metadata' if has_structure_content else ' (metadata)'}",
    })

    return batches


# Keys whose values are lists (concatenated during merge).
_LIST_CORRECTION_KEYS = frozenset({
    "low_confidence_fixes",
    "title_fixes",
    "paragraph_fixes",
    "chapter_fixes",
    "toc_fixes",
    "decoration_fixes",
})


def merge_corrections(results: list[dict]) -> dict:
    """Merge multiple AI correction dicts into one.

    List-valued keys (``low_confidence_fixes``, ``title_fixes``, etc.) are
    concatenated. Scalar/dict keys (``metadata``, ``toc_linkification``,
    ``book_structure_fixes``) take the last non-null/non-empty value.

    ``results`` entries that are ``None`` or not dicts are silently skipped
    (a failed API call for one batch should not block the others).
    """
    merged: dict = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        for key, value in result.items():
            if key in _LIST_CORRECTION_KEYS:
                if isinstance(value, list):
                    merged.setdefault(key, []).extend(value)
            elif key in ("metadata", "book_structure_fixes"):
                if isinstance(value, dict) and value:
                    existing = merged.get(key)
                    if isinstance(existing, dict):
                        existing.update(value)
                    else:
                        merged[key] = value
            elif key == "toc_linkification":
                if isinstance(value, dict) and value:
                    merged[key] = value
            else:
                merged[key] = value
    return merged


__all__ = [
    "collect_markdown_issues",
    "build_review_prompt",
    "build_review_batches",
    "merge_corrections",
    "apply_markdown_corrections",
]
