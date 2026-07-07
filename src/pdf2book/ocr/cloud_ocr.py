"""Cloud OCR backend (Phase 3, high-quality tier via remote API).

Calls a user-configured HTTP endpoint that returns PP-StructureV3-compatible
JSON (``parsing_res_list`` + ``layout_det_res``). This lets users route page
images to a high-accuracy OCR service (e.g. a GPU-backed PaddleVL deployment,
a cloud OCR provider) without installing local model dependencies.

Request contract:
    POST ``{cfg.cloud_api_url}``
    Headers: ``Authorization: Bearer {cfg.cloud_api_key}`` (when key is set)
    Body: ``{"image": "<base64-encoded PNG bytes>", "page_index": N}``

Response contract (PP-Structure compatible):
    ``{"parsing_res_list": [...], "layout_det_res": {"boxes": [...]}, "width": W, "height": H}``

The response is parsed by the same logic as ``PaddlePPBackend`` (composition,
not inheritance) so the schema stays in sync without duplicating the parser.
``from_json`` delegates to ``PaddlePPBackend.from_json`` for the same reason.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from pdf2book.config import OCRConfig
from pdf2book.ocr.base import OCRBackend
from pdf2book.ocr.models import PageResult


class CloudOCRBackend(OCRBackend):
    """OCR backend that delegates recognition to a remote PP-compatible API."""

    def __init__(self, cfg: OCRConfig) -> None:
        super().__init__(cfg)
        self._client: Any = None
        # Compose a PaddlePPBackend (without initializing it) so we can reuse
        # its parsing logic for PP-Structure-compatible responses.
        from pdf2book.ocr.paddle_pp import PaddlePPBackend

        self._parser = PaddlePPBackend(cfg)

    def initialize(self) -> None:
        if not self._cfg.cloud_api_url:
            raise ValueError(
                "cloud_ocr backend requires OCRConfig.cloud_api_url to be set"
            )
        try:
            import httpx
        except ImportError as e:  # pragma: no cover - exercised via monkeypatch
            raise ImportError(
                "cloud_ocr backend requires httpx; install with `pip install "
                "pdf2book[cloud]` or `pip install httpx>=0.27`"
            ) from e

        headers = {"Accept": "application/json"}
        if self._cfg.cloud_api_key:
            headers["Authorization"] = f"Bearer {self._cfg.cloud_api_key}"
        self._client = httpx.Client(timeout=60.0, headers=headers)

    def recognize(self, image: Path, page_index: int) -> PageResult:
        if self._client is None:
            raise RuntimeError("CloudOCRBackend used outside `with` block")

        image_bytes = Path(image).read_bytes()
        image_b64 = base64.b64encode(image_bytes).decode("ascii")

        resp = self._client.post(
            self._cfg.cloud_api_url,
            json={"image": image_b64, "page_index": page_index},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"cloud_ocr API returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        json_data = resp.json()
        elements = self._parser._parse_elements(json_data)
        width, height = self._parser._page_size(json_data)
        # Pillow fallback when the API omits page dimensions.
        if width == 0.0 or height == 0.0:
            w, h = _image_size(image)
            width = width or w
            height = height or h

        return PageResult(
            page_index=page_index,
            width=width,
            height=height,
            elements=elements,
            markdown_ref="",
            source_json=None,
            raw_json=json.dumps(json_data, ensure_ascii=False),
        )

    def from_json(self, page_json: str, page_index: int) -> PageResult:
        """Delegate to PaddlePPBackend (PP-compatible JSON schema)."""
        return self._parser.from_json(page_json, page_index)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def _image_size(image: Path) -> tuple[float, float]:
    """Read image dimensions via Pillow (mirrors RapidOCRBackend strategy)."""
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


__all__ = ["CloudOCRBackend"]
