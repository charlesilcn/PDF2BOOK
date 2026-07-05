"""Title level inference (T8).

Per decision D1 in the implementation plan, PP-StructureV3 does NOT
serialize `title_level` into its JSON output (only into its markdown
rendering, where every `paragraph_title` becomes `##`). That rendering is
useless for literary books that need "第一章 → H1, 第一节 → H2".

This module is the sole authority for title levels. It assigns
`Element.inferred_level` (1=H1, 2=H2, 3=H3) using a priority cascade:

  1. Literary chapter keywords (第X章/回/卷/篇, Chapter N) -> H1
  2. Section keywords (第X节)                                -> H2
  3. Numeric numbering (1.1.1)                               -> depth = number of dots + 1
  4. Font-size ratio vs page median text height              -> >=2.0: H1, >=1.4: H2
  5. Fallback                                                -> H3

After assignment, `enforce_monotonic` walks titles in reading order and
demotes any level-k title whose k-1 ancestor is missing (down to the
highest valid ancestor). This prevents TOC jumps like H1 → H3.

Only elements whose type is in TITLE_LABELS and not `dropped` are
considered. Mutates `Element.inferred_level` in place; returns the same
list for chaining.
"""

from __future__ import annotations

import re
from pathlib import Path
from statistics import median

from pdf2book.config import PostprocessConfig
from pdf2book.ocr.models import PageResult
from pdf2book.postprocess.images import IMAGE_LABELS

# Element types that carry title semantics.
TITLE_LABELS = frozenset({"doc_title", "paragraph_title", "content_title"})

# PP element types whose `text` carries raw HTML (PP's table recognizer output).
TABLE_LABELS = frozenset({"table"})

# Block-level formula types; rendered as $$ ... $$ display math.
FORMULA_LABELS = frozenset({"formula", "display_formula"})

# 第X章/回/卷/篇 -> H1. "节" is excluded here (handled by _SEC_RE -> H2).
_CHAP_RE = re.compile(r"^第[一二三四五六七八九十百千0-9]+[章回卷篇]")
_SEC_RE = re.compile(r"^第[一二三四五六七八九十百千0-9]+节")
_CHAP_EN_RE = re.compile(r"^Chapter\s+[IVX0-9]+", re.IGNORECASE)
# "1", "1.1", "1.1.1" -> depth = dot_count + 1.
_NUMBERING_RE = re.compile(r"^(\d+(?:\.\d+)*)\s*\S*")

# Font-size ratio thresholds (relative to page median text line height).
_H1_RATIO = 2.0
_H2_RATIO = 1.4

# A title's text is usually short.
_MAX_TITLE_LEN = 60


def infer_title_levels(
    pages: list[PageResult], cfg: PostprocessConfig
) -> list[PageResult]:
    """Assign `Element.inferred_level` to title candidates.

    Mutates elements in place; returns the same list for chaining.
    No-op when `cfg.infer_title_level` is False.
    """
    if not cfg.infer_title_level or not pages:
        return pages

    cands = _collect_candidates(pages)
    if not cands:
        return pages

    page_median = {
        page.page_index: _median_text_height(page) for page in pages
    }

    for page, el in cands:
        el.inferred_level = _classify(el, page, page_median.get(page.page_index))

    _enforce_monotonic(cands)
    return pages


def _collect_candidates(pages: list[PageResult]):
    """Return [(page, element)] for title-typed, non-dropped elements, in order."""
    cands = []
    for page in pages:
        for el in page.elements:
            if el.type in TITLE_LABELS and not el.dropped:
                cands.append((page, el))
    return cands


def _median_text_height(page: PageResult) -> float | None:
    heights = [
        e.bbox.height
        for e in page.elements
        if e.type == "text" and not e.dropped and e.bbox.height > 0
    ]
    if not heights:
        return None
    return float(median(heights))


def _classify(el, page: PageResult, med_h: float | None) -> int:
    text = el.text.strip()

    # 1. Literary keywords (highest priority).
    if _CHAP_RE.match(text) or _CHAP_EN_RE.match(text):
        return 1
    if _SEC_RE.match(text):
        return 2

    # 2. Numeric numbering: 1.1.1 -> depth 3.
    depth = _numbering_depth(text)
    if depth is not None:
        return depth

    # 3. Font-size ratio vs page median text height.
    if med_h and med_h > 0:
        ratio = el.bbox.height / med_h
        if ratio >= _H1_RATIO:
            return 1
        if ratio >= _H2_RATIO:
            return 2

    # 4. Fallback.
    return 3


