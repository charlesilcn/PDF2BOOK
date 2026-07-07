"""TOC linkification: convert OCR'd "标题／页码" paragraphs into clickable lists.

This is the rule-based fallback for when AI review is disabled or did not
produce a ``::: {.toc-list}`` block. It scans ``book.md`` for the TOC region
(between a "目录"/"CONTENTS" heading and the first H1 chapter), parses
``标题／页码`` entries, matches each title against H1 anchors
(``# title {#ch-N}``), and emits a fenced-div list of clickable links::

    ::: {.toc-list}
    - [第一卷南山经](#ch-1)
    - [第二卷西山经](#ch-2)
    - **海经**
    - [第六卷海外南经](#ch-6)
    :::

Idempotent: if ``::: {.toc-list}`` is already present, the text is returned
unchanged so AI-generated output is preserved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Chapter anchor: `#` or `###` heading with `{#ch-N}` -> captures (title, "ch-N").
_CHAPTER_ANCHOR_RE = re.compile(r"^#{1,3}\s+(.+?)\s*\{#(ch-\d+)\}\s*$", re.MULTILINE)

# First chapter line (H1-H3 with {#ch-N} anchor, used as TOC region's upper bound).
_FIRST_CHAPTER_RE = re.compile(r"^#{1,3}\s+.+\{#ch-\d+\}\s*$", re.MULTILINE)

# Heading line starting with "目录" or "CONTENTS" (any level).
_TOC_HEADING_RE = re.compile(
    r"^#{1,6}\s+(?:.*(?:目录|目\s*录|CONTENTS|Contents|contents).*)\s*$",
    re.MULTILINE,
)

# Entry separator: full-width ／ or half-width / optionally followed by digits.
_ENTRY_SPLIT_RE = re.compile(r"[／/]\s*(\d+)?")

# Volume prefix pattern: "第一卷", "第十六卷", etc. Used to split crammed
# entries that have no page numbers (e.g. "第十六卷大荒西经第十七卷大荒北经").
_VOLUME_PREFIX_RE = re.compile(r"(?=第[一二三四五六七八九十百零\d]+卷)")

# Heading line inside the TOC region (e.g. "### 海经"). Captures the heading text.
_INNER_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")

# Fenced-div sentinel for already-linkified TOC.
_TOC_LIST_SENTINEL = "::: {.toc-list}"

# Category labels (short classifier words inside the TOC region that are not
# chapter entries). Treated as bold separator rows in the rendered list.
_CATEGORY_LABELS = frozenset({"山经", "海经"})


@dataclass
class TocEntry:
    """One parsed TOC entry.

    ``title`` is the entry text (e.g. "第一卷南山经").
    ``page`` is the page number string (e.g. "2") or None when absent.
    ``is_category`` marks classifier labels like "山经"/"海经" that render as
    bold separator rows instead of links.
    """

    title: str
    page: str | None = None
    is_category: bool = False


def linkify_toc_entries(md_text: str) -> str:
    """Convert the TOC region in ``md_text`` into a clickable vertical list.

    Returns the text unchanged when:
      - No TOC heading ("目录"/"CONTENTS") is found.
      - No H1 chapter anchor (``# ... {#ch-N}``) exists to link to.
      - The text already contains ``::: {.toc-list}`` (idempotent).
    """
    if _TOC_LIST_SENTINEL in md_text:
        return md_text

    anchors = _collect_chapter_anchors(md_text)
    if not anchors:
        return md_text

    region = _find_toc_region(md_text)
    if region is None:
        return md_text

    start, end = region
    region_lines = md_text.splitlines()[start:end]
    entries = _parse_toc_entries(region_lines)
    if not entries:
        return md_text

    rendered = _render_toc_list(entries, anchors)
    lines = md_text.splitlines(keepends=True)
    head = "".join(lines[:start])
    tail = "".join(lines[end:])
    new_text = head + rendered + "\n" + tail
    return new_text


def _collect_chapter_anchors(md_text: str) -> list[tuple[str, str]]:
    """Return ``[(title, "#ch-N"), ...]`` for every H1-H3 chapter anchor."""
    out: list[tuple[str, str]] = []
    for m in _CHAPTER_ANCHOR_RE.finditer(md_text):
        title = m.group(1).strip()
        anchor = "#" + m.group(2)
        out.append((title, anchor))
    return out


def _find_toc_region(md_text: str) -> tuple[int, int] | None:
    """Locate the TOC region as ``(start_line, end_line)`` (0-based, half-open).

    Start: the first line matching a TOC heading ("目录"/"CONTENTS").
    End: the first chapter line (H1-H3 with ``{#ch-N}``) at or after start.
    Returns None when either bound is missing or end <= start.
    """
    lines = md_text.splitlines()
    toc_match = _TOC_HEADING_RE.search(md_text)
    if toc_match is None:
        return None
    start = md_text[: toc_match.start()].count("\n")
    end = None
    for i in range(start, len(lines)):
        if _FIRST_CHAPTER_RE.match(lines[i]):
            end = i
            break
    if end is None or end <= start:
        return None
    return (start, end)


def _parse_toc_entries(region_lines: list[str]) -> list[TocEntry]:
    """Parse TOC region lines into a list of :class:`TocEntry`.

    Processes line by line to avoid cross-line merging:
      - Heading lines (``### 目录``) are skipped; ``### 海经`` becomes a category.
      - ``CONTENTS`` and pure-digit lines are noise.
      - Entry lines are split by ``／digits``; crammed titles without page
        numbers are further split by the ``第N卷`` prefix pattern.
    """
    entries: list[TocEntry] = []
    for line in region_lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Heading inside TOC region.
        heading_match = _INNER_HEADING_RE.match(stripped)
        if heading_match:
            heading_text = heading_match.group(1).strip()
            # Skip TOC header headings (目录/CONTENTS).
            if "目录" in heading_text or "CONTENTS" in heading_text.upper():
                continue
            # Category labels (海经/山经) become bold separator rows.
            if heading_text in _CATEGORY_LABELS:
                entries.append(
                    TocEntry(title=heading_text, page=None, is_category=True)
                )
            # Other headings inside TOC — skip.
            continue

        # Non-heading noise.
        if _is_noise(stripped):
            continue

        # Category labels as plain text (no heading marker).
        if stripped in _CATEGORY_LABELS:
            entries.append(TocEntry(title=stripped, page=None, is_category=True))
            continue

        # Entry line: split by ／digits and extract titles.
        entries.extend(_parse_entry_line(stripped))
    return entries


def _parse_entry_line(line: str) -> list[TocEntry]:
    """Parse one TOC entry line into one or more :class:`TocEntry`.

    A line like ``第一卷南山经／2第二卷西山经／16`` splits into two entries
    with pages "2" and "16". A line like ``第十六卷大荒西经第十七卷大荒北经``
    (no page numbers) splits by the ``第N卷`` prefix pattern.
    """
    parts = _ENTRY_SPLIT_RE.split(line)
    out: list[TocEntry] = []
    # parts = [title0, page0, title1, page1, ..., trailing_title]
    # Iterate pairs of (title, page).
    for i in range(0, len(parts) - 1, 2):
        title = parts[i].strip()
        if not title or _is_noise(title):
            continue
        page_raw = parts[i + 1] if i + 1 < len(parts) else None
        page = page_raw.strip() if page_raw else None
        page = page or None
        for sub_title in _split_volume_entries(title):
            if _is_noise(sub_title):
                continue
            out.append(TocEntry(title=sub_title, page=page))
    # Trailing fragment (after the last ／digits separator) with no page.
    if len(parts) % 2 == 1:
        title = parts[-1].strip()
        if title and not _is_noise(title):
            for sub_title in _split_volume_entries(title):
                if _is_noise(sub_title):
                    continue
                out.append(TocEntry(title=sub_title, page=None))
    return out


def _split_volume_entries(title: str) -> list[str]:
    """Split a title containing multiple ``第N卷`` prefixes into individual titles.

    ``"第十六卷大荒西经第十七卷大荒北经"`` →
    ``["第十六卷大荒西经", "第十七卷大荒北经"]``.

    Returns ``[title]`` unchanged when no split is needed (single entry or
    no ``第N卷`` prefix).
    """
    if "第" not in title:
        return [title]
    # Lookahead split keeps the delimiter at the start of each fragment.
    fragments = _VOLUME_PREFIX_RE.split(title)
    result = [f.strip() for f in fragments if f.strip()]
    # If splitting produced only one fragment, the title had no volume prefix
    # pattern; return it unchanged.
    if len(result) <= 1:
        return [title] if title.strip() else []
    return result


def _is_noise(text: str) -> bool:
    """True for fragments that are not real TOC entries."""
    if not text:
        return True
    if text.upper() == "CONTENTS":
        return True
    if text in {"目录", "目 录"}:
        return True
    if text.isdigit():
        return True
    # Image references and fenced-div markers leaked into the TOC region.
    if text.startswith("![") or text.startswith(":::"):
        return True
    # TOC entry lines contain ／digits or /digits page-number patterns.
    # These are not noise even when long (multiple entries crammed on one line).
    if _ENTRY_SPLIT_RE.search(text):
        return False
    # Long prose (not a chapter title). Chapter titles are short (≤30 chars)
    # and usually start with "第N卷" or a section keyword. Prose paragraphs
    # leaked from OCR layout are long and don't match the volume pattern.
    if len(text) > 30 and not _VOLUME_PREFIX_RE.search(text):
        return True
    return False


def _match_title(title: str, anchors: list[tuple[str, str]]) -> str | None:
    """Return the anchor for ``title`` via a multi-level match strategy.

    Strategy (first hit wins):
      1. Exact match.
      2. Ignore-whitespace match.
      3. Substring either way.
      4. Anchor title starts with ``title[:4]`` (OCR truncation tolerance).
    """
    if not title:
        return None
    norm = title.replace(" ", "")
    # 1. Exact.
    for anchor_title, anchor in anchors:
        if title == anchor_title:
            return anchor
    # 2. Ignore whitespace.
    for anchor_title, anchor in anchors:
        if norm == anchor_title.replace(" ", ""):
            return anchor
    # 3. Substring either way.
    for anchor_title, anchor in anchors:
        if title in anchor_title or anchor_title in title:
            return anchor
    # 4. Prefix (first 4 chars) — tolerates OCR truncation.
    prefix = title[:4]
    if prefix:
        for anchor_title, anchor in anchors:
            if anchor_title.startswith(prefix):
                return anchor
    return None


def _render_toc_list(entries: list[TocEntry], anchors: list[tuple[str, str]]) -> str:
    """Render entries as a fenced-div markdown list with links to anchors."""
    lines: list[str] = ["::: {.toc-list}"]
    for entry in entries:
        if entry.is_category:
            lines.append(f"- **{entry.title}**")
            continue
        anchor = _match_title(entry.title, anchors)
        if anchor:
            lines.append(f"- [{entry.title}]({anchor})")
        else:
            lines.append(f"- {entry.title}")
    lines.append(":::")
    return "\n".join(lines)


__all__ = ["linkify_toc_entries", "TocEntry"]
