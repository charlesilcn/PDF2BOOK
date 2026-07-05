"""Configuration models for pdf2book."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class OCRConfig(BaseModel):
    """OCR engine configuration."""

    backend: Literal["paddle_pp", "paddle_vl"] = "paddle_pp"
    dpi: int = 300
    use_table_recognition: bool = False
    use_formula_recognition: bool = False
    use_region_detection: bool = True


class PostprocessConfig(BaseModel):
    """Post-processing configuration."""

    drop_header_footer: bool = True
    merge_cross_page: bool = True
    infer_title_level: bool = True
    # Skip OCR + post-processing for the first N pages (cover, sub-cover,
    # copyright/colophon, TOC, etc.) and the last M pages (colophon, ads).
    # Page renders are still produced so ``--cover`` can use page 0.
    skip_first_pages: int = 0
    skip_last_pages: int = 0
    chapter_patterns: list[str] = Field(
        default_factory=lambda: [
            r"第[一二三四五六七八九十百千0-9]+[章回节卷篇]",
            r"Chapter\s+[IVX0-9]+",
        ]
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


class AppConfig(BaseModel):
    """Top-level application configuration."""

    ocr: OCRConfig = Field(default_factory=OCRConfig)
    postprocess: PostprocessConfig = Field(default_factory=PostprocessConfig)
    epub: EpubConfig = Field(default_factory=EpubConfig)
    cache_db: Path = Path(".pdf2book/cache.db")
    work_dir: Path = Path(".pdf2book")

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
