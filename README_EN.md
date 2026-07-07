# PDF2BOOK

> Let AI be your e-book typesetter — convert scanned PDFs to Kindle-friendly EPUB in one sentence

English | [中文](README.md)

PDF2BOOK is an AI-driven auto-typesetting tool. Rather than simply calling OCR, the AI acts like an editor — making all decisions that require "content understanding": judging page types, extracting metadata, proofreading OCR errors, inferring chapter structure, and ultimately generating an EPUB with a table of contents, chapter divisions, and Kindle-optimized formatting.

## Core Highlight: AI as Decision Maker, Not Tool Caller

Traditional conversion tools only "mechanically transport" content. PDF2BOOK lets AI handle 4 editorial decisions:

| AI Decision | What it does | Why AI is needed |
|---|---|---|
| **Page type identification** | Reviews OCR results, classifies cover/copyright/TOC/body/endpages | Requires understanding page content semantics; rules can't enumerate all cases |
| **Metadata extraction** | Extracts title, author, ISBN, language from copyright page | Scanned PDF embedded metadata is usually missing |
| **OCR proofreading** | Fixes typos, adjusts heading levels, cleans noise | OCR struggles with CJK punctuation and rare characters |
| **Layout parameter inference** | Analyzes heading distribution to distinguish story collections vs novels | Different book types need different chapter granularity |

**Trae Skill one-sentence trigger**: In Trae, say "convert XX.pdf to EPUB" and the AI automatically completes a 9-step decision chain (OCR → page analysis → metadata extraction → proofreading → layout inference → EPUB generation). See [`.trae/skills/pdf2book/SKILL.md`](.trae/skills/pdf2book/SKILL.md).

## Features

- **OCR recognition** — Based on PaddleOCR PP-StructureV3, recognizes text, titles, images, tables and other layout elements
- **Multiple OCR backends** — Supports `paddle_pp` (CPU default), `rapid_ocr` (lightweight), `paddle_vl` (GPU high-quality), `cloud_ocr` (remote API)
- **Header/footer removal** — Automatically detects and removes recurring headers, page numbers, running heads
- **Cross-page paragraph merging** — Correctly handles CJK punctuation, avoiding erroneous spaces at paragraph boundaries
- **Heading level inference** — Infers H1–H3 levels based on font size and chapter patterns (Chapter N / 第X章)
- **Image cropping** — Crops illustrations from rendered pages by OCR bbox, saves as standalone PNG references
- **Automatic page classification** — Rule-based identification of cover/frontispiece/copyright/TOC/body/endpages; decorative pages use PDF renders directly, body pages get OCR'd
- **CIP metadata extraction** — Extracts title, author, ISBN, publisher from copyright page OCR text following GB/T 12451 standard
- **Three-tier confidence marking** — Text classified as normal / low-confidence / dropped based on OCR recognition confidence; low-confidence text is preserved and marked for proofreading
- **AI review pipeline** — `--ai-review` enables LLM proofreading of low-confidence text, heading correction, metadata extraction, and book structure validation
- **Automatic TOC linking** — Converts "title／page-number" format TOC into clickable vertical link lists that jump to corresponding chapters
- **Batch processing** — `batch` subcommand converts all PDFs in a directory in parallel, each with independent work directory and cache
- **Resume support** — SQLite cache stores OCR results; `--resume` skips completed pages
- **Kindle-optimized typography** — Built-in `kindle.css`, `chapter_level` controls chapter granularity, each story/chapter gets its own page

## Installation

### System Requirements

- Python ≥ 3.10
- Pandoc (bundled automatically via `pypandoc_binary`, no separate install needed)

### Installation Steps

```bash
git clone https://github.com/charlesilcn/PDF2BOOK.git
cd PDF2BOOK
pip install -e ".[ocr,dev]"
```

> **Note**: `paddlepaddle` is large (~1.5GB model + dependencies). Users in China can use a mirror to speed up installation:
> ```bash
> pip install -e ".[ocr,dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```

### Optional Dependencies

