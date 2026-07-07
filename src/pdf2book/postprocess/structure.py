"""Title level inference (T8).

Per decision D1 in the implementation plan, PP-StructureV3 does NOT
serialize `title_level` into its JSON output (only into its markdown
rendering, where every `paragraph_title` becomes `##`). That rendering is
useless for literary books that need "第一章 → H1, 第一节 → H2".

This module is the sole authority for title levels. It assigns
`Element.inferred_level` (1=H1, 2=H2, 3=H3) using a priority cascade:

  1. Literary chapter keywords (cfg.chapter_patterns) -> H1
     (第X章/回/卷/篇, Chapter N)
  2. Section keywords (cfg.section_patterns) -> H2
     (第X节)
  3. Numeric numbering (1.1.1) -> depth = number of dots + 1
  4. Font-size ratio vs page median text height -> H1 (>=2.0) / H2 (>=1.4)
  5. Fallback -> H3

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
from pdf2book.postprocess.page_classifier import DECORATIVE_TYPES, PageType

# Element types that carry title semantics.
TITLE_LABELS = frozenset({"doc_title", "paragraph_title", "content_title"})

# Page types skipped entirely in `to_markdown` (no text, no image rendered).
# COVER: EPUB cover is set via Pandoc's --epub-cover-image; don't duplicate
#   the page image in the body (would show the cover twice in readers).
# TOC: EPUB has its own navigation TOC (nav.xhtml); the PDF original is
#   redundant and its OCR text is too noisy to typeset.
_SKIP_PAGE_TYPES = frozenset({PageType.COVER, PageType.TOC})

# Decorative pages rendered as PDF page images (bypass OCR layout).
# Excludes COVER (skipped via _SKIP_PAGE_TYPES); frontispiece/copyright/
# illustration have informational/decorative value worth preserving as images.
_IMAGE_RENDER_TYPES = DECORATIVE_TYPES - {PageType.COVER}

# PP element types whose `text` carries raw HTML (PP's table recognizer output).
TABLE_LABELS = frozenset({"table"})

# Block-level formula types; rendered as $$ ... $$ display math.
FORMULA_LABELS = frozenset({"formula", "display_formula"})

# "1.1 Title", "1.1.1 Section" -> depth = dot_count + 1. Requires at least
# one non-numeric character after the number prefix, so pure page numbers
# like "30" or "7" (which are not headings) don't match.
_NUMBERING_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+\S+")

# Font-size ratio thresholds (relative to page median text line height).
_H1_RATIO = 2.0
_H2_RATIO = 1.4

# A title's text is usually short.
_MAX_TITLE_LEN = 60


def infer_title_levels(
    pages: list[PageResult],
    cfg: PostprocessConfig,
    *,
    skip_page_types: frozenset[str] | None = None,
) -> list[PageResult]:
    """Assign `Element.inferred_level` to title candidates.

    Mutates elements in place; returns the same list for chaining.
    No-op when `cfg.infer_title_level` is False.

    ``skip_page_types``: when provided, title candidates on pages whose
    ``page_type`` is in this set are ignored (their ``inferred_level`` is
    left unset). Used to exclude decorative/TOC pages whose OCR-detected
    "titles" (e.g. a misread "目录" → "水*CONTENTS") would pollute the
    level hierarchy. Callers should re-invoke after page classification
    so this filter takes effect.
    """
    if not cfg.infer_title_level or not pages:
        return pages

    # Compile user-configurable patterns (replaces former hardcoded regexes).
    chapter_res = [re.compile(p, re.IGNORECASE) for p in cfg.chapter_patterns]
    section_res = [re.compile(p, re.IGNORECASE) for p in cfg.section_patterns]

    cands = _collect_candidates(pages, skip_page_types)
    if not cands:
        return pages

    page_median = {
        page.page_index: _median_text_height(page) for page in pages
    }

    for page, el in cands:
        el.inferred_level = _classify(
            el, page, page_median.get(page.page_index), chapter_res, section_res
        )

    _promote_flat_fallback(cands)
    _enforce_monotonic(cands)
    return pages


def _collect_candidates(
    pages: list[PageResult], skip_page_types: frozenset[str] | None = None
):
    """Return [(page, element)] for title-typed, non-dropped elements, in order.

    When ``skip_page_types`` is provided, pages whose ``page_type`` matches
    are skipped entirely (their title elements are not considered candidates).
    """
    cands = []
    for page in pages:
        if skip_page_types and page.page_type in skip_page_types:
            continue
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


def _classify(
    el,
    page: PageResult,
    med_h: float | None,
    chapter_res: list[re.Pattern],
    section_res: list[re.Pattern],
) -> int:
    text = el.text.strip()

    # 1. Literary keywords (highest priority, config-driven).
    if any(r.match(text) for r in chapter_res):
        return 1
    if any(r.match(text) for r in section_res):
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


def _promote_flat_fallback(cands) -> None:
    """Promote all candidates to H1 when every candidate fell to H3 fallback.

    Literary books whose chapter titles don't match ``chapter_patterns``
    (e.g. "人类与地球", "神奇的地球") all land at the H3 fallback. Without
    this fix, ``_enforce_monotonic`` would create a false hierarchy
    (H1 → H2 → H3 → H3 → ...) from what should be a flat chapter list.
    When ALL candidates are at level 3 (the fallback), promote them all
    to H1 so each chapter becomes a top-level entry in the EPUB TOC.

    Conservative: triggers only when every candidate is at level 3. If
    even one candidate matched a pattern or font-size ratio (H1/H2),
    the structure is left for ``_enforce_monotonic`` to handle.
    """
    if not cands:
        return
    levels = [el.inferred_level for _, el in cands if el.inferred_level is not None]
    if not levels:
        return
    if all(lvl == 3 for lvl in levels):
        for _, el in cands:
            if el.inferred_level is not None:
                el.inferred_level = 1


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

# CJK left-quote characters that signal a dialogue paragraph. After
# typography normalization ASCII `"` becomes `"` (U+201C); we also accept
# the raw ASCII form and the Japanese/CJK corner bracket `「` (U+300C).
_DIALOGUE_PREFIXES = ('"', '\u201c', '\u300c', '"')


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

    After block assembly, two Pandoc fenced-div wrappers are applied so the
    Kindle CSS can target chapter and dialogue styling:

      - Each H1 section (H1 + all following blocks until the next H1 or
        end-of-document) is wrapped in ``::: {.chapter} ... :::``.
      - Paragraphs starting with a CJK left quote (``"`` or ``「``) are
        wrapped in ``::: {.dialogue} ... :::``.

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
        if page.page_type in _SKIP_PAGE_TYPES:
            continue
        if page.page_type in _IMAGE_RENDER_TYPES:
            blocks.append(f"![](pages/page_{page.page_index:04d}.png)")
            continue
        live = sorted(
            [e for e in page.elements if not e.dropped],
            key=lambda e: e.order_index,
        )
        for el in live:
            text = el.text.strip()
            if not text:
                continue

            # AI-corrected text takes precedence over raw OCR text.
            if el.ai_corrected is not None:
                text = el.ai_corrected.strip()

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
                caption = (el.image_caption or "").strip()
                if caption:
                    blocks.append(f"![{caption}]({text})")
                else:
                    blocks.append(f"![]({text})")
            elif el.type in TABLE_LABELS:
                blocks.append(text)
            elif el.type in FORMULA_LABELS:
                blocks.append(f"$$\n{text}\n$$")
            elif el.low_confidence and el.ai_corrected is None:
                # Mark for AI review; `review.applier` replaces this block
                # with the corrected text (or [需校对] when AI is unsure).
                # When `ai_corrected` is set, fall through to the plain-text
                # branch below — the correction (or [需校对]/[UNCLEAR] marker)
                # replaces the low-confidence block as normal text.
                blocks.append(f">[low-confidence] {text}")
            else:
                blocks.append(text)

    # Wrap dialogue paragraphs (single-paragraph) and chapter sections
    # (H1 + body until next H1) in Pandoc fenced divs for CSS targeting.
    blocks = [_wrap_dialogue(b) for b in blocks]
    blocks = _wrap_chapters(blocks)

    out.write_text("\n\n".join(blocks).rstrip() + "\n", encoding="utf-8")
    return out


def _is_h1_block(block: str) -> bool:
    """True if `block` is an ATX H1 heading (starts with `# ` but not `## `)."""
    return block.startswith("# ") and not block.startswith("## ")


def _is_paragraph_block(block: str) -> bool:
    """True if `block` is a plain paragraph (not heading/image/table/formula/div)."""
    if not block:
        return False
    if block.startswith(("#", "!", "$$", ":::", "<table")):
        return False
    return True


def _wrap_dialogue(block: str) -> str:
    """Wrap a dialogue paragraph in `::: {.dialogue} ... :::`.

    A dialogue paragraph is a plain paragraph starting with a CJK left quote
    (`"`, `「`, or ASCII `"` for pre-normalization text). Non-paragraph blocks
    and paragraphs without a dialogue prefix are returned unchanged.
    """
    if not _is_paragraph_block(block):
        return block
    if not block.startswith(_DIALOGUE_PREFIXES):
        return block
    return f"::: {{.dialogue}}\n{block}\n:::"


def _wrap_chapters(blocks: list[str]) -> list[str]:
    """Wrap each H1 section in `::: {.chapter} ... :::`.

    An H1 section is the H1 heading plus all following blocks until the next
    H1 or end-of-list. Blocks before the first H1 (e.g. preface, book title)
    are emitted as-is without wrapping.
    """
    out: list[str] = []
    i = 0
    while i < len(blocks):
        if not _is_h1_block(blocks[i]):
            out.append(blocks[i])
            i += 1
            continue

        # Collect the H1 + all following non-H1 blocks as one chapter.
        section: list[str] = [blocks[i]]
        i += 1
        while i < len(blocks) and not _is_h1_block(blocks[i]):
            section.append(blocks[i])
            i += 1
        out.append("::: {.chapter}\n" + "\n\n".join(section) + "\n:::")
    return out
