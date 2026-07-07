"""Review-item collector & sentence-complete context extractor.

This module bridges rule-based postprocessing and AI review. It walks the
PageResult list after all rule-based stages have run, and produces a
`review.json`-shaped dict containing:

  * `metadata_candidates` — cover/copyright page text (for AI extraction
    when CIP rules failed).
  * `low_confidence_texts` — every `low_confidence=True` element with
    sentence-complete surrounding context + extracted constraints.
  * `title_candidates` — title elements with their current inferred_level
    (for AI to confirm/demote).
  * `page_type_candidates` — pages where the rule-based classifier returned
    `unknown` or low-confidence classifications.

The context extractor (`extract_context`) is the token-efficiency core:
it gathers surrounding text in *complete sentences only* (no half-sentences)
and stops at structural boundaries (paragraph `\n\n`, chapter H1, page break
when the neighbor's first/last sentence isn't a paragraph continuation).

See `pdf2book-ai-review-pipeline.md` Phase 5 for the full design.
"""

from __future__ import annotations

import re
from pathlib import Path

from pdf2book.ocr.models import Element, PageResult
from pdf2book.postprocess.structure import TITLE_LABELS
from pdf2book.review.constraints import extract_constraints

# Sentence boundary punctuation: CJK + ASCII terminators.
# `。` U+3002 (CJK full stop), `？` U+300F, `！` U+301F, `；` U+303F (semicolon),
# `?` `!` `;` ASCII equivalents, `\n` newline (treated as soft boundary).
# Note: `.` (ASCII period) is NOT a sentence boundary — too ambiguous in OCR
# output (decimals, abbreviations, page numbers like "3.14" or "Ch. 1").
SENTENCE_TERMINATORS = frozenset("。？！；\n?!;")

# Sentence-split regex: keep the terminator with the sentence.
# Splits on any terminator, consuming it into the preceding piece.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。？！；\n?!;])")

# Chapter heading marker in flattened text. We treat any element with
# `inferred_level == 1` as a hard boundary — context does not cross H1.
# (H2/H3 are soft boundaries — context can cross them.)


# ---------------------------------------------------------------------------
# Sentence splitting & accumulation (Phase 5.2)
# ---------------------------------------------------------------------------


def _split_by_sentence(text: str) -> list[str]:
    """Split `text` into sentences, keeping terminators with each piece.

    Boundary chars: `。？！；\\n?!;` (CJK + ASCII). Paragraph breaks
    (`\\n\\n`) are preserved as empty-string markers between paragraphs so
    callers can detect paragraph boundaries; non-empty pieces never start
    with a newline.

    Returns a list of sentence strings (some may be empty due to paragraph
    breaks). Whitespace-only pieces are dropped. A trailing piece without
    a terminator (a "half sentence") is included as the last element —
    callers decide whether to use it via `_is_complete_sentence`.

    Examples:
      >>> _split_by_sentence("第一句。第二句！半句")
      ["第一句。", "第二句！", "半句"]
      >>> _split_by_sentence("段一。\\n\\n段二。")
      ["段一。", "", "段二。"]  # empty string marks paragraph break
    """
    if not text:
        return []

    # Split after each terminator. This keeps the terminator with the
    # preceding sentence. `re.split` with a lookbehind does exactly this.
    parts = _SENTENCE_SPLIT_RE.split(text)

    # Strip whitespace-only pieces to empty strings (preserves paragraph
    # break detection via empty strings between non-empty pieces).
    out: list[str] = []
    for p in parts:
        if p.strip() == "":
            out.append("")  # paragraph break marker
        else:
            out.append(p)
    # Drop trailing empty markers.
    while out and out[-1] == "":
        out.pop()
    return out


def _is_complete_sentence(s: str) -> bool:
    """True if `s` ends with a sentence terminator.

    A "half sentence" lacks a terminator — per Phase 5 design, half sentences
    are NEVER transmitted to the AI (information incomplete, value low, even
    if it's a cross-page paragraph continuation).
    """
    if not s:
        return False
    return s[-1] in SENTENCE_TERMINATORS


