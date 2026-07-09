"""Configuration models for pdf2book."""

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
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
    # Front matter / back matter patterns (前言, 序, 后记, 附录, etc.).
    # These are NEVER H1 — they're structural sections, not chapters.
    # `_enforce_monotonic` won't demote them below H2, preventing them
    # from stealing the ch-1 anchor from the first real chapter.
    front_matter_patterns: list[str] = Field(
        default_factory=lambda: [
            r"前言",
            r"序言",
            r"序$",
            r"后记",
            r"附录",
            r"绪论",
            r"引言",
            r"跋",
            r"结语",
            r"参考文献",
            r"目录",
            r"CONTENTS",
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

    LLM review with optional multimodal (vision) support. When
    ``enabled=False`` the pipeline skips the AI review stage entirely;
    low-confidence texts keep their ``>[low-confidence]`` markers in
    book.md for manual proofreading.

    The API is OpenAI-compatible (chat/completions with JSON response).
    Set ``api_url`` to your endpoint (e.g. "https://api.openai.com/v1/chat/completions"
    or a local LLM server). ``model`` defaults to a cheap model since the
    constraint-validation loop keeps quality high even with weaker models.

    When ``multimodal=True``, page images are sent alongside the text
    prompt for low-confidence OCR correction and title review. Requires a
    vision-capable model (e.g. gpt-4o-mini). Falls back to text-only on
    encoding failure or API error.
    """

    enabled: bool = False
    api_url: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    # 8192 accommodates large books with many title/low-confidence fixes.
    # 4096 was found to truncate mid-JSON on books with ~25+ too_short
    # titles, causing _parse_json_lenient to return a partial list instead
    # of the expected dict, silently skipping all corrections. Each batch
    # is small (5 items), so valid JSON is ~200-500 tokens; the extra headroom
    # absorbs occasional verbose model output before truncation triggers a
    # retry via the batch-splitting mechanism below.
    max_tokens: int = 8192
    # Truncation retry depth: when finish_reason="length", the batch is split
    # in half and each half is retried recursively, up to this many levels.
    # With max_retries=3, a 5-item batch can be split 3 times (5→2+3→1+1+1+2→
    # all singletons), so even severely truncation-prone batches eventually
    # produce results. Originally documented as a constraint-validation
    # retry loop; that feature was removed when the pipeline switched to
    # Markdown-based review, and the field was repurposed for batch retry.
    max_retries: int = 3
    # Per-request timeout (seconds). A single review request covers all
    # low-confidence texts in one batch, so this should accommodate large books.
    timeout: float = 120.0
    # Multimodal vision support (opt-in). When True, page images are
    # attached to the review prompt for low-confidence OCR correction and
    # title review. Requires a vision-capable model. When False (default),
    # review is pure-text only.
    multimodal: bool = False
    # Maximum page images per review request. Page images at 300 DPI are
    # ~2-5MB each; this limits request size and latency. When the number
    # of issue pages exceeds this, low-confidence pages are prioritized
    # over title-issue pages.
    max_images: int = 8


_ENV_VAR_RE = re.compile(r"\$\{([^}:-]+)(?::-([^}]*))?\}")


def _expand_env_vars(value: str) -> str:
    """Expand ${VAR:-default} environment variable references in a string.

    Example: "${PDF2BOOK_API_KEY:-}" → os.environ["PDF2BOOK_API_KEY"] or ""
    """
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default = match.group(2) or ""
        return os.environ.get(var_name, default)

    return _ENV_VAR_RE.sub(replace, value)


def _expand_env_vars_in_dict(d: dict) -> dict:
    """Recursively expand environment variables in dict values."""
    result = {}
    for key, value in d.items():
        if isinstance(value, dict):
            result[key] = _expand_env_vars_in_dict(value)
        elif isinstance(value, str):
            result[key] = _expand_env_vars(value)
        else:
            result[key] = value
    return result


class AppConfig(BaseModel):
    """Top-level application configuration."""

    ocr: OCRConfig = Field(default_factory=OCRConfig)
    postprocess: PostprocessConfig = Field(default_factory=PostprocessConfig)
    epub: EpubConfig = Field(default_factory=EpubConfig)
    ai_review: AIReviewConfig = Field(default_factory=AIReviewConfig)
    # Standard three-folder layout: inbox (drop PDFs) -> library (EPUBs)
    # -> workspace (per-book intermediate artifacts, visible for debugging).
    input_dir: Path = Path("inbox")
    output_dir: Path = Path("library")
    cache_db: Path = Path("workspace/cache.db")
    work_dir: Path = Path("workspace")
    # Batch processing parallelism (Phase 4). Each worker is a subprocess
    # that loads its own OCR model — memory scales linearly with workers.
    # RapidOCR ~50MB/worker (high concurrency OK); PaddlePP ~1.5GB/worker
    # (recommend 1-2 workers). Set to 1 for serial execution.
    max_workers: int = 1

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        """Load config from a YAML file.

        Applies AI review auto-enable rule: when ``ai_review.api_key`` is
        non-empty and ``enabled`` was not explicitly set in the YAML, AI
        review is turned on automatically so users get "configure api_key →
        one command works" behavior. An explicit ``enabled: false`` (Skill
        path) is respected and never overridden.
        """
        load_dotenv(Path(path).parent / ".env", override=False)
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        raw = _expand_env_vars_in_dict(raw)
        cfg = cls.model_validate(raw)
        cfg._auto_enable_ai_review(raw)
        return cfg

    def _auto_enable_ai_review(self, raw: dict) -> None:
        """Enable AI review when api_key is set and enabled was not explicit.

        Rule matrix:
          - ``enabled: true``              → stays True
          - ``enabled: false`` + api_key   → stays False (Skill escape hatch)
          - ``enabled`` absent + api_key   → auto True (CLI mode)
          - ``enabled`` absent + no key    → stays False (default/Skill)
        """
        ai_raw = raw.get("ai_review") or {}
        if "enabled" not in ai_raw and self.ai_review.api_key:
            self.ai_review.enabled = True

    @classmethod
    def default(cls) -> "AppConfig":
        """Return default config."""
        return cls()


def isolate_work_dir(cfg: AppConfig, pdf_stem: str) -> None:
    """Isolate ``work_dir`` and ``cache_db`` under a per-book subdirectory.

    Must be called BEFORE ``ConversionPipeline(cfg)`` construction —
    ``PostProcessor.__init__`` captures ``cfg.work_dir`` by value at
    construction time, so later mutations won't propagate to image
    extraction (``images.extract_images`` uses the captured path).

    Trailing whitespace is stripped from ``pdf_stem`` to avoid Windows
    path issues (directory names with trailing spaces cause SQLite
    ``unable to open database file`` errors).
    """
    clean_stem = pdf_stem.rstrip()
    cfg.work_dir = cfg.work_dir / clean_stem
    cfg.cache_db = cfg.work_dir / "cache.db"
