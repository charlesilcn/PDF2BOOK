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
- **AI review pipeline** — Auto-enables when `api_key` is set in `config.yaml`; LLM proofreads low-confidence text, fixes headings, extracts metadata, validates book structure; `epub` stage supports supplemental review (idempotent)
- **Automatic TOC linking** — Converts "title／page-number" format TOC into clickable vertical link lists that jump to corresponding chapters
- **Decoration image stripping** — Detects repeated decorative images (chapter dividers, flourishes) via perceptual hash (pHash) clustering and strips them from the EPUB; protects functional images (QR codes/barcodes) from misidentification
- **Multimodal visual review** — AI review can optionally attach page images to assist low-confidence text and title proofreading (requires a vision model like gpt-4o-mini)
- **Batch processing** — `batch` subcommand converts all PDFs in a directory in parallel, each with independent work directory and cache
- **Resume support** — SQLite cache stores OCR results; `--resume` skips completed pages
- **Kindle-optimized typography** — Built-in `kindle.css`, `chapter_level` controls chapter granularity, each story/chapter gets its own page

## Quick Start

The project uses a standard three-folder layout with zero configuration:

```
PDF2BOOK/
├── inbox/       # Drop PDFs to convert here
├── library/     # Generated EPUBs (named after the source PDF stem)
└── workspace/   # Intermediate artifacts (per-book subdirectory workspace/{stem}/)
```

**One command to convert**:

```bash
# 1. Put your PDF in inbox/
cp your_book.pdf inbox/

# 2. Run (no arguments)
pdf2book

# 3. EPUB appears in library/your_book.epub
```

Intermediate artifacts (`book.md`, `meta.md`, page renders, cache) are organized under `workspace/{book_title}/` for easy debugging and proofreading.

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

PDF2BOOK offers two usage modes, both letting AI fully take over proofreading/layout/metadata work so the user never needs to manually review:

| Mode | Use case | API key required | Who does the AI work |
|---|---|---|---|
| **CLI mode** | Command-line batch processing, script integration | Yes (in config.yaml) | External LLM (e.g. GPT-4o-mini) |
| **Skill mode** | Natural-language trigger in Trae IDE | No | Trae agent's own reasoning |

### Preparation

1. Prepare a scanned PDF book (e.g., `world_myths.pdf`)
2. Install dependencies: `pip install -e ".[ocr,dev]"`
3. (CLI mode) Fill in `api_key` in `config.yaml` (see below); Skill mode skips this step

### CLI Mode (requires apikey, AI fully takes over)

**Configure apikey**: Fill in your api_key in the `ai_review` section of `config.yaml`. Once set, AI review auto-enables — no extra flags needed:

```yaml
ai_review:
  api_url: "https://api.openai.com/v1/chat/completions"
  api_key: "your-api-key"    # AI review auto-enables once filled in
  model: "gpt-4o-mini"
```

> **Auto-enable rule**: AI review turns on when `api_key` is non-empty and `enabled` is not explicitly `false`. To force it off (e.g. the Skill path), pass `--no-ai-review` or write `enabled: false` explicitly.

**One-command PDF → EPUB (one-shot full pipeline)**

```bash
pdf2book convert inbox/world_myths.pdf
```

Default output to `library/world_myths.epub` (named after the PDF stem). AI handles everything: page classification, CIP metadata extraction, OCR typo correction, heading-level fixes, layout parameter inference, TOC linkification. No manual review needed.

**One-command PDF → Markdown (staged, previewable)**

```bash
pdf2book ocr inbox/world_myths.pdf
```

Generates `workspace/world_myths/book.md` + `workspace/world_myths/meta.md`. To manually fine-tune before building the EPUB, edit `book.md`/`meta.md` then run the command below.

**One-command Markdown → EPUB (from existing OCR results)**

```bash
pdf2book epub workspace/world_myths/book.md -o library/world_myths.epub \
    --cover workspace/world_myths/pages/page_0000.png
```

