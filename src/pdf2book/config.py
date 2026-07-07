"""Configuration models for pdf2book."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class OCRConfig(BaseModel):
    """OCR engine configuration.

    Backend tiers:
      * ``paddle_pp``  — default, CPU-friendly, full layout analysis (PaddleOCR PP-StructureV3)
      * ``rapid_ocr``  — lightweight, ~50MB onnxruntime, no layout classification
      * ``paddle_vl``  — high-quality, requires NVIDIA GPU (PaddleOCR-VL)
      * ``cloud_ocr``  — remote OCR API plugin (httpx, user-configured endpoint)

    The factory ``make_ocr_backend`` decides which backends are actually
    implemented; unknown/unimplemented backends raise ValueError there.
    """

    backend: Literal["paddle_pp", "rapid_ocr", "paddle_vl", "cloud_ocr"] = "paddle_pp"
    dpi: int = 300
    use_table_recognition: bool = False
    use_formula_recognition: bool = False
    use_region_detection: bool = True
    # CloudOCRBackend endpoint configuration. Empty string disables cloud.
    # The API must return PP-StructureV3-compatible JSON (parsing_res_list +
    # layout_det_res); see `cloud_ocr.py` for the request/response contract.
    cloud_api_url: str = ""
    cloud_api_key: str = ""


class PostprocessConfig(BaseModel):
    """Post-processing configuration."""

    drop_header_footer: bool = True
    merge_cross_page: bool = True
    infer_title_level: bool = True
    # CJK punctuation normalization (Phase 5). Runs as step 0.5 (before
    # confidence filtering) so downstream stages see clean text. Converts
    # half-width punctuation to full-width in CJK context, compresses
    # repeated punctuation, and pairs ASCII quotes into CJK quotes.
    normalize_punctuation: bool = True
    # Skip OCR + post-processing for the first N pages (cover, sub-cover,
    # copyright/colophon, TOC, etc.) and the last M pages (colophon, ads).
    # Page renders are still produced so ``--cover`` can use page 0.
    skip_first_pages: int = 0
    skip_last_pages: int = 0
    # H1 chapter heading patterns (第X章/回/卷/篇, Chapter N). `节` is
    # intentionally excluded — it's a section (H2), see `section_patterns`.
    # Patterns are compiled with `re.match` (anchored at start); `re.IGNORECASE`
    # is applied so users don't need to bracket both cases of `Chapter`.
    chapter_patterns: list[str] = Field(
        default_factory=lambda: [
            r"第[一二三四五六七八九十百千0-9]+[章回卷篇]",
            r"Chapter\s+[IVX0-9]+",
        ]
    )
    # H2 section heading patterns (第X节). Separate from `chapter_patterns`
    # so users can tune sections independently (e.g. disable section detection
    # for books that only have chapters).
    section_patterns: list[str] = Field(
        default_factory=lambda: [
            r"第[一二三四五六七八九十百千0-9]+节",
        ]
    )
    # Three-tier confidence marking (Phase 4 refactor). Replaces the former
    # binary `min_confidence` drop-or-keep behavior with a graded scheme:
    #   score < noise_confidence            + (empty or single-char)  -> dropped
    #   score < low_confidence_threshold    + non-empty multi-char    -> low_confidence=True
    #   score >= low_confidence_threshold                            -> normal (kept)
    # `confidence=None` is conservatively preserved (not dropped, not flagged).
    # `low_confidence` elements survive into book.md with a `>[low-confidence]`
    # marker and are collected into review.json for AI correction.
    noise_confidence: float = 0.3
    low_confidence_threshold: float = 0.5
    # Kept for backward compatibility; new code should use the two fields above.
    # When `min_confidence > 0.0` it is mapped to `low_confidence_threshold`
    # by the filter (legacy callers still work).
    min_confidence: float = 0.5
    confidence_filter_types: list[str] = Field(
        default_factory=lambda: ["text", "paragraph_title", "doc_title", "content_title"]
    )


class EpubConfig(BaseModel):
    """EPUB generation configuration."""

    css_path: Path | None = None
    cover: Path | None = None
    toc_depth: int = 2
    # Pandoc `--split-level` (formerly `--epub-chapter-level`): splits headers
    # at this level (and above) into separate EPUB XHTML files, which is the
    # Kindle page-break mechanism. Decoupled from `toc_depth` (a display
    # concern) so a book can show H1+H2 in the TOC while only splitting at H1.
    chapter_level: int = 1


class AIReviewConfig(BaseModel):
    """AI review configuration (Phase 5).

    Pure-text LLM review (no multimodal). When `enabled=False` the pipeline
    skips the AI review stage entirely; low-confidence texts keep their
    `>[low-confidence]` markers in book.md for manual proofreading.

    The API is OpenAI-compatible (chat/completions with JSON response).
    Set `api_url` to your endpoint (e.g. "https://api.openai.com/v1/chat/completions"
    or a local LLM server). `model` defaults to a cheap model since the
    constraint-validation loop keeps quality high even with weaker models.
    """

    enabled: bool = False
    api_url: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    max_tokens: int = 4096
    # Constraint-validation retry loop: if AI correction violates constraints
    # (edit distance, char count, preserved chars), the violation is fed back
    # to the AI for a retry. After `max_retries` failures the original text
    # is kept with a [需校对] marker.
    max_retries: int = 3
    # Per-request timeout (seconds). A single review request covers all
    # low-confidence texts in one batch, so this should accommodate large books.
    timeout: float = 120.0


class AppConfig(BaseModel):
    """Top-level application configuration."""

    ocr: OCRConfig = Field(default_factory=OCRConfig)
    postprocess: PostprocessConfig = Field(default_factory=PostprocessConfig)
    epub: EpubConfig = Field(default_factory=EpubConfig)
    ai_review: AIReviewConfig = Field(default_factory=AIReviewConfig)
    cache_db: Path = Path(".pdf2book/cache.db")
    work_dir: Path = Path(".pdf2book")
    # Batch processing parallelism (Phase 4). Each worker is a subprocess
    # that loads its own OCR model — memory scales linearly with workers.
    # RapidOCR ~50MB/worker (high concurrency OK); PaddlePP ~1.5GB/worker
    # (recommend 1-2 workers). Set to 1 for serial execution.
    max_workers: int = 1

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        """Load config from a YAML file."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)

    @classmethod
    def default(cls) -> "AppConfig":
        """Return default config."""
        return cls()
