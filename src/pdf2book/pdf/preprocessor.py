"""Image preprocessing for scanned PDFs.

For high-quality scans (300+ DPI, clean, no skew), this is a thin pass-through.
If low-quality scans need support later, add despeckle / deskew / binarize here.
"""

from pathlib import Path


def preprocess(image_path: Path) -> Path:
    """Preprocess a page image. Currently a no-op for high-quality scans."""
    return image_path
