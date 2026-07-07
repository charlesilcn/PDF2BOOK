"""PaddleOCR-VL backend (reserved for NVIDIA GPU machines, high-quality mode).

Placeholder implementation. PP-StructureV3 (paddle_pp.py) is the default
backend for CPU and covers most use cases. PaddleVL would offer higher
accuracy on complex layouts (multi-column, mixed-formula, dense tables)
but requires:

  * NVIDIA GPU with CUDA support
  * `paddleocr-vl` package (separate from `paddleocr`)
  * ~6GB VRAM for inference

To enable on a GPU machine, install the optional dependency and replace
the `NotImplementedError` below with a real `PPStructureV3(device='gpu')`
initializer (mirroring `paddle_pp.py` but with `device='gpu'`). The
parsing logic (`_parse_elements`, `_build_score_map`, `from_json`) can be
reused verbatim from `PaddlePPBackend` since both share the PP-Structure
JSON schema.

For remote high-quality OCR without local GPU, use `backend='cloud_ocr'`
instead — see `cloud_ocr.py`.
"""

from __future__ import annotations

from pathlib import Path

from pdf2book.config import OCRConfig
from pdf2book.ocr.base import OCRBackend
from pdf2book.ocr.models import PageResult


class PaddleVLBackend(OCRBackend):
    """OCR backend using PaddleOCR-VL (requires NVIDIA GPU + paddleocr-vl)."""

    def __init__(self, cfg: OCRConfig) -> None:
        super().__init__(cfg)

    def initialize(self) -> None:
        raise NotImplementedError(
            "PaddleVLBackend requires an NVIDIA GPU + the `paddleocr-vl` "
            "package. On CPU use backend='paddle_pp'; for remote high-quality "
            "OCR use backend='cloud_ocr'. See the module docstring for "
            "enablement steps."
        )

    def recognize(self, image: Path, page_index: int) -> PageResult:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        pass
