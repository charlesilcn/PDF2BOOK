"""OCR backend abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pdf2book.config import OCRConfig
from pdf2book.ocr.models import BBox, PageResult


def safe_bbox(raw: Any) -> BBox:
    """Coerce a 4-element sequence into BBox, falling back to zeros.

    Shared by all backends (PaddlePP, RapidOCR, PaddleVL, Cloud) so bbox
    parsing stays consistent. Tolerates int/float mixes and short/long
    sequences (returns a zero bbox on any error).
    """
    try:
        return BBox(
            x1=float(raw[0]),
            y1=float(raw[1]),
            x2=float(raw[2]),
            y2=float(raw[3]),
        )
    except (TypeError, IndexError, ValueError):
        return BBox(x1=0, y1=0, x2=0, y2=0)


class OCRBackend(ABC):
    """Abstract OCR backend.

    Subclasses implement `initialize`, `recognize`, and `close`.
    Use as a context manager to ensure resources are set up and released.
    """

    def __init__(self, cfg: OCRConfig) -> None:
        self._cfg = cfg
        self._initialized = False

    @abstractmethod
    def initialize(self) -> None:
        """Load models and prepare the backend."""

    @abstractmethod
    def recognize(self, image: Path, page_index: int) -> PageResult:
        """Recognize a single page image."""

    def recognize_many(self, images: Iterable[tuple[Path, int]]) -> Iterator[PageResult]:
        """Recognize multiple pages. Default: sequential."""
        for image, page_index in images:
            yield self.recognize(image, page_index)

    def from_json(self, page_json: str, page_index: int) -> PageResult:
        """Rebuild a PageResult from a cached JSON string.

        Backend-specific (each backend serializes its own JSON format).
        The default raises `NotImplementedError`; backends that support
        resume override this.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support resume (from_json)"
        )

    @abstractmethod
    def close(self) -> None:
        """Release resources."""

    def __enter__(self) -> OCRBackend:
        if not self._initialized:
            self.initialize()
            self._initialized = True
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def make_ocr_backend(cfg: OCRConfig) -> OCRBackend:
    """Factory: create an OCR backend by name.

    Backends are lazy-imported so optional dependencies (rapidocr, httpx)
    are only required when the user actually selects that backend.
    """
    if cfg.backend == "paddle_pp":
        from pdf2book.ocr.paddle_pp import PaddlePPBackend

        return PaddlePPBackend(cfg)
    if cfg.backend == "rapid_ocr":
        from pdf2book.ocr.rapid_ocr import RapidOCRBackend

        return RapidOCRBackend(cfg)
    if cfg.backend == "paddle_vl":
        from pdf2book.ocr.paddle_vl import PaddleVLBackend

        return PaddleVLBackend(cfg)
    if cfg.backend == "cloud_ocr":
        from pdf2book.ocr.cloud_ocr import CloudOCRBackend

        return CloudOCRBackend(cfg)
    raise ValueError(f"Unknown OCR backend: {cfg.backend}")