def _numbering_depth(text: str) -> int | None:
    """Extract numeric outline depth from a title like '1.1.1 Title' -> 3.

    Returns None if the text does not start with a numbering prefix.
    """
    m = _NUMBERING_RE.match(text)
    if not m:
        return None
    return m.group(1).count(".") + 1


def _enforce_monotonic(cands) -> None:
    """Demote any level-k title whose k-1 ancestor is missing.

    Walks titles in reading order (cands is already ordered). Maintains a
    `seen` set of levels that have appeared. If a title claims level k but
    k-1 has not been seen, demote it step by step until k-1 exists or k=1.
    """
    seen: set[int] = set()
    for _page, el in cands:
        lvl = el.inferred_level
        if lvl is None:
            continue
        while lvl > 1 and (lvl - 1) not in seen:
            lvl -= 1
            el.inferred_level = lvl
        seen.add(lvl)


# ---------------------------------------------------------------------------
# Markdown assembly (T9)
# ---------------------------------------------------------------------------

# Default header depth when a title element has no inferred_level (e.g. T8
# disabled, or a title survived after enforce_monotonic left it unset).
_DEFAULT_LEVEL = 3


def to_markdown(
    pages: list[PageResult],
    meta: dict | None,
    work_dir: Path,
) -> Path:
    """Assemble a single `book.md` from the post-processed PageResult list.

    Iterates pages in `page_index` order; within each page, iterates live
    (non-dropped) elements sorted by `order_index`. Rendering rules:

      - title elements (TITLE_LABELS) -> ATX headers `#`/`##`/`###` based on
        `inferred_level` (default H3). Each H1 gets an explicit `{#ch-N}`
        attribute so repeated chapter titles cannot collide on Pandoc's
        auto-generated anchor.
      - image/figure/chart -> `![](rel_path)` (rel_path is set by
        `images.extract_images` to `images/pN_eM.png`).
      - table -> raw HTML verbatim (PP's table recognizer emits HTML).
      - formula/display_formula -> `$$ \\n text \\n $$` display math.
      - anything else -> a plain paragraph.

    Chapter page breaks are NOT emitted here: Pandoc's `--split-level=1`
    (configured in T10's PandocBuilder) splits H1 into separate EPUB XHTML
    files, which is the Kindle-correct page-break mechanism. A raw LaTeX
    `\\newpage` would be dropped on EPUB output, so it is intentionally
    omitted.

    `meta` (the PDF metadata dict) is accepted for pipeline signature
    symmetry but is not written into the markdown body — EPUB metadata
    (title/author/lang) is authored by T10's `epub/metadata.py` as a
    separate YAML file that Pandoc merges at build time.

    Returns the path to `work_dir/book.md`.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "book.md"

    if not pages:
        out.write_text("", encoding="utf-8")
        return out

    blocks: list[str] = []
    ch_counter = 0

    for page in sorted(pages, key=lambda p: p.page_index):
        live = sorted(
            [e for e in page.elements if not e.dropped],
            key=lambda e: e.order_index,
        )
        for el in live:
            text = el.text.strip()
            if not text:
                continue

            if el.type in TITLE_LABELS:
                level = el.inferred_level or _DEFAULT_LEVEL
                hashes = "#" * max(1, min(level, 6))
                if level == 1:
                    ch_counter += 1
                    blocks.append(f"{hashes} {text} {{#ch-{ch_counter}}}")
                else:
                    blocks.append(f"{hashes} {text}")
                continue

            if el.type in IMAGE_LABELS:
                blocks.append(f"![]({text})")
            elif el.type in TABLE_LABELS:
                blocks.append(text)
            elif el.type in FORMULA_LABELS:
                blocks.append(f"$$\n{text}\n$$")
            else:
                blocks.append(text)

    out.write_text("\n\n".join(blocks).rstrip() + "\n", encoding="utf-8")
    return out
