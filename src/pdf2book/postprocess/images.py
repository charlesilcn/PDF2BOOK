"""Image extraction (T9).

PP-StructureV3 identifies image/figure/chart regions and returns their
bounding boxes, but does not save cropped image files in our configuration
(no `save_to_json` output directory). The `block_content` for these blocks
contains OCR'd text within the region (often noise from illustrations, or
the full text for cover pages), not a file path.

This module crops the actual image region from the rendered page PNG
(``work_dir/pages/page_NNNN.png``) using Pillow and saves it to
``work_dir/images/pN_eM.png`` so the generated markdown can reference it
with a relative path. The original OCR text is preserved as
``Element.image_caption`` so ``to_markdown`` can emit it as a caption.

When the page PNG is missing or Pillow is unavailable, we fall back to a
placeholder path (no file written) — the image reference in markdown will
be broken, but the pipeline does not crash.
"""

from __future__ import annotations

from pathlib import Path

from pdf2book.ocr.models import PageResult

# Public so structure.to_markdown can reuse the same label set.
IMAGE_LABELS = frozenset({"image", "figure", "chart"})


def extract_images(pages: list[PageResult], work_dir: Path) -> list[PageResult]:
    """Crop image regions from rendered page PNGs into ``work_dir/images/``.

    For each image/figure/chart element:
      1. Save its original ``text`` to ``image_caption`` (if non-empty).
      2. Crop the bbox region from ``work_dir/pages/page_NNNN.png``.
      3. Save the crop to ``work_dir/images/pN_eM.png``.
      4. Overwrite ``Element.text`` with the relative path.

    Mutates elements in place; returns the same list for chaining.
    """
    if not pages:
        return pages

    img_dir = work_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = work_dir / "pages"

    for page in pages:
        page_png = pages_dir / f"page_{page.page_index:04d}.png"
        page_img = _load_image(page_png) if page_png.exists() else None

        for i, el in enumerate(page.elements):
            if el.dropped or el.type not in IMAGE_LABELS:
                continue

            # Preserve original OCR text before overwriting with the path.
            original = el.text.strip()
            if original:
                el.image_caption = original

            target_name = f"p{page.page_index}_e{i}.png"
            target_path = img_dir / target_name

            if page_img is not None:
                _crop_and_save(page_img, el.bbox, target_path, page.page_index)

            el.text = f"images/{target_name}"
    return pages


def _load_image(path: Path):
    """Lazy-import Pillow and open the image; return None on failure."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        return Image.open(path)
    except (OSError, ValueError):
        return None


def _crop_and_save(page_img, bbox, target: Path, page_index: int) -> None:
    """Crop ``bbox`` from ``page_img`` and save to ``target``.

    ``bbox`` is (x1, y1, x2, y2) in pixel coordinates. Coordinates are
    clamped to image bounds. Silently skips degenerate (zero-area) crops.
    """
    w, h = page_img.size
    x1 = max(0, int(round(bbox.x1)))
    y1 = max(0, int(round(bbox.y1)))
    x2 = min(w, int(round(bbox.x2)))
    y2 = min(h, int(round(bbox.y2)))
    if x2 <= x1 or y2 <= y1:
        return
    try:
        crop = page_img.crop((x1, y1, x2, y2))
        crop.save(target, format="PNG")
    except (OSError, ValueError):
        pass
