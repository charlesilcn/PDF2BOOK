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
from pdf2book.ocr.base import OCRBackend, safe_bbox
from pdf2book.ocr.models import Element, PageResult


def _lookup_score(
    score_map: dict[tuple[int, int, int, int], float], bbox_raw: Any
) -> float | None:
    """Look up the layout-detection score for a `block_bbox`.

    `score_map` is keyed by int tuples built from `layout_det_res.boxes[j].coordinate`.
    `bbox_raw` is `parsing_res_list[i].block_bbox` (already int in current PP,
    but tolerated as float). Returns None if no match (conservative: caller
    leaves `Element.confidence=None` so downstream confidence filtering skips
    the element rather than dropping it).

    Note: this is the *layout detection* score (how confident the layout model
    is that this region exists), NOT the OCR recognition score. Prefer
    `_lookup_rec_score` for OCR-quality filtering — see Phase 4 refactor.
    """
    try:
        key = (int(bbox_raw[0]), int(bbox_raw[1]), int(bbox_raw[2]), int(bbox_raw[3]))
    except (TypeError, IndexError, ValueError):
        return None
    return score_map.get(key)


def _lookup_rec_score(
    rec_scores: list[tuple[tuple[int, int, int, int], float]], bbox_raw: Any
) -> float | None:
    """Look up the OCR recognition score for a `block_bbox`.

    `rec_scores` is a list of (rec_box, rec_score) pairs from
    `overall_ocr_res.rec_boxes` + `overall_ocr_res.rec_scores`. Each rec_box
    is one recognized text line's bbox [x1, y1, x2, y2].

    Matching rule: a rec_box belongs to a block when its center point falls
    inside the block_bbox. A block may contain multiple text lines; we return
    the mean of their rec_scores (a paragraph of 3 lines → average of 3).

    Returns None if no rec_box's center falls inside the block (conservative:
    caller falls back to `_lookup_score` for layout-detection score, or
    ultimately leaves `Element.confidence=None`).
    """
    try:
        bx1, by1, bx2, by2 = (
            int(bbox_raw[0]),
            int(bbox_raw[1]),
            int(bbox_raw[2]),
            int(bbox_raw[3]),
        )
    except (TypeError, IndexError, ValueError):
        return None

    matched: list[float] = []
    for rec_box, rec_score in rec_scores:
        rx1, ry1, rx2, ry2 = rec_box
        cx = (rx1 + rx2) / 2
        cy = (ry1 + ry2) / 2
        if bx1 <= cx <= bx2 and by1 <= cy <= by2:
            matched.append(rec_score)

    if not matched:
        return None
    return sum(matched) / len(matched)


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

        `confidence` is the OCR recognition score (Phase 4 refactor):
          1. Primary: `overall_ocr_res.rec_scores[j]` matched to `block_bbox`
             by center-point containment of `rec_boxes[j]` inside the block.
             A block matching multiple rec_boxes gets their mean score
             (paragraph of N text lines → average of N rec_scores).
          2. Fallback: `layout_det_res.boxes[j].score` matched by int-tuple
             key (used when overall_ocr_res is absent, e.g. image-only pages).
          3. None: when neither matches (conservative: downstream confidence
             filtering skips None rather than dropping).
        """
        blocks = json_data.get("parsing_res_list") or []
        score_map = self._build_score_map(json_data)
        rec_scores = self._build_rec_scores(json_data)
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
            # Prefer OCR recognition score (reflects text quality) over
            # layout detection score (reflects region existence).
            confidence = _lookup_rec_score(rec_scores, bbox_raw)
            if confidence is None:
                confidence = _lookup_score(score_map, bbox_raw)
            elements.append(
                Element(
                    type=str(label),
                    bbox=safe_bbox(bbox_raw),
                    text=str(content),
                    order_index=order,
                    title_level=None,
                    confidence=confidence,
                )
            )
        return elements

    def _build_score_map(
        self, json_data: dict
    ) -> dict[tuple[int, int, int, int], float]:
        """Build {bbox_int_tuple: score} from `layout_det_res.boxes`.

        PP-StructureV3 stores detection scores in
        `layout_det_res.boxes[j].score` with float `coordinate` arrays.
        `parsing_res_list[i].block_bbox` is the int-truncated form of the
        same coordinates. Keying by both `int()` and `round()` forms absorbs
        PP's int-conversion variance (truncation vs rounding) so the lookup
        hits regardless of which conversion PP used for a given block.

        Note: this is the *layout detection* score (region existence
        confidence), NOT the OCR recognition score. Used as fallback when
        `overall_ocr_res.rec_scores` is unavailable.
        """
        score_map: dict[tuple[int, int, int, int], float] = {}
        layout = json_data.get("layout_det_res") or {}
        for box in layout.get("boxes") or []:
            if not isinstance(box, dict):
                continue
            coord = box.get("coordinate")
            score = box.get("score")
            if not coord or score is None:
                continue
            try:
                f = [float(c) for c in coord]
            except (TypeError, IndexError, ValueError):
                continue
            for key in (
                (int(f[0]), int(f[1]), int(f[2]), int(f[3])),
                (round(f[0]), round(f[1]), round(f[2]), round(f[3])),
            ):
                score_map[key] = float(score)
        return score_map

    def _build_rec_scores(
        self, json_data: dict
    ) -> list[tuple[tuple[int, int, int, int], float]]:
        """Build [(rec_box_int_tuple, rec_score), ...] from `overall_ocr_res`.

        PP-StructureV3 stores per-text-line OCR recognition results in
        `overall_ocr_res.rec_boxes[j]` (bbox [x1,y1,x2,y2]) paired with
        `overall_ocr_res.rec_scores[j]` (float in [0,1]). Each entry is one
        recognized text line; a block may contain multiple lines.

        Returns a list (not a dict) because the matching is geometric
        (center-point containment), not key-equality — multiple rec_boxes
        can match the same block, and we want their mean score.

        Tolerates float coords by int-truncation; falls back to rec_polys
        (4-point polygon) flattened to bbox when rec_boxes is missing.
        """
        out: list[tuple[tuple[int, int, int, int], float]] = []
        ocr = json_data.get("overall_ocr_res") or {}
        if not isinstance(ocr, dict):
            return out

        rec_boxes = ocr.get("rec_boxes")
        rec_polys = ocr.get("rec_polys")
        rec_scores = ocr.get("rec_scores")
        if not rec_scores:
            return out

        # Build rec_boxes from rec_polys if rec_boxes is missing.
        if not rec_boxes and rec_polys:
            rec_boxes = []
            for poly in rec_polys:
                if not isinstance(poly, list) or len(poly) < 4:
                    continue
                try:
                    xs = [float(poly[k][0]) for k in range(4)]
                    ys = [float(poly[k][1]) for k in range(4)]
                except (TypeError, IndexError, ValueError):
                    continue
                rec_boxes.append([min(xs), min(ys), max(xs), max(ys)])

        if not rec_boxes:
            return out

        for box, score in zip(rec_boxes, rec_scores):
            if not isinstance(box, (list, tuple)) or len(box) < 4:
                continue
            if score is None:
                continue
            try:
                key = (int(box[0]), int(box[1]), int(box[2]), int(box[3]))
                out.append((key, float(score)))
            except (TypeError, IndexError, ValueError):
                continue
        return out

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