| Extras | Description | Install command |
|---|---|---|
| `ocr` | PaddleOCR PP-StructureV3 (default OCR backend) | `pip install -e ".[ocr]"` |
| `rapid` | RapidOCR lightweight backend (~50MB) | `pip install -e ".[rapid]"` |
| `cloud` | Remote OCR API backend | `pip install -e ".[cloud]"` |
| `dev` | Testing and linting tools | `pip install -e ".[dev]"` |

## Usage

### Preparation

1. Prepare a scanned PDF book (e.g., `world_myths.pdf`)
2. (Optional) Create a `config.yaml` to adjust OCR and layout parameters — see [Configuration](#configuration)
3. Confirm dependencies are installed: `pip install -e ".[ocr,dev]"`

### Two-Stage Workflow (Recommended)

First generate a previewable Markdown, then build the EPUB after editing. Suitable for scenarios requiring manual OCR proofreading.

**Stage 1: PDF → OCR → Markdown**

```bash
pdf2book ocr world_myths.pdf --config config.yaml
```

This generates the following in the working directory (default `.pdf2book/`):

| File | Description |
|---|---|
| `.pdf2book/book.md` | Full-text Markdown from OCR, editable |
| `.pdf2book/meta.md` | Metadata YAML (title, author, language, etc.) |
| `.pdf2book/pages/page_NNNN.png` | Per-page renders (can be used as cover) |
| `.pdf2book/images/pN_eM.png` | Cropped illustrations |
| `.pdf2book/cache.db` | SQLite cache for resume support |

**Edit intermediate output (optional but recommended)**

OCR results may contain minor errors. Manual proofreading before building the EPUB is recommended:

- Edit `book.md`: fix typos, adjust heading levels (`#`/`##`/`###`), remove irrelevant content
- Edit `meta.md`: fill in correct title and author. Format:

```yaml
---
title: World Myths and Legends
author: Xu Chen
lang: en
date: '2026-07-06'
---
```

**Stage 2: Markdown → EPUB**

```bash
pdf2book epub .pdf2book/book.md -o world_myths.epub \
    --cover .pdf2book/pages/page_0000.png
```

`--cover` specifies the cover image — recommended to use the first page render (`page_0000.png`). The EPUB will auto-split by `chapter_level` and generate a TOC by `toc_depth`.

### One-Shot Mode

Complete PDF → EPUB conversion in one step without previewing intermediates:

```bash
pdf2book convert world_myths.pdf -o world_myths.epub \
    --cover .pdf2book/pages/page_0000.png \
    --config config.yaml
```

### Batch Processing

Convert all PDFs in a directory:

```bash
pdf2book batch ./pdfs/ -o ./epubs/ --workers 2 --config config.yaml
```

Each PDF gets its own `work_dir/{stem}/` subdirectory and SQLite cache. RapidOCR uses ~50MB/process (high concurrency OK); PaddlePP uses ~1.5GB/process (recommend `--workers 1-2`).

### Resume

OCR is the most time-consuming stage. If interrupted, use `--resume` to recover from cache and skip completed pages:

```bash
pdf2book ocr world_myths.pdf --resume --config config.yaml
```

### Enable AI Proofreading

Configure the `ai_review` section in `config.yaml`, then use the `--ai-review` flag:

```yaml
ai_review:
  enabled: true
  api_url: "https://api.openai.com/v1/chat/completions"
  api_key: "your-api-key"
  model: "gpt-4o-mini"
```

```bash
pdf2book convert world_myths.pdf -o out.epub --ai-review --config config.yaml
```

AI review will: proofread low-confidence OCR text, fix garbled headings, supplement metadata, validate chapter structure, and linkify the TOC.

### Common Scenarios

**Scenario 1: Story collection / short story anthology**

Each story is an H3 heading, and you want each story on its own page with TOC navigation:

```yaml
epub:
  toc_depth: 3        # TOC shows story titles
  chapter_level: 3    # Each H3 story gets its own page
```

**Scenario 2: Scanned book with cover/copyright/TOC pages**

The page classifier automatically identifies cover, frontispiece, copyright, and TOC pages as decorative — these use PDF renders directly (no OCR), while body pages get OCR'd and content extracted. No manual `skip_pages` configuration needed.

**Scenario 3: Novel with chapter-based pagination**

Chapters are H1 (`Chapter N`), and you want each chapter on its own page:

```yaml
epub:
  toc_depth: 2        # TOC shows chapter titles
  chapter_level: 1    # Each H1 chapter gets its own page
```

## CLI Reference

Four subcommands, invoked via `pdf2book <subcommand>` (also supports `python -m pdf2book`):

### `pdf2book ocr` — Stage 1: PDF → Markdown

```
pdf2book ocr PDF [OPTIONS]

Options:
  --resume         Resume from cache, skip already-OCR'd pages
  --config PATH    Config YAML path (uses built-in defaults if omitted)
  --backend NAME   OCR backend: paddle_pp | rapid_ocr | paddle_vl | cloud_ocr
  --ai-review      Enable AI review (low-confidence proofreading + metadata + headings)
  -v, --verbose    Enable DEBUG logging
```

### `pdf2book epub` — Stage 2: Markdown → EPUB

```
pdf2book epub MARKDOWN -o OUTPUT [OPTIONS]

Options:
  -o, --output PATH   Output EPUB path (required)
  --meta PATH         Metadata YAML path (defaults to sibling meta.md)
  --cover PATH        Cover image path
  --css PATH          CSS stylesheet path (defaults to built-in kindle.css)
  --config PATH       Config YAML path
  -v, --verbose       Enable DEBUG logging
```

### `pdf2book convert` — One-Shot Mode

```
pdf2book convert PDF -o OUTPUT [OPTIONS]

Options:
  -o, --output PATH   Output EPUB path (required)
  --resume            Resume from cache
  --config PATH       Config YAML path
  --backend NAME      OCR backend: paddle_pp | rapid_ocr | paddle_vl | cloud_ocr
  --cover PATH        Cover image path
  --ai-review         Enable AI review
  -v, --verbose       Enable DEBUG logging
```

### `pdf2book batch` — Batch Conversion

```
pdf2book batch INPUT_DIR -o OUTPUT_DIR [OPTIONS]

Options:
  -o, --output PATH   Output directory (required)
  --workers N         Parallel worker processes (memory scales linearly)
  --resume            Resume from cache
  --config PATH       Config YAML path
  --backend NAME      OCR backend
  --ai-review         Enable AI review
  -v, --verbose       Enable DEBUG logging
```

## Configuration

Specify a config file via `--config config.yaml`. Full field example in [`config.yaml`](config.yaml):

```yaml
ocr:
  backend: paddle_pp        # paddle_pp (CPU) | rapid_ocr | paddle_vl (GPU) | cloud_ocr
  dpi: 300                  # Render DPI; higher = clearer but slower
  use_region_detection: true
  use_table_recognition: false
  use_formula_recognition: false

postprocess:
  drop_header_footer: true
  merge_cross_page: true
  infer_title_level: true
  chapter_patterns:         # Chapter heading regexes for level inference
    - "第[一二三四五六七八九十百千0-9]+[章回节卷篇]"
    - "Chapter\\s+[IVX0-9]+"

epub:
  toc_depth: 2              # TOC depth (show up to H?)
  chapter_level: 1          # Pandoc --split-level; splits at this level
  css_path: null            # Custom CSS (default: src/pdf2book/epub/templates/kindle.css)
  cover: null               # Cover image (recommend passing via --cover CLI flag)

ai_review:
  enabled: false            # Off by default; enable with --ai-review flag
  api_url: ""               # OpenAI-compatible chat/completions endpoint
  api_key: ""               # API key
  model: "gpt-4o-mini"      # Model name (constraint validation loop ensures quality; cheap models OK)
  max_tokens: 8192          # Response token cap (large books need 8192 to avoid truncation)
```

**Layout tuning guide**:

| Book type | toc_depth | chapter_level | Description |
|---|---|---|---|
| Story collection / anthology | 3 | 3 | Each H3 story on its own page, TOC navigable |
| Long novel | 2 | 1 | Paginate by H1 chapter |
| Sectioned book | 2 | 2 | Paginate by H2 section |
| No chapter structure | 1 | 1 | Whole book as one page |

**Built-in CSS**: `src/pdf2book/epub/templates/kindle.css`, follows Kindle KDP constraints (no font-size on body/p, no flexbox/grid/@media), CJK line-height 1.75, first-line indent 2em, centered headings. Override with `--css`.

## Project Structure

```
src/pdf2book/
├── cli.py              # Typer CLI entry (ocr/epub/convert/batch subcommands)
├── __main__.py         # Supports python -m pdf2book
├── pipeline.py         # Two-stage pipeline orchestration
├── batch.py            # Batch parallel conversion
├── config.py           # Pydantic config models
├── ocr/                # OCR backends (paddle_pp/rapid_ocr/paddle_vl/cloud_ocr + abstract base)
├── postprocess/        # Post-processing
│   ├── processor.py        # Orchestration: header/footer, cross-page merge, heading levels, image crop
│   ├── header_footer.py    # Header/footer detection and removal
│   ├── merger.py           # Cross-page paragraph merging (CJK-punctuation-aware)
│   ├── structure.py        # Heading level inference + page classification dispatch
│   ├── page_classifier.py  # Rule-based page type identification (cover/frontispiece/copyright/TOC/body/endpage)
│   ├── cip_extractor.py    # CIP metadata extraction (GB/T 12451)
│   ├── confidence_filter.py # OCR confidence filtering and three-tier marking
│   ├── typography.py       # Chinese publishing typography rules
│   └── images.py           # Illustration cropping
├── review/             # AI review pipeline (enabled with --ai-review)
│   ├── markdown_review.py  # Collector + Prompt + Applier (incl. TOC linkification)
│   ├── ai_client.py        # LLM calls + retry + JSON repair
│   └── constraints.py      # Correction constraint extraction and validation
├── epub/               # EPUB construction
│   ├── builder.py          # Pandoc invocation + post-processing (remove auto-title page/fix ncx)
│   ├── metadata.py         # Metadata YAML read/write + BookMetadata
│   ├── toc_links.py        # TOC linkification plain-text fallback
│   └── templates/kindle.css # Kindle-optimized CSS
├── pdf/                # PDF rendering and metadata extraction
└── utils/              # SQLite cache, logging
```

## Trae Skill

The project includes a built-in Trae Skill that lets an AI agent automate the complete conversion flow:

- **Location**: [`.trae/skills/pdf2book/SKILL.md`](.trae/skills/pdf2book/SKILL.md)
- **Trigger**: In Trae, say "convert XX.pdf to EPUB"
- **Flow**: 9-step decision chain (OCR → page analysis → metadata extraction → OCR proofreading → layout inference → EPUB generation)

Under the Skill path, the AI agent itself handles all "content understanding" decisions — no external LLM API key required.

## Contributing

Contributions are welcome! Please follow this workflow:

1. **Fork** the repository and clone locally
2. **Create a branch**: `git checkout -b feature/your-feature-name`
3. **Install dev dependencies**: `pip install -e ".[ocr,dev]"`
4. **Write code** and ensure checks pass:
   ```bash
   ruff check src/          # Lint
   ruff format src/         # Format
   pytest -v -m "not slow"  # Run fast tests (no OCR model needed)
   ```
5. **Commit changes**: Use conventional commit messages (e.g., `feat: add xxx` / `fix: resolve xxx`)
6. **Create a Pull Request**: Describe the changes and motivation

### Development Guidelines

- **Code style**: Use [ruff](https://docs.astral.sh/ruff/) for linting and formatting, line width 100
- **Type annotations**: All public functions require type annotations
- **Testing**: New features require tests; `slow`-marked tests require loading the real OCR model
- **Commit convention**: Follow [Conventional Commits](https://www.conventionalcommits.org/)

### Architecture

PDF2BOOK uses a modular design with core layers:

- **OCR layer** (`ocr/`): Pluggable OCR backends with a unified `OCRBackend` abstract base class
- **Post-processing layer** (`postprocess/`): Rule-based text processing including page classification, CIP extraction, confidence filtering
- **AI review layer** (`review/`): Optional LLM proofreading pipeline with constraint-validation retry loop
- **EPUB layer** (`epub/`): Pandoc-driven EPUB generation with Kindle-optimized CSS

For detailed PP-StructureV3 JSON field mappings and developer notes, see the [Developer Notes](#developer-notes) section in the Chinese README.

## License

[MIT License](LICENSE) — Copyright (c) 2026 pdf2book