def _accumulate_sentences(
    sentences: list[str],
    budget: int,
    direction: str,
) -> str:
    """Accumulate complete sentences up to `budget * 1.5` chars.

    Args:
        sentences: Ordered list of sentence strings (some may be empty,
            marking paragraph breaks). For `direction="forward"` this is
            in reading order; for `direction="backward"` it's reversed
            (caller reverses the input).
        budget: Target character budget (e.g. 100). Accumulation stops when
            adding the next sentence would exceed `budget * 1.5`.
        direction: "forward" or "backward" — used only for the empty-string
            filter (backward skips leading empties from the end).

    Returns:
        Joined sentence string. Empty if no complete sentence fits.

    Rules (per design doc):
      1. Skip half sentences (no terminator) — ALWAYS excluded.
      2. Skip empty strings (paragraph breaks) — but stop accumulation
         when we hit one (cross-paragraph context is unrelated).
         Exception: leading empties (at the very start) are skipped silently.
      3. First complete sentence is always included even if it exceeds
         budget (guarantees at least some context when available).
      4. Subsequent sentences included only if accumulated + len ≤ budget * 1.5.
      5. H1 chapter headings (`# Title`) stop accumulation immediately —
         the caller pre-stops at H1 elements, but defensive check here too.
    """
    if not sentences or budget <= 0:
        return ""

    max_total = int(budget * 1.5)
    collected: list[str] = []
    accumulated = 0
    seen_non_empty = False

    for s in sentences:
        # Paragraph break: stop (don't cross paragraph boundary).
        if s == "":
            if seen_non_empty:
                break
            else:
                # Leading paragraph break (e.g. at start of backward scan) — skip.
                continue

        # Half sentence: skip per design (rule 1).
        if not _is_complete_sentence(s):
            continue

        # H1 chapter heading marker (defensive; caller should pre-stop).
        if s.lstrip().startswith("# "):
            break

        seen_non_empty = True

        # First complete sentence: always include (rule 3).
        if not collected:
            collected.append(s)
            accumulated = len(s)
            continue

        # Subsequent: check budget (rule 4).
        if accumulated + len(s) > max_total:
            break

        collected.append(s)
        accumulated += len(s)

    if not collected:
        return ""

    if direction == "backward":
        # We accumulated in reverse order; reverse back for correct reading.
        collected = list(reversed(collected))

    return "".join(collected)


# ---------------------------------------------------------------------------
# Context extraction (Phase 5.2 main API)
# ---------------------------------------------------------------------------


def extract_context(
    pages: list[PageResult],
    target_page_index: int,
    target_element_order: int,
    budget: int = 100,
) -> tuple[str, str]:
    """Extract sentence-complete context around a target element.

    Walks the flattened element stream (all pages, live elements only) in
    reading order, finds the target by (page_index, order_index), and
    gathers complete-sentence context before/after it — bounded by `budget`
    and stopped at structural boundaries (H1 chapter heading, paragraph
    break, page edge when neighbor isn't a paragraph continuation).

    Args:
        pages: All pages from the OCR pipeline (sorted by page_index).
        target_page_index: The page containing the target element.
        target_element_order: The `order_index` of the target element.
        budget: Character budget for each side (before/after). Default 100.

    Returns:
        (context_before, context_after) — each a string of complete
        sentences. Either may be empty if no context fits or all neighbors
        are half-sentences / across structural boundaries.
    """
    if not pages:
        return "", ""

    # Flatten all live elements across pages, in reading order.
    # Each entry is (page_index, element, is_h1_boundary).
    flat: list[tuple[int, Element, bool]] = []
    for page in sorted(pages, key=lambda p: p.page_index):
        for el in sorted(
            [e for e in page.elements if not e.dropped],
            key=lambda e: e.order_index,
        ):
            is_h1 = (
                el.type in TITLE_LABELS
                and el.inferred_level == 1
            )
            flat.append((page.page_index, el, is_h1))

    # Find target position.
    target_pos = -1
    for i, (page_idx, el, _) in enumerate(flat):
        if page_idx == target_page_index and el.order_index == target_element_order:
            target_pos = i
            break

    if target_pos < 0:
        return "", ""

    # Build candidate text streams before & after the target, stopping at
    # H1 boundaries (chapter breaks — context does not cross).
    before_pieces: list[str] = []
    for i in range(target_pos - 1, -1, -1):
        page_idx, el, is_h1 = flat[i]
        if is_h1:
            break  # hard boundary — stop
        text = (el.ai_corrected or el.text or "").strip()
        if not text:
            continue
        before_pieces.append(text)
        # Stop if we have enough candidate text (3x budget is plenty).
        if sum(len(p) for p in before_pieces) > budget * 3:
            break

    after_pieces: list[str] = []
    for i in range(target_pos + 1, len(flat)):
        page_idx, el, is_h1 = flat[i]
        if is_h1:
            break  # hard boundary — stop
        text = (el.ai_corrected or el.text or "").strip()
        if not text:
            continue
        after_pieces.append(text)
        if sum(len(p) for p in after_pieces) > budget * 3:
            break

    # Join pieces with empty string (each element is one OCR block; their
    # own punctuation handles sentence boundaries). Using `\n` here would
    # make `_split_by_sentence` emit paragraph-break markers between every
    # pair of elements, triggering the "don't cross paragraph" rule and
    # keeping only the immediately-adjacent element. Empty-string join
    # preserves element-internal newlines (real paragraph breaks in the
    # source text) while not introducing artificial ones between blocks.
    before_text = "".join(reversed(before_pieces))
    after_text = "".join(after_pieces)

    # Split into sentences and accumulate within budget.
    before_sentences = _split_by_sentence(before_text)
    after_sentences = _split_by_sentence(after_text)

    # For backward accumulation, reverse the sentence list so we walk from
    # the target outward (closest first), then reverse the result back.
    before_context = _accumulate_sentences(
        list(reversed(before_sentences)), budget, direction="backward"
    )
    after_context = _accumulate_sentences(
        after_sentences, budget, direction="forward"
    )

    return before_context, after_context


