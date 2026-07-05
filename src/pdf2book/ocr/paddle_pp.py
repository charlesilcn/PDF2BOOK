"""PaddleOCR PP-StructureV3 backend (default, CPU-friendly).

Field names verified against spike fixture (tests/fixtures/spike_output/
sample_page1_res.json) for paddleocr==3.7.0. The parsing layer keeps alias
fallbacks (label/bbox/content/order_index) to absorb future schema drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pdf2book.config import OCRConfig
from pdf2book.ocr.base import OCRBackend
from pdf2book.ocr.models import BBox, Element, PageResult


def _safe_bbox(raw: Any) -> BBox:
    """Coerce a 4-element sequence into BBox, falling back to zeros."""
    try:
        return BBox(
            x1=float(raw[0]),
            y1=float(raw[1]),
            x2=float(raw[2]),
            y2=float(raw[3]),
        )
    except (TypeError, IndexError, ValueError):
        return BBox(x1=0, y1=0, x2=0, y2=0)


class PaddlePPBackend(OCRBackend):
    """OCR backend using PaddleOCR PP-StructureV3."""

    def __init__(self, cfg: OCRConfig) -> None:
        super().__init__(cfg)
        self._pipe: Any = None

    def initialize(self) -> None:
        from paddleocr import PPStructureV3

        self._pipe = PPStructureV3(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            use_table_recognition=self._cfg.use_table_recognition,
            use_formula_recognition=self._cfg.use_formula_recognition,
            use_region_detection=self._cfg.use_region_detection,
        )

    def recognize(self, image: Path, page_index: int) -> PageResult:
        results = list(self._pipe.predict(input=str(image)))
        if not results:
            return PageResult(
                page_index=page_index, width=0, height=0, elements=[], raw_json="{}"
            )
        res = results[0]
        json_data = self._extract_json(res)
        elements = self._parse_elements(json_data)
        width, height = self._page_size(json_data)
        markdown_ref = self._extract_markdown(res)
        return PageResult(
            page_index=page_index,
            width=width,
            height=height,
            elements=elements,
            markdown_ref=markdown_ref,
            source_json=None,
            raw_json=json.dumps(json_data, ensure_ascii=False),
        )

    def from_json(self, page_json: str, page_index: int) -> PageResult:
        """Rebuild a PageResult from a cached PP-Structure JSON string.

        Reuses `_extract_json` (handles both the `{"res":{...}}` runtime
        wrapper and the bare `save_to_json` shape) so resume works regardless
        of which form was cached.
        """
        data = json.loads(page_json)
        json_data = self._extract_json(data)
        elements = self._parse_elements(json_data)
        width, height = self._page_size(json_data)
        return PageResult(
            page_index=page_index,
            width=width,
            height=height,
            elements=elements,
            markdown_ref="",
            source_json=None,
            raw_json=page_json,
        )

    def _extract_json(self, res: Any) -> dict:
        """Extract the inner payload dict from a PP-Structure result.

        Handles two shapes:
          * runtime  res.json  -> {"res": {input_path, parsing_res_list, ...}}
          * save_to_json file  -> {input_path, parsing_res_list, ...} (no wrapper)
        """
        if hasattr(res, "json") and isinstance(res.json, dict):
            data = res.json
        elif hasattr(res, "to_dict"):
            data = res.to_dict()
        elif isinstance(res, dict):
            data = res
        else:
            return {}
        # Strip the "res" wrapper when present (runtime res.json shape).
        if isinstance(data.get("res"), dict):
            data = data["res"]
        return data

    def _parse_elements(self, json_data: dict) -> list[Element]:
        """Parse PP-Structure JSON into Element list.

        Verified field names (paddleocr 3.7.0):
          parsing_res_list[i] = {block_label, block_content, block_bbox,
                                 block_id, block_order}
        Alias fallbacks (label/bbox/content/order_index) absorb schema drift.
        title_level is intentionally NOT read: PP-StructureV3 does not
        serialize it into parsing_res_list (only into markdown output).
        T8 (TitleLevelInferrer) owns level inference from literary rules.
        """
        blocks = json_data.get("parsing_res_list") or []
        elements: list[Element] = []
        for i, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            label = block.get("block_label") or block.get("label") or "text"
            bbox_raw = block.get("block_bbox") or block.get("bbox") or [0, 0, 0, 0]
            content = block.get("block_content") or block.get("content") or ""
            order_raw = block.get("block_order", block.get("order_index", i))
            try:
                order = int(order_raw)
            except (TypeError, ValueError):
                order = i
            elements.append(
                Element(
                    type=str(label),
                    bbox=_safe_bbox(bbox_raw),
                    text=str(content),
                    order_index=order,
                    title_level=None,
                )
            )
        return elements

    def _page_size(self, json_data: dict) -> tuple[float, float]:
        """Get page dimensions from top-level width/height keys."""
        try:
            return float(json_data["width"]), float(json_data["height"])
        except (KeyError, TypeError, ValueError):
            return 0.0, 0.0

    def _extract_markdown(self, res: Any) -> str:
        """Best-effort extraction of PP's own markdown for debug comparison."""
        for attr in ("markdown", "md", "markdown_text"):
            val = getattr(res, attr, None)
            if isinstance(val, str) and val:
                return val
        return ""

    def close(self) -> None:
        self._pipe = None
