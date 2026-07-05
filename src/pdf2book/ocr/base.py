"""OCR backend abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from pathlib import Path

from pdf2book.config import OCRConfig
from pdf2book.ocr.models import PageResult


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
    """Factory: create an OCR backend by name."""
    if cfg.backend == "paddle_pp":
        from pdf2book.ocr.paddle_pp import PaddlePPBackend

        return PaddlePPBackend(cfg)
    if cfg.backend == "paddle_vl":
        from pdf2book.ocr.paddle_vl import PaddleVLBackend

        return PaddleVLBackend(cfg)
    raise ValueError(f"Unknown OCR backend: {cfg.backend}")