If `book.md` still contains `>[low-confidence]` markers (OCR ran without AI review), this command auto-supplements AI review before building the EPUB (idempotent: skips if already cleaned).

**Batch Processing**

```bash
pdf2book batch                       # Default: inbox/ → library/
# Or specify directories
pdf2book batch ./pdfs/ -o ./epubs/ --workers 2
```

Each PDF gets its own `workspace/{stem}/` subdirectory and SQLite cache. RapidOCR uses ~50MB/process; PaddlePP uses ~1.5GB/process (recommend `--workers 1-2`).

**Resume**

```bash
pdf2book ocr inbox/world_myths.pdf --resume
```

**Force-disable AI review** (e.g. to keep raw OCR results for manual proofreading):

```bash
pdf2book convert inbox/world_myths.pdf --no-ai-review
```

#### Output Artifacts

| File | Description |
|---|---|
| `workspace/{stem}/book.md` | Full-text Markdown after OCR + AI proofreading |
| `workspace/{stem}/meta.md` | Metadata YAML extracted by CIP/AI (title, author, language, etc.) |
| `workspace/{stem}/pages/page_NNNN.png` | Per-page renders (can be used as cover) |
| `workspace/{stem}/images/pN_eM.png` | Cropped illustrations |
| `workspace/{stem}/cache.db` | SQLite cache for resume support |

### Skill Mode (no apikey required, agent's own reasoning)

In the Trae IDE, say "convert XX.pdf to EPUB" and the AI agent automatically completes a 9-step decision chain:

1. Full OCR (auto page classification + CIP extraction + confidence filtering)
2. AI reviews page structure (agent reads book.md to verify classification)
3. AI proofreads metadata (agent cross-checks meta.md against cover/copyright pages)
4. Generate config.yaml
5. Regenerate book.md (from cache, applying latest config)
6. AI proofreads book.md (agent fixes low-confidence text, typos, heading levels)
7. AI infers layout parameters (agent analyzes heading distribution to decide toc_depth/chapter_level)
8. Generate final EPUB

Under the Skill path, all `pdf2book` commands pass `--no-ai-review` and `config.yaml` explicitly writes `ai_review.enabled: false`, ensuring no external LLM calls. AI work is done by the agent itself using Read/Grep/Edit tools.

See [`.trae/skills/pdf2book/SKILL.md`](.trae/skills/pdf2book/SKILL.md) for details.

### Migration Note (for old --ai-review users)

The old `--ai-review` flag has been removed. Migration:
- Old usage: `pdf2book convert book.pdf -o out.epub --ai-review`
- New usage: fill `api_key` in `config.yaml`, then `pdf2book convert book.pdf -o out.epub`
- Force off: add `--no-ai-review`

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

Running `pdf2book` (no arguments) is equivalent to `pdf2book batch inbox -o library`, auto-scanning inbox/ and outputting to library/. Four subcommands via `pdf2book <subcommand>` (also supports `python -m pdf2book`):

### `pdf2book` — Zero-argument default behavior

```
pdf2book
# Equivalent to: scan inbox/ for all PDFs → output to library/{stem}.epub
# Intermediate artifacts in workspace/{stem}/
```

### `pdf2book ocr` — Stage 1: PDF → Markdown

```
pdf2book ocr PDF [OPTIONS]

Options:
  --resume          Resume from cache, skip already-OCR'd pages
  --config PATH     Config YAML path (auto-discovers cwd config.yaml)
  --backend NAME    OCR backend: paddle_pp | rapid_ocr | paddle_vl | cloud_ocr
  --no-ai-review    Force-disable AI review (even if config.yaml has api_key)
  -v, --verbose     Enable DEBUG logging
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
  --no-ai-review      Force-disable AI review (auto-enables by default when
                      api_key is set and book.md has low-confidence markers,
                      supplemental review runs idempotently)
  -v, --verbose       Enable DEBUG logging
```

