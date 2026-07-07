"""AI review pipeline for PDF2BOOK.

This package implements the four-stage AI review loop that sits between
rule-based postprocessing and EPUB generation:

  1. **Collect** — `collector.collect_review_items` walks the PageResult
     list and gathers four categories of review items (metadata candidates,
     low-confidence texts, title candidates, page-type candidates) into a
     `review.json`-shaped dict.
  2. **Constrain** — `constraints.extract_constraints` derives hard limits
     (max_length, preserved_chars, max_edit_distance) from each low-conf
     text; `validate_correction` checks AI corrections against them.
  3. **Prompt** — `prompt_builder.build_*_prompt` constructs the four
     task-specific prompts (metadata / low-confidence / titles / page-types)
     with embedded input data, output schema, and refusal protocol.
  4. **Call + Retry** — `ai_client.AIClient.review_all` dispatches the four
     prompts to an OpenAI-compatible endpoint, validates low-confidence
     corrections against constraints, and retries violations per-item
     (up to `max_retries`).
  5. **Apply** — `applier.apply_review_results` writes the validated AI
     decisions back into `Element.ai_corrected`, `Element.inferred_level`,
     `PageResult.page_type`, and `BookMetadata`, ready for `to_markdown`
     and `write_meta_yaml` to regenerate the final book.md and meta.md.

The pipeline is pure-text (no multimodal) — all inputs are serialized as
structured text, and the constraint-validation retry loop keeps quality
high even with weaker models. When `AIReviewConfig.enabled=False`, every
stage short-circuits and the pipeline runs without AI (low-confidence
texts keep their `>[low-confidence]` markers for manual proofreading).
"""

from __future__ import annotations

from pdf2book.review.ai_client import AIClient, ReviewResult
from pdf2book.review.applier import apply_review_results
from pdf2book.review.collector import collect_review_items, extract_context
from pdf2book.review.constraints import (
    CorrectionConstraints,
    extract_constraints,
    validate_correction,
)
from pdf2book.review.prompt_builder import (
    build_low_confidence_prompt,
    build_metadata_prompt,
    build_page_type_prompt,
    build_title_prompt,
)

__all__ = [
    # Collector
    "collect_review_items",
    "extract_context",
    # Constraints
    "CorrectionConstraints",
    "extract_constraints",
    "validate_correction",
    # Prompt builders
    "build_metadata_prompt",
    "build_low_confidence_prompt",
    "build_title_prompt",
    "build_page_type_prompt",
    # AI client
    "AIClient",
    "ReviewResult",
    # Applier
    "apply_review_results",
]
