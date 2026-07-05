"""PaddleOCR-VL backend (reserved for NVIDIA GPU machines, high-quality mode).

Placeholder implementation. PP-StructureV3 (paddle_pp.py) is the default backend
for CPU. This module will be filled in when the VL pipeline is wired up.
"""

from __future__ import annotations

from pathlib import Path

from pdf2book.config import OCRConfig
from pdf2book.ocr.base import OCRBackend
from pdf2book.ocr.models import PageResult


class PaddleVLBackend(OCRBackend):
    """OCR backend using PaddleOCR-VL-1.6 (requires NVIDIA GPU)."""

    def __init__(self, cfg: OCRConfig) -> None:
        super().__init__(cfg)

    def initialize(self) -> None:
        raise NotImplementedError(
            "PaddleVLBackend is reserved for NVIDIA GPU machines. "
            "Use backend='paddle_pp' for CPU mode."
        )

    def recognize(self, image: Path, page_index: int) -> PageResult:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        pass