### `pdf2book convert` — One-Shot Mode

```
pdf2book convert [PDF] [-o OUTPUT] [OPTIONS]

# No PDF argument: process inbox/ → library/ (default behavior)
# PDF without -o: defaults to library/{stem}.epub

Options:
  -o, --output PATH   Output EPUB path (default: library/{stem}.epub)
  --resume            Resume from cache
  --config PATH       Config YAML path
  --backend NAME      OCR backend: paddle_pp | rapid_ocr | paddle_vl | cloud_ocr
  --cover PATH        Cover image path
  --no-ai-review      Force-disable AI review
  -v, --verbose       Enable DEBUG logging
```

### `pdf2book batch` — Batch Conversion

```
pdf2book batch [INPUT_DIR] [-o OUTPUT_DIR] [OPTIONS]

# Default: inbox/ → library/

Options:
  -o, --output PATH   Output directory (default: library/)
  --workers N         Parallel worker processes (memory scales linearly)
  --resume            Resume from cache
  --config PATH       Config YAML path
  --backend NAME      OCR backend
  --no-ai-review      Force-disable AI review
  -v, --verbose       Enable DEBUG logging
```

## Configuration

`pdf2book` auto-discovers `config.yaml` in the current directory (no `--config` needed). Full field example in [`config.yaml`](config.yaml):

```yaml
work_dir: workspace          # Intermediate artifacts root (per-book under workspace/{stem}/)
cache_db: workspace/cache.db # SQLite cache base path (actual: workspace/{stem}/cache.db)
input_dir: inbox             # PDF input directory
output_dir: library          # EPUB output directory

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
  enabled: false            # Explicit false disables even with api_key set (Skill path)
  api_url: ""               # OpenAI-compatible chat/completions endpoint
  api_key: ""               # Fill in to auto-enable AI review (no need for enabled: true)
  model: "gpt-4o-mini"      # Model name (constraint validation loop ensures quality; cheap models OK)
  max_tokens: 8192          # Response token cap (large books need 8192 to avoid truncation)
  multimodal: false         # Multimodal visual review (requires vision model, sends page images)
  max_images: 8             # Max page images per review request
```

**Environment variable configuration**: API keys can be managed via `.env` file to avoid hardcoding in `config.yaml`:

```bash
# Copy the template and fill in your real key
cp .env.example .env
# Edit .env: PDF2BOOK_API_KEY=sk-your-key
```

Use `${VAR:-default}` syntax in `config.yaml` to reference environment variables:
```yaml
ai_review:
  api_key: ${PDF2BOOK_API_KEY:-}     # Read from .env, empty if absent
  api_url: ${PDF2BOOK_API_URL:-https://api.openai.com/v1/chat/completions}
```

> The `.env` file is excluded by `.gitignore` and won't be uploaded to GitHub. `.env.example` is a template file and safe to commit.

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
PDF2BOOK/
├── inbox/                 # Drop PDFs to convert here (zero-config entry)
├── library/               # Generated EPUBs (named after source PDF stem)
├── workspace/             # Intermediate artifacts (per-book subdirectory workspace/{stem}/)
├── config.yaml            # Config file (auto-loaded, no --config needed)
└── src/pdf2book/
    ├── cli.py              # Typer CLI entry (no-arg default processes inbox/ → library/)
    ├── __main__.py         # Supports python -m pdf2book
    ├── pipeline.py         # Two-stage pipeline orchestration
    ├── batch.py            # Batch parallel conversion (calls isolate_work_dir per book)
    ├── config.py           # Pydantic config models + isolate_work_dir shared function
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
    │   ├── decorations.py      # Decoration image stripping (pHash clustering + separator detection)
    │   └── images.py           # Illustration cropping
    ├── review/             # AI review pipeline (auto-enables when config.yaml has api_key)
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