# ---------------------------------------------------------------------------
# Review-item collection (Phase 5.3)
# ---------------------------------------------------------------------------

# Page types that carry metadata hints (cover/copyright text often contains
# title/author/ISBN). These pages' OCR text is sent to the AI as metadata
# extraction candidates when CIP rule-based extraction failed or returned
# incomplete data.
METADATA_PAGE_TYPES = frozenset({"cover", "copyright", "frontispiece"})

# Page types where the rule-based classifier is uncertain. Only these are
# sent to the AI for confirmation; confident classifications (body, toc,
# etc.) are trusted as-is to save tokens.
UNCERTAIN_PAGE_TYPES = frozenset({"unknown"})

# Maximum chars of OCR text to include per page in page_type_candidates.
# Long pages are truncated to keep the review.json payload bounded.
_PAGE_SAMPLE_LIMIT = 200

# Maximum number of metadata candidate pages to send (top N by relevance:
# cover > frontispiece > copyright). Keeps the prompt focused.
_MAX_METADATA_CANDIDATES = 3


def collect_review_items(
    pages: list[PageResult],
    metadata,  # BookMetadata | None — avoid import cycle with epub.metadata
    book_md: Path | None = None,
    context_budget: int = 100,
) -> dict:
    """Collect review items for AI audit into a `review.json`-shaped dict.

    Walks the PageResult list (after all rule-based postprocessing) and
    gathers four categories of review items:

      1. `metadata` — current BookMetadata (may be incomplete) + candidate
         page texts for AI extraction when CIP rules failed.
      2. `low_confidence_texts` — every `low_confidence=True` element with
         sentence-complete context + extracted constraints. This is the
         main payload for AI OCR correction.
      3. `title_candidates` — title elements (TITLE_LABELS) with their
         current `inferred_level`, for AI to confirm or demote.
      4. `page_type_candidates` — pages classified as `unknown` (or other
         uncertain types), with a text sample for AI classification.

    Args:
        pages: All pages from the OCR pipeline (post-classification).
        metadata: Current BookMetadata (may be None if CIP extraction
            failed and no fallback ran). When None, metadata_candidates
            is populated but `current` is empty.
        book_md: Unused (kept for pipeline signature symmetry). The review
            dict is returned, not written, by this function.
        context_budget: Character budget for each side of low-confidence
            context extraction. Default 100.

    Returns:
        Dict structured for JSON serialization to `review.json`. Empty
        lists when no items of a category need review.
    """
    # Sort pages by index for stable output.
    sorted_pages = sorted(pages, key=lambda p: p.page_index)

    # --- 1. Metadata candidates -------------------------------------------
    metadata_current: dict = {}
    metadata_candidates: list[dict] = []
    if metadata is not None:
        metadata_current = {
            "title": metadata.title,
            "author": metadata.author,
            "lang": getattr(metadata, "lang", "zh-CN"),
            "date": getattr(metadata, "date", None),
            "rights": getattr(metadata, "rights", None),
        }
        # If title is still default, CIP extraction likely failed — gather
        # candidate pages for AI extraction.
        is_incomplete = metadata.title in ("", "Untitled") or metadata.author in ("", "Unknown")
    else:
        is_incomplete = True

    if is_incomplete:
        for page in sorted_pages:
            if page.page_type not in METADATA_PAGE_TYPES:
                continue
            text = _collect_page_text(page)
            if not text.strip():
                continue
            metadata_candidates.append(
                {
                    "page_index": page.page_index,
                    "page_type": page.page_type,
                    "text": text[:500],  # cap at 500 chars per candidate
                }
            )
            if len(metadata_candidates) >= _MAX_METADATA_CANDIDATES:
                break

    # --- 2. Low-confidence texts ------------------------------------------
    low_confidence_texts: list[dict] = []
    for page in sorted_pages:
        for el in sorted(page.elements, key=lambda e: e.order_index):
            if not el.low_confidence or el.dropped:
                continue
            text = (el.text or "").strip()
            if not text:
                continue

            context_before, context_after = extract_context(
                pages, page.page_index, el.order_index, budget=context_budget
            )
            constraints = extract_constraints(text, context_before, context_after)

            low_confidence_texts.append(
                {
                    "id": f"p{page.page_index}_e{el.order_index}",
                    "page_index": page.page_index,
                    "order_index": el.order_index,
                    "original_text": text,
                    "context_before": context_before,
                    "context_after": context_after,
                    "constraints": {
                        "max_length": constraints.max_length,
                        "preserved_chars": constraints.preserved_chars,
                        "max_edit_distance": constraints.max_edit_distance,
                        "wildcard_count": constraints.wildcard_count,
                    },
                }
            )

    # --- 3. Title candidates ----------------------------------------------
    title_candidates: list[dict] = []
    for page in sorted_pages:
        for el in sorted(page.elements, key=lambda e: e.order_index):
            if el.dropped or el.type not in TITLE_LABELS:
                continue
            text = (el.ai_corrected or el.text or "").strip()
            if not text:
                continue
            title_candidates.append(
                {
                    "id": f"p{page.page_index}_e{el.order_index}",
                    "page_index": page.page_index,
                    "order_index": el.order_index,
                    "text": text,
                    "current_level": el.inferred_level,
                }
            )

    # --- 4. Page type candidates ------------------------------------------
    page_type_candidates: list[dict] = []
    for page in sorted_pages:
        if page.page_type not in UNCERTAIN_PAGE_TYPES:
            continue
        text_sample = _collect_page_text(page)[:_PAGE_SAMPLE_LIMIT]
        page_type_candidates.append(
            {
                "page_index": page.page_index,
                "current_type": page.page_type,
                "text_sample": text_sample,
            }
        )

    return {
        "metadata": {
            "current": metadata_current,
            "candidates": metadata_candidates,
        },
        "low_confidence_texts": low_confidence_texts,
        "title_candidates": title_candidates,
        "page_type_candidates": page_type_candidates,
    }


def _collect_page_text(page: PageResult) -> str:
    """Concatenate all live (non-dropped) element texts on a page.

    Used for metadata candidate extraction and page-type sample text.
    Elements are joined with newline to preserve visual structure.
    """
    parts: list[str] = []
    for el in sorted(page.elements, key=lambda e: e.order_index):
        if el.dropped:
            continue
        text = (el.ai_corrected or el.text or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


__all__ = [
    "extract_context",
    "collect_review_items",
    "_split_by_sentence",
    "_is_complete_sentence",
    "_accumulate_sentences",
    "SENTENCE_TERMINATORS",
    "METADATA_PAGE_TYPES",
    "UNCERTAIN_PAGE_TYPES",
]
