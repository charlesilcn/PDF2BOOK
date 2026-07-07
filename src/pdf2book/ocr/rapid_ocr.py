"""RapidOCR lightweight backend (Phase 2, ~50MB onnxruntime, CPU-only).

RapidOCR (rapidocr_onnxruntime) is a stripped-down PP-OCR port that ships
only text detection + recognition — no layout classification, no table/
formula/image detection. Output format: ``[[box, text, score], ...]``
where ``box`` is a 4-point polygon.

Trade-offs vs the default PaddlePP backend:
  * Pro: ~50MB model, fast startup, high concurrency (great for batch).
  * Pro: Recognition confidence is directly available (no cross-reference).
  * Con: No title/image/table/formula detection — all elements become
    ``type="text"``, so ``structure.infer_title_levels`` falls back to
    font-size ratio + numeric numbering (literary chapter keywords like
    "第X章" still work because they're text-based).
  * Con: Images, tables, and formulas are not extracted (text-only output).

Use this backend when:
  - Processing many short PDFs in batch (memory matters).
  - The source is plain text (novels, papers without figures).
  - Rapid iteration is more important than layout fidelity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pdf2book.config import OCRConfig
from pdf2book.ocr.base import OCRBackend, safe_bbox
from pdf2book.ocr.models import BBox, Element, PageResult


class RapidOCRBackend(OCRBackend):
    """OCR backend using rapidocr-onnxruntime (lightweight tier)."""

    def __init__(self, cfg: OCRConfig) -> None:
        super().__init__(cfg)
        self._engine: Any = None

    def initialize(self) -> None:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]

        self._engine = RapidOCR()

    def recognize(self, image: Path, page_index: int) -> PageResult:
        result, _elapse = self._engine(str(image))
        elements = self._build_elements(result)
        width, height = self._image_size(image)
        payload = self._serialize(elements, width, height, page_index)
        return PageResult(
            page_index=page_index,
            width=width,
            height=height,
            elements=elements,
            markdown_ref="",
            source_json=None,
            raw_json=json.dumps(payload, ensure_ascii=False),
        )

    def from_json(self, page_json: str, page_index: int) -> PageResult:
        """Rebuild a PageResult from a cached RapidOCR JSON string."""
        payload = json.loads(page_json)
        elements = [
            Element(
                type=str(e["type"]),
                bbox=BBox(
                    x1=float(e["bbox"]["x1"]),
                    y1=float(e["bbox"]["y1"]),
                    x2=float(e["bbox"]["x2"]),
                    y2=float(e["bbox"]["y2"]),
                ),
                text=str(e["text"]),
                order_index=int(e["order_index"]),
                confidence=float(e["confidence"]) if e["confidence"] is not None else None,
            )
            for e in payload.get("elements", [])
        ]
        return PageResult(
            page_index=page_index,
            width=float(payload.get("width", 0)),
            height=float(payload.get("height", 0)),
            elements=elements,
            markdown_ref="",
            source_json=None,
            raw_json=page_json,
        )

    def close(self) -> None:
        self._engine = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_elements(self, result: Any) -> list[Element]:
        """Convert RapidOCR's ``[[box, text, score], ...]`` to Element list.

        Each ``box`` is a 4-point polygon ``[[x1,y1],[x2,y2],[x3,y3],[x4,y4]]``.
        We collapse it to an axis-aligned BBox via min/max. ``order_index`` is
        the list position (RapidOCR returns top-to-bottom, left-to-right).
        """
        if not result:
            return []
        elements: list[Element] = []
        for i, item in enumerate(result):
            if not item or len(item) < 3:
                continue
            box, text, score = item[0], item[1], item[2]
            bbox = _poly_to_bbox(box)
            try:
                conf = float(score) if score is not None else None
            except (TypeError, ValueError):
                conf = None
            elements.append(
                Element(
                    type="text",  # RapidOCR has no layout classification
                    bbox=bbox,
                    text=str(text) if text is not None else "",
                    order_index=i,
                    confidence=conf,
                )
            )
        return elements

    def _image_size(self, image: Path) -> tuple[float, float]:
        """Get image dimensions via Pillow (RapidOCR doesn't return them)."""
        try:
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError:
            return 0.0, 0.0
        try:
            with Image.open(image) as img:
                w, h = img.size
            return float(w), float(h)
        except (OSError, ValueError):
            return 0.0, 0.0

    def _serialize(
        self,
        elements: list[Element],
        width: float,
        height: float,
        page_index: int,
    ) -> dict:
        """Build the JSON payload for cache storage."""
        return {
            "page_index": page_index,
            "width": width,
            "height": height,
            "elements": [
                {
                    "type": el.type,
                    "bbox": {
                        "x1": el.bbox.x1,
                        "y1": el.bbox.y1,
                        "x2": el.bbox.x2,
                        "y2": el.bbox.y2,
                    },
                    "text": el.text,
                    "order_index": el.order_index,
                    "confidence": el.confidence,
                }
                for el in elements
            ],
        }


def _poly_to_bbox(poly: Any) -> BBox:
    """Convert a 4-point polygon ``[[x,y]*4]`` to an axis-aligned BBox.

    Falls back to ``safe_bbox`` (which handles malformed input by returning
    a zero bbox) when the polygon isn't a 4-element sequence of pairs.
    """
    try:
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        return BBox(x1=min(xs), y1=min(ys), x2=max(xs), y2=max(ys))
    except (TypeError, IndexError, ValueError):
        return safe_bbox(poly)


__all__ = ["RapidOCRBackend"]
