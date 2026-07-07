"""Confidence-based element filtering (Phase 1 + Phase 4 refactor).

Three-tier marking replaces the former binary drop-or-keep:

  score < noise_confidence            + (empty or single-char)  -> dropped
  score < low_confidence_threshold    + non-empty multi-char    -> low_confidence=True
  score >= low_confidence_threshold                            -> normal (kept)
  confidence=None                                              -> preserved

`low_confidence` elements survive into book.md with a `>[low-confidence]`
marker and are collected into review.json for AI correction. Only true
noise (empty/single-char + very low score) is dropped outright.

Conservative by design:
  * `confidence=None` is preserved (not dropped, not flagged) — backends
    that don't provide confidence shouldn't lose elements.
  * Image/table/formula elements are never filtered by confidence — their
    `confidence` is layout-detection score, not OCR quality.
  * Already-dropped elements (e.g. by header_footer) are not re-touched.
"""

from __future__ import annotations

from pdf2book.config import PostprocessConfig
from pdf2book.ocr.models import PageResult


def filter_by_confidence(
    pages: list[PageResult], cfg: PostprocessConfig
) -> list[PageResult]:
    """Apply three-tier confidence marking to text elements.

    Mutates elements in place; returns the same list for chaining.
    No-op when `cfg.low_confidence_threshold <= 0.0` (filter disabled).
    """
    if not pages:
        return pages

    noise = cfg.noise_confidence
    low = cfg.low_confidence_threshold
    # Legacy: if min_confidence is set differently from low_confidence_threshold,
    # honor it as the low threshold (backward compat for old configs).
    if cfg.min_confidence > 0.0 and cfg.min_confidence != low:
        low = cfg.min_confidence

    if low <= 0.0:
        return pages  # filter disabled

    filter_types = set(cfg.confidence_filter_types)

    for page in pages:
        for el in page.elements:
            if el.dropped:
                continue  # already dropped by a prior stage
            if el.type not in filter_types:
                continue  # images/tables/formulas unaffected
            if el.confidence is None:
                continue  # conservative: preserve unknown-confidence elements

            score = el.confidence
            text = (el.text or "").strip()

            if score < noise:
                # Very low confidence: noise unless multi-char non-empty.
                if not text or len(text) <= 1:
                    el.dropped = True
                else:
                    el.low_confidence = True
            elif score < low:
                # Low confidence: mark for AI review if non-empty.
                if text:
                    el.low_confidence = True
                else:
                    el.dropped = True
            # else: normal — keep as-is.

    return pages


__all__ = ["filter_by_confidence"]
