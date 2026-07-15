"""Decorative image stripping (Phase 1, deterministic fallback).

Removes image references from ``book.md`` that carry zero information:
chapter-divider flourishes repeated across the book, horizontal separator
bars, and similar visual noise. Also protects functional images (QR codes,
barcodes) from being misidentified as decorations.

Three-step deterministic identification (strong signals, zero false positives):

  1. Perceptual-hash (pHash) clustering: images whose pHash Hamming distance
     is ≤ 5 and appear ≥ 3 times across the book are marked as decorations.
     This catches "the same flower icon beside every chapter title."
  2. Horizontal separator detection: extremely flat, near-monochrome bars
     (height < 10px, aspect ratio ≥ 5:1, >95% black-or-white pixels).
  3. Functional-image protection: QR codes / barcodes are detected via
     Pillow feature analysis (high contrast + three corner finder patterns)
     and surrounding OCR keyword ("扫码 / 二维码 / QR"). Functional images
     are removed from the decoration candidate set so they are never deleted.

Both conditions ("repeated ≥3 times" AND "not functional") must hold for a
pHash-clustered image to be stripped. Separator bars are deleted unconditionally
because their geometry is a strong enough signal on its own.

Idempotency: ``build_epub`` reads the original ``book.md`` on every call, so
``strip_decorations`` receives the same input each time and produces the same
output. No sentinel is written into the markdown — doing so would leak a
content block into the EPUB (Pandoc treats trailing HTML comments as a
separate split file). Phase 2 AI review deletes decorations by writing back
to ``book.md``; on the next ``build_epub`` the deleted image references are
already gone, so ``_collect_image_refs`` finds nothing to strip.

This module is the rule-based fallback for when AI review is disabled or did
not identify decorations. The AI multimodal path (markdown_review.py task 9)
handles ambiguous cases — chapter illustrations that look decorative but
carry content (e.g. 12 zodiac chapter illustrations). When AI review deletes
an image, the markdown line is gone, so this module's pHash collection simply
does not see it — the two paths never conflict.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# pHash Hamming-distance threshold: two images whose hashes differ in ≤ this
# many bits are treated as "the same picture." 5 is the conventional value
# (out of 64 bits) — tolerates JPEG re-encoding noise and bbox crop drift
# while rejecting genuinely different images.
_PHASH_HAMMING_THRESHOLD = 5

# Minimum occurrence count for a pHash cluster to be classified as a
# decoration. 3 = "appears in ≥3 places across the book." A single decorative
# image duplicated 1-2 times is left alone (could be a real figure reused).
_REPEAT_THRESHOLD = 3

# pHash size: produces a 64-bit hash (8×8 low-frequency DCT block).
_PHASH_SIZE = 8
_PHASH_INPUT_SIZE = 32  # Resize source to 32×32 before DCT.

# Separator-bar geometric thresholds.
_SEPARATOR_MAX_HEIGHT = 10   # px; taller than this is a real image, not a bar
_SEPARATOR_MIN_WIDTH = 50     # px; narrower is a dot or icon, not a divider
_SEPARATOR_MIN_ASPECT = 5.0   # width / height; <5 is too square to be a bar
_SEPARATOR_BW_RATIO = 0.95    # fraction of pixels that are near-black or near-white

# QR-code / functional image thresholds.
_QR_MIN_SIZE = 50              # px; smaller than this is unlikely to be a QR code
_QR_BLACK_RATIO_MIN = 0.30    # QR codes are roughly balanced black/white
_QR_BLACK_RATIO_MAX = 0.70
_QR_FINDER_MIN_HITS = 2       # ≥2 of 3 corners must look like a finder pattern
_QR_FINDER_BLACK_RATIO = 0.40  # corner region must be >40% black to count as finder

# OCR keywords around functional images (scanned within ±2 lines of the image).
_FUNCTIONAL_KEYWORDS = ("扫码", "扫描", "二维码", "条形码", "ISBN", "QR", "barcode", "Barcode")

# Markdown image reference: a line that is *only* an image (no surrounding text).
# Captures group(1)=alt text (may be empty), group(2)=images/relative path.
_IMAGE_LINE_RE = re.compile(r"^\s*!\[([^\]]*)\]\((images/[^)]+)\)\s*$")

# Window around image refs to scan for functional OCR keywords.
_KEYWORD_SCAN_WINDOW = 2  # ±2 lines


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ImageRef:
    """One ``![](images/pN_eM.png)`` reference in book.md.

    ``line_idx`` is 0-based (matches Python list index into splitlines).
    ``rel_path`` is the path as it appears in markdown (``images/pN_eM.png``).
    ``alt_text`` is the caption (may be empty string).
    """

    line_idx: int
    rel_path: str
    alt_text: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def strip_decorations(md_text: str, work_dir: Path | None = None) -> str:
    """Remove decorative image references from ``md_text``.

    Returns the text unchanged when:
      - ``work_dir`` is None or has no ``images/`` subdirectory.
      - No image references are found in the markdown.
      - No decorations are identified (all images are unique content).

    Otherwise strips the matching ``![](images/pN_eM.png)`` lines and
    removes adjacent blank lines left behind.

    Idempotent by construction: ``build_epub`` reads the original ``book.md``
    on every call, so the same input always yields the same output. When the
    Phase 2 AI review deletes an image line from ``book.md``, this function
    simply finds nothing to strip on the next run.
    """
    if work_dir is None:
        return md_text

    images_dir = work_dir / "images"
    if not images_dir.exists():
        return md_text

    refs = _collect_image_refs(md_text)
    if not refs:
        return md_text

    # 1. Functional-image protection (highest priority — must run before
    #    decoration identification so QR codes/barcodes are never deleted).
    functional_paths = _identify_functional_images(refs, images_dir, md_text)

    # 2. Repeated decorations via pHash clustering.
    decoration_paths = _identify_repeated_decorations(refs, images_dir)
    decoration_paths -= functional_paths  # never strip functional images

    # 3. Horizontal separator bars (geometric signal alone is sufficient).
    separator_paths = _identify_separator_lines(refs, images_dir)
    separator_paths -= functional_paths
    decoration_paths |= separator_paths

    if not decoration_paths:
        return md_text

    return _remove_image_lines(md_text, decoration_paths)


# ---------------------------------------------------------------------------
# 1. Image-reference collection
# ---------------------------------------------------------------------------


def _collect_image_refs(md_text: str) -> list[ImageRef]:
    """Find all standalone image lines in ``md_text``.

    Only lines whose entire content is ``![alt](images/...)`` are collected;
    inline images embedded in paragraphs are skipped (those are part of
    content, not standalone decorations).
    """
    refs: list[ImageRef] = []
    for i, line in enumerate(md_text.splitlines()):
        m = _IMAGE_LINE_RE.match(line)
        if m:
            refs.append(
                ImageRef(line_idx=i, rel_path=m.group(2), alt_text=m.group(1))
            )
    return refs


# ---------------------------------------------------------------------------
# 2. pHash computation and clustering
# ---------------------------------------------------------------------------


def _phash(path: Path) -> str | None:
    """Compute a 64-bit perceptual hash for the image at ``path``.

    Uses Pillow + numpy + scipy.fft.dct (no external imagehash dependency).
    Returns None on any I/O or import failure — callers treat None as
    "cannot classify, leave the image alone."
    """
    try:
        import numpy as np
        from PIL import Image
        from scipy.fft import dct
    except ImportError:
        return None

    try:
        img = Image.open(path).convert("L").resize(
            (_PHASH_INPUT_SIZE, _PHASH_INPUT_SIZE), Image.Resampling.LANCZOS
        )
        arr = np.asarray(img, dtype=np.float64)
        # 2D DCT-II: apply along rows, then columns. 'ortho' normalisation
        # makes the magnitude scale-invariant to source-image dimensions.
        dct_rows = dct(arr, axis=0, type=2, norm="ortho")
        dct_2d = dct(dct_rows, axis=1, type=2, norm="ortho")
        low = dct_2d[:_PHASH_SIZE, :_PHASH_SIZE].flatten()
        # Median excluding the DC term (low[0]) which carries no spatial
        # information and would skew the threshold on mostly-white images.
        median = float(np.median(low[1:]))
        bits = (low > median).astype(np.uint8)
        return "".join(str(b) for b in bits)
    except (OSError, ValueError, ZeroDivisionError):
        return None


def _hamming(h1: str, h2: str) -> int:
    """Hamming distance between two equal-length bit strings.

    Returns a large value (999) for mismatched lengths so callers can
    compare uniformly without length checks.
    """
    if len(h1) != len(h2):
        return 999
    return sum(c1 != c2 for c1, c2 in zip(h1, h2))


def _identify_repeated_decorations(
    refs: list[ImageRef], images_dir: Path
) -> set[str]:
    """Find image paths whose pHash clusters appear ≥3 times across the book.

    Returns the set of ``rel_path`` strings (e.g. ``"images/p1_e0.png"``)
    that should be treated as decorations.

    Clustering is single-linkage greedy: each new image joins the first
    existing cluster whose any member is within ``_PHASH_HAMMING_THRESHOLD``
    bits. This is O(n²) worst case but n is small (a book typically has
    <100 images).
    """
    # Count occurrences of each unique path (same path used multiple times
    # in the book — e.g. the same divider image referenced on every page).
    path_counts: Counter[str] = Counter(r.rel_path for r in refs)

    # Compute pHash once per unique path.
    phashes: dict[str, str] = {}
    for rel_path in path_counts:
        full_path = images_dir.parent / rel_path
        if not full_path.exists():
            continue
        ph = _phash(full_path)
        if ph is not None:
            phashes[rel_path] = ph

    # Greedy single-linkage clustering.
    clusters: list[set[str]] = []
    for path_str, ph in phashes.items():
        placed = False
        for cluster in clusters:
            for member in cluster:
                if _hamming(ph, phashes[member]) <= _PHASH_HAMMING_THRESHOLD:
                    cluster.add(path_str)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            clusters.append({path_str})

    # A cluster is decorative iff its total occurrence count ≥ threshold.
    decorations: set[str] = set()
    for cluster in clusters:
        total = sum(path_counts[p] for p in cluster)
        if total >= _REPEAT_THRESHOLD:
            decorations.update(cluster)
    return decorations


# ---------------------------------------------------------------------------
# 3. Horizontal separator detection
# ---------------------------------------------------------------------------


def _identify_separator_lines(
    refs: list[ImageRef], images_dir: Path
) -> set[str]:
    """Find image paths that are horizontal separator bars.

    A separator bar is a near-monochrome flat rectangle: very short height
    (≤10px), wide (≥50px), aspect ratio ≥5:1, and >95% near-black-or-white
    pixels. This geometric signature alone is strong enough to delete
    without confirmation — a real image is never this flat.
    """
    separators: set[str] = set()
    seen: set[str] = set()
    for ref in refs:
        if ref.rel_path in seen:
            continue
        seen.add(ref.rel_path)
        full_path = images_dir.parent / ref.rel_path
        if not full_path.exists():
            continue
        if _is_separator(full_path):
            separators.add(ref.rel_path)
    return separators


def _is_separator(path: Path) -> bool:
    """True if the image is a horizontal separator bar."""
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        img = Image.open(path).convert("L")
    except (OSError, ValueError):
        return False

    w, h = img.size
    if h >= _SEPARATOR_MAX_HEIGHT:
        return False
    if w < _SEPARATOR_MIN_WIDTH:
        return False
    if h == 0 or w / h < _SEPARATOR_MIN_ASPECT:
        return False

    pixels = list(img.tobytes())
    if not pixels:
        return False
    bw = sum(1 for p in pixels if p < 30 or p > 225)
    return bw / len(pixels) > _SEPARATOR_BW_RATIO


# ---------------------------------------------------------------------------
# 4. Functional-image protection (QR codes, barcodes)
# ---------------------------------------------------------------------------


def _identify_functional_images(
    refs: list[ImageRef],
    images_dir: Path,
    md_text: str,
) -> set[str]:
    """Find image paths that must NOT be stripped (QR codes, barcodes).

    Two signals, either sufficient:
      - Visual: QR-code finder pattern (three corner black squares).
      - Textual: surrounding OCR contains a functional keyword
        ("扫码", "二维码", "QR", "ISBN", etc.).

    Visual detection is conservative — it errs on the side of *not*
    classifying an image as a QR code, because a missed functional image
    gets deleted (irrecoverable) while a false-positive functional image
    only survives as a redundant decoration. Pyzbar is used when available
    for precise detection; the Pillow feature method is the fallback.
    """
    functional: set[str] = set()
    lines = md_text.splitlines()
    seen: set[str] = set()

    for ref in refs:
        if ref.rel_path in seen:
            continue
        seen.add(ref.rel_path)
        full_path = images_dir.parent / ref.rel_path
        if not full_path.exists():
            continue

        # 1. Try pyzbar (precise) if installed.
        if _pyzbar_is_qr_code(full_path):
            functional.add(ref.rel_path)
            continue

        # 2. Try Pillow feature method.
        if _is_qr_code(full_path):
            functional.add(ref.rel_path)
            continue

        # 3. Surrounding OCR text keyword (deterministic, zero false positives).
        if _has_functional_keyword_nearby(lines, ref.line_idx):
            functional.add(ref.rel_path)

    return functional


def _pyzbar_is_qr_code(path: Path) -> bool:
    """Use pyzbar for precise QR/barcode detection if installed.

    Returns False when pyzbar is unavailable or the image has no code.
    """
    try:
        from pyzbar.pyzbar import decode
    except ImportError:
        return False
    try:
        results = decode(str(path))
        return len(results) > 0
    except Exception:
        return False


def _is_qr_code(path: Path) -> bool:
    """Pillow feature method: detect QR-code-like visual signatures.

    A QR code has:
      - Roughly balanced black/white ratio (30-70% black).
      - Three "finder patterns" at three corners (top-left, top-right,
        bottom-left) — each a black square ring with a black center.

    The finder-pattern check is approximate: it verifies that ≥2 of 3
    corners have a high black-pixel ratio (the outer ring of the finder
    pattern). This is conservative — it catches most QR codes while
    rejecting regular illustrations.
    """
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        img = Image.open(path).convert("L")
    except (OSError, ValueError):
        return False

    w, h = img.size
    if w < _QR_MIN_SIZE or h < _QR_MIN_SIZE:
        return False

    # Balanced black/white ratio.
    small = img.resize((100, 100), Image.Resampling.LANCZOS)
    pixels = list(small.tobytes())
    if not pixels:
        return False
    black_ratio = sum(1 for p in pixels if p < 128) / len(pixels)
    if not (_QR_BLACK_RATIO_MIN <= black_ratio <= _QR_BLACK_RATIO_MAX):
        return False

    # Finder-pattern check on three corners.
    finder_hits = 0
    corners = [
        (0, 0, max(1, w // 4), max(1, h // 4)),               # top-left
        (3 * w // 4, 0, w, max(1, h // 4)),                    # top-right
        (0, 3 * h // 4, max(1, w // 4), h),                    # bottom-left
    ]
    for x1, y1, x2, y2 in corners:
        if x2 <= x1 or y2 <= y1:
            continue
        region = img.crop((x1, y1, x2, y2)).resize((7, 7), Image.Resampling.LANCZOS)
        rp = list(region.tobytes())
        if not rp:
            continue
        black = sum(1 for p in rp if p < 128)
        if black / len(rp) > _QR_FINDER_BLACK_RATIO:
            finder_hits += 1

    return finder_hits >= _QR_FINDER_MIN_HITS


def _has_functional_keyword_nearby(lines: list[str], line_idx: int) -> bool:
    """True if any functional OCR keyword appears within ±2 lines of ``line_idx``."""
    start = max(0, line_idx - _KEYWORD_SCAN_WINDOW)
    end = min(len(lines), line_idx + _KEYWORD_SCAN_WINDOW + 1)
    for i in range(start, end):
        if i == line_idx:
            continue
        if any(kw in lines[i] for kw in _FUNCTIONAL_KEYWORDS):
            return True
    return False


# ---------------------------------------------------------------------------
# 5. Markdown rewriting
# ---------------------------------------------------------------------------


def _remove_image_lines(md_text: str, decoration_paths: set[str]) -> str:
    """Delete image-reference lines whose path is in ``decoration_paths``.

    Also removes a single trailing blank line after each deletion so the
    markdown does not accumulate empty paragraphs.
    """
    lines = md_text.splitlines(keepends=True)
    new_lines: list[str] = []
    skip_next_blank = False
    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        m = _IMAGE_LINE_RE.match(stripped)
        if m and m.group(2) in decoration_paths:
            skip_next_blank = True
            continue
        if skip_next_blank and stripped.strip() == "":
            skip_next_blank = False
            continue
        skip_next_blank = False
        new_lines.append(line)
    return "".join(new_lines)


__all__ = ["strip_decorations", "ImageRef"]
