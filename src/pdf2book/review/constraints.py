"""Constraints extraction & validation for low-confidence OCR correction.

This module is the core anti-hallucination mechanism for AI review. Given an
OCR'd text fragment (possibly with wildcards like `电*`), we extract structural
constraints from the original and verify any AI-proposed correction against
them. Violations trigger a retry with feedback (see `ai_client.py`).

Constraint rules:
  * `max_length = len(original) + 1` — allow ±1 char tolerance (e.g. expand
    `电*` to `电视` but not `电视机` which would add 2 chars to a 2-char text).
  * `preserved_chars` — non-wildcard chars in the original MUST appear in the
    correction. `电*` preserves `电`; the AI cannot return `电视机` (drops none
    but adds too many) or `雷*` (drops `电`).
  * `max_edit_distance = max(2, len(original) // 3)` — allow ~1/3 of chars to
    change. For 2-char text → max 2; for 6-char text → max 2; for 9-char → 3.
  * `wildcard_count` — number of `*?□○` chars; informational, used by prompt
    builder to tell the AI how many slots to fill.

Wildcard set (`*?□○◇●`): `*`/`?` are ASCII glob-style; `□`/`○`/`◇`/`●` are
CJK placeholder glyphs that PP-OCR sometimes emits for unrecognized chars.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

# Wildcards the OCR engine emits for unrecognized characters.
# `*`/`?` — ASCII glob-style placeholders (RapidOCR/PaddleOCR noise).
# `□`/`○`/`◇`/`●` — CJK placeholder glyphs (PP-StructureV3 unk-token render).
# `〼` — boxed-kanji placeholder, occasionally seen in JP/CN OCR.
WILDCARD_CHARS = frozenset("*?□○◇●〼")


@dataclass
class CorrectionConstraints:
    """Constraints extracted from the original OCR text.

    Used by `validate_correction` to verify AI-proposed corrections. All
    fields are derived from `original_text` (and the surrounding context,
    which is informational — not enforced — to keep validation deterministic).
    """

    original_text: str
    max_length: int
    preserved_chars: list[str]
    max_edit_distance: int
    wildcard_count: int
    context_before: str = ""
    context_after: str = ""

    @property
    def preserved_multiset(self) -> dict[str, int]:
        """Multiset view of `preserved_chars` for O(n) validation.

        Recomputed on access; not cached because `preserved_chars` is mutable.
        Callers in `validate_correction` call this once per correction.
        """
        return dict(Counter(self.preserved_chars))


def extract_constraints(
    original_text: str,
    context_before: str = "",
    context_after: str = "",
) -> CorrectionConstraints:
    """Extract structural constraints from an OCR text fragment.

    Args:
        original_text: The raw OCR text (may contain wildcards).
        context_before: Sentence-complete preceding context (informational).
        context_after: Sentence-complete following context (informational).

    Returns:
        CorrectionConstraints populated per the rules in the module docstring.
        Empty original_text returns max_length=0, which will reject any
        non-empty correction (empty input → mark [需校对] upstream).
    """
    text = original_text
    length = len(text)

    # Preserved chars: every non-wildcard char in the original. Whitespace
    # is excluded — it's noise from OCR layout, not content the AI must keep.
    preserved = [ch for ch in text if ch not in WILDCARD_CHARS and not ch.isspace()]

    wildcard_count = sum(1 for ch in text if ch in WILDCARD_CHARS)

    # max_edit_distance: floor(len/3), but at least 2 to give the AI room for
    # short fragments. For 2-char text → 2; for 6-char → 2; for 9-char → 3.
    max_edit_distance = max(2, length // 3)

    return CorrectionConstraints(
        original_text=text,
        max_length=length + 1,  # +1 char tolerance
        preserved_chars=preserved,
        max_edit_distance=max_edit_distance,
        wildcard_count=wildcard_count,
        context_before=context_before,
        context_after=context_after,
    )


def validate_correction(
    original: str,
    corrected: str,
    constraints: CorrectionConstraints,
) -> tuple[bool, str]:
    """Verify an AI-proposed correction against the constraints.

    Returns:
        (is_valid, reason). `reason` is empty when valid; otherwise it's a
        human-readable explanation suitable for feeding back to the AI as a
        retry hint (e.g. "edit distance 3 > max 1; please stay closer to the
        original").

    Checks (in order, first failure wins):
      1. `[UNCLEAR]` in corrected → REJECT (this is the AI's "I don't know"
         signal, not a correction — caller should mark [需校对] and stop).
      2. Empty corrected → REJECT (AI returned nothing usable).
      3. Length > max_length → REJECT (too many chars added).
      4. Preserved chars missing → REJECT (dropped known content).
      5. Edit distance > max_edit_distance → REJECT (changed too much).
    """
    # 1. [UNCLEAR] is the AI's refusal signal — not a validation failure per
    #    se, but the correction is unusable. Caller treats this specially.
    if "[UNCLEAR]" in corrected:
        return False, "AI returned [UNCLEAR] — uncertain, mark for manual review"

    # 2. Empty correction.
    if not corrected.strip():
        return False, "Correction is empty"

    # 3. Length check.
    if len(corrected) > constraints.max_length:
        return (
            False,
            f"Length {len(corrected)} > max {constraints.max_length} "
            f"(original {len(original)} chars)",
        )

    # 4. Preserved chars: every non-wildcard char in original must appear in
    #    corrected (as a multiset — duplicates must be matched).
    needed_multiset = constraints.preserved_multiset
    if needed_multiset:
        corrected_counts: dict[str, int] = {}
        for ch in corrected:
            if ch in needed_multiset:
                corrected_counts[ch] = corrected_counts.get(ch, 0) + 1
        missing = []
        for ch, needed in needed_multiset.items():
            have = corrected_counts.get(ch, 0)
            if have < needed:
                missing.append(f"'{ch}' ({have}/{needed})")
        if missing:
            return False, f"Missing preserved chars: {', '.join(missing)}"

    # 5. Edit distance.
    distance = _levenshtein(original, corrected)
    if distance > constraints.max_edit_distance:
        return (
            False,
            f"Edit distance {distance} > max {constraints.max_edit_distance} "
            f"(original {len(original)} chars)",
        )

    return True, ""


def _levenshtein(a: str, b: str) -> int:
    """Classic Levenshtein edit distance (insertions/deletions/substitutions).

    O(m*n) DP, O(min(m,n)) memory via rolling row. Used by `validate_correction`
    to bound how much the AI can deviate from the original OCR text.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    # Make `a` the shorter string to minimize memory.
    if len(a) > len(b):
        a, b = b, a

    prev = list(range(len(a) + 1))
    for j, bch in enumerate(b, start=1):
        curr = [j] + [0] * len(a)
        for i, ach in enumerate(a, start=1):
            cost = 0 if ach == bch else 1
            curr[i] = min(
                prev[i] + 1,        # deletion
                curr[i - 1] + 1,    # insertion
                prev[i - 1] + cost,  # substitution
            )
        prev = curr
    return prev[len(a)]


__all__ = [
    "CorrectionConstraints",
    "extract_constraints",
    "validate_correction",
    "WILDCARD_CHARS",
]
