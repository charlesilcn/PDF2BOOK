"""Data models for OCR results (global data contract)."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class BBox(BaseModel):
    """Bounding box in page coordinates (pixels)."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2


class Element(BaseModel):
    """A single document element (text block, title, image, table, etc.).

    `type` is an open string to stay compatible with PP-DocLayout_plus-L (20
    classes) and future PP-DocLayout-L (23 classes). Known labels include:
    text, doc_title, paragraph_title, abstract, content, reference, footnote,
    header, footer, aside_text, image, chart, table, seal, formula,
    display_formula, inline_formula, formula_number, algorithm, number.
    """

    type: str
    bbox: BBox
    text: str = ""
    children: list["Element"] = []
    order_index: int = 0
    title_level: int | None = None
    confidence: float | None = None
    inferred_level: int | None = None
    dropped: bool = False
    # Original OCR text for image/figure blocks; populated by
    # `postprocess.images.extract_images` before `el.text` is overwritten
    # with the relative image path. `to_markdown` emits it as a caption.
    image_caption: str | None = None
    # Three-tier confidence marking (Phase 4 refactor):
    #   - `dropped=True`                  : noise (score<noise_threshold, empty/single-char)
    #   - `low_confidence=True`           : suspect (score<low_threshold, non-empty multi-char)
    #   - neither set                     : normal (score>=low_threshold, or score is None)
    # `low_confidence` elements survive into book.md with a `>[low-confidence]` marker
    # and are collected into review.json for AI correction.
    low_confidence: bool = False
    # AI-corrected text (Phase 5). Set by `review.applier` after constraint
    # validation passes. `to_markdown` emits `ai_corrected` instead of `text`
    # when set; `None` means "not reviewed" or "AI returned [UNCLEAR]".
    ai_corrected: str | None = None
    model_config = ConfigDict(arbitrary_types_allowed=True)


class PageResult(BaseModel):
    """OCR result for a single page."""

    page_index: int
    width: float
    height: float
    elements: list[Element] = []
    markdown_ref: str = ""
    source_json: Path | None = None
    # Raw PP-Structure JSON string carried so the pipeline can cache it and
    # rebuild this PageResult on resume via `OCRBackend.from_json`. Postprocess
    # stages ignore this field; it exists purely for resume support.
    raw_json: str | None = None
    # Page classification result (Phase 3). Set by `page_classifier.classify_pages`.
    # One of: cover/frontispiece/copyright/toc/preface/body/illustration/appendix/unknown.
    # `to_markdown` skips decorative pages (cover/frontispiece/copyright/illustration);
    # `epub.builder` inserts their `page_image_path` as raw PDF page images.
    page_type: str = "unknown"
    # Rendered page image path (Phase 3). Set by the pipeline for every page;
    # used by `epub.builder` for decorative pages that bypass OCR-based layout.
    page_image_path: Path | None = None


Element.model_rebuild()
