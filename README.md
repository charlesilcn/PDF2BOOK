# PDF2BOOK

> Let AI be your e-book typesetter — convert scanned PDFs to Kindle-friendly EPUB in one sentence

English | [中文](README_CN.md)

PDF2BOOK is an AI-driven auto-typesetting tool. Rather than simply calling OCR, the AI acts like an editor — making all decisions that require "content understanding": judging page types, extracting metadata, proofreading OCR errors, inferring chapter structure, and ultimately generating an EPUB with a table of contents, chapter divisions, and Kindle-optimized formatting.

## Quick Start

**Three steps to your first EPUB:**

```bash
# 1. Install
git clone https://github.com/charlesilcn/PDF2BOOK.git
cd PDF2BOOK
pip install -e ".[ocr,dev]"

# 2. Drop a PDF
cp your_book.pdf inbox/

# 3. Convert
pdf2book
# → library/your_book.epub
```

That's it. The project uses a three-folder layout with zero configuration:

```
PDF2BOOK/
├── inbox/       # Drop PDFs here
├── library/     # Generated EPUBs (named after source PDF)
└── workspace/   # Intermediate artifacts (per-book workspace/{stem}/)
```

## Two Ways to Use

Both modes let AI fully handle proofreading, layout, and metadata — no manual review needed.

| Mode | Use case | API key | AI work done by |
|---|---|---|---|
| **CLI mode** | Command-line batch, scripting | Yes (config.yaml) | External LLM (e.g. GPT-4o-mini) |
| **Skill mode** | Natural language in any AI agent | No | Agent's own reasoning |

### CLI Mode

Fill in `api_key` in `config.yaml` — AI review auto-enables, no extra flags:

```yaml
ai_review:
  api_key: "your-api-key"    # Auto-enables once filled
  model: "gpt-4o-mini"
```

```bash
pdf2book convert inbox/your_book.pdf          # One-shot PDF → EPUB
pdf2book ocr inbox/your_book.pdf              # PDF → Markdown (staged)
pdf2book epub workspace/your_book/book.md -o library/your_book.epub  # Markdown → EPUB
pdf2book batch                                 # Batch: inbox/ → library/
pdf2book ocr inbox/your_book.pdf --resume      # Resume from cache
```

### Skill Mode (no API key needed)

Paste this into any AI agent's chat (Claude Code, Cursor, Codex, Trae, etc.) — it fetches the skill file and auto-installs:

```bash
curl -fsSL https://raw.githubusercontent.com/charlesilcn/PDF2BOOK/main/skills/pdf2book/SKILL.md
```

Then just say "convert XX.pdf to EPUB". The agent reads the 9-step workflow, auto-checks environment, clones the repo, installs dependencies, and runs the full conversion — no external API key needed.

- **Skill file**: [`skills/pdf2book/SKILL.md`](skills/pdf2book/SKILL.md)
- **Agent entry**: [`AGENTS.md`](AGENTS.md) (auto-read by Claude Code, Cursor, Codex, etc.)

## Installation

### System Requirements

- Python ≥ 3.10
- Pandoc (bundled via `pypandoc_binary`, no separate install)

### Steps

```bash
git clone https://github.com/charlesilcn/PDF2BOOK.git
cd PDF2BOOK
pip install -e ".[ocr,dev]"
```

> **Note**: `paddlepaddle` is large (~1.5GB model + deps). Users in China can use a mirror:
> ```bash
> pip install -e ".[ocr,dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
> ```

### Optional Dependencies

| Extras | Description | Install |
|---|---|---|
| `ocr` | PaddleOCR PP-StructureV3 (default backend) | `pip install -e ".[ocr]"` |
| `rapid` | RapidOCR lightweight (~50MB) | `pip install -e ".[rapid]"` |
| `cloud` | Remote OCR API backend | `pip install -e ".[cloud]"` |
| `dev` | Testing and linting tools | `pip install -e ".[dev]"` |

## Core Highlight: AI as Decision Maker

Traditional tools only "mechanically transport" content. PDF2BOOK lets AI handle 4 editorial decisions:

| AI Decision | What it does | Why AI is needed |
|---|---|---|
| **Page type identification** | Reviews OCR results, classifies cover/copyright/TOC/body/endpages | Requires understanding page semantics; rules can't enumerate all cases |
| **Metadata extraction** | Extracts title, author, ISBN, language from copyright page | Scanned PDF embedded metadata is usually missing |
| **OCR proofreading** | Fixes typos, adjusts heading levels, cleans noise | OCR struggles with CJK punctuation and rare characters |
| **Layout inference** | Analyzes heading distribution to distinguish story collections vs novels | Different book types need different chapter granularity |

## Features

- **OCR recognition** — PaddleOCR PP-StructureV3, recognizes text, titles, images, tables, layout elements
- **Multiple OCR backends** — `paddle_pp` (CPU default), `rapid_ocr` (lightweight), `paddle_vl` (GPU), `cloud_ocr` (remote API)
- **Header/footer removal** — Auto-detects and removes recurring headers, page numbers, running heads
- **Cross-page paragraph merging** — Correctly handles CJK punctuation, avoids erroneous spaces at paragraph boundaries
- **Heading level inference** — Infers H1–H3 from font size and chapter patterns (Chapter N / 第X章)
- **Image cropping** — Crops illustrations from rendered pages by OCR bbox, saves as standalone PNGs
- **Automatic page classification** — Rule-based cover/frontispiece/copyright/TOC/body/endpage detection; decorative pages use PDF renders, body pages get OCR'd
- **CIP metadata extraction** — Extracts title, author, ISBN, publisher from copyright page (GB/T 12451 standard)
- **Three-tier confidence marking** — normal / low-confidence / dropped based on OCR confidence; low-confidence text preserved and marked for proofreading
- **AI review pipeline** — Auto-enables with `api_key`; LLM proofreads low-confidence text, fixes headings, extracts metadata, validates structure; `epub` stage supports supplemental review (idempotent)
- **Automatic TOC linking** — Converts "title／page-number" TOC into clickable vertical link lists
- **Decoration image stripping** — pHash clustering detects repeated decorative images (dividers, flourishes) and strips them; protects functional images (QR codes/barcodes)
- **Multimodal visual review** — Optional page image attachment for low-confidence text proofreading (requires vision model)
- **Batch processing** — Parallel conversion with independent work directory and cache per PDF
- **Resume support** — SQLite cache stores OCR results; `--resume` skips completed pages
- **Kindle-optimized typography** — Built-in `kindle.css`, `chapter_level` controls chapter granularity, each story/chapter gets its own page

## CLI Reference

Running `pdf2book` (no args) equals `pdf2book batch inbox -o library`. Four subcommands:

### `pdf2book` — Zero-arg default

```
pdf2book
# Scans inbox/ → library/{stem}.epub
```

### `pdf2book ocr` — Stage 1: PDF → Markdown

```
pdf2book ocr PDF [OPTIONS]

Options:
  --resume          Resume from cache, skip already-OCR'd pages
  --config PATH     Config YAML path (auto-discovers cwd config.yaml)
  --backend NAME    OCR backend: paddle_pp | rapid_ocr | paddle_vl | cloud_ocr
  --no-ai-review    Force-disable AI review
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
  --no-ai-review      Force-disable AI review
  -v, --verbose       Enable DEBUG logging
```

### `pdf2book convert` — One-shot mode

```
pdf2book convert [PDF] [-o OUTPUT] [OPTIONS]

# No PDF: process inbox/ → library/
# PDF without -o: defaults to library/{stem}.epub

Options:
  -o, --output PATH   Output EPUB path (default: library/{stem}.epub)
  --resume            Resume from cache
  --config PATH       Config YAML path
  --backend NAME      OCR backend
  --cover PATH        Cover image path
  --no-ai-review      Force-disable AI review
  -v, --verbose       Enable DEBUG logging
```

### `pdf2book batch` — Batch conversion

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

### Output Artifacts

| File | Description |
|---|---|
| `workspace/{stem}/book.md` | Full-text Markdown after OCR + AI proofreading |
| `workspace/{stem}/meta.md` | Metadata YAML extracted by CIP/AI |
| `workspace/{stem}/pages/page_NNNN.png` | Per-page renders (can be used as cover) |
| `workspace/{stem}/images/pN_eM.png` | Cropped illustrations |
| `workspace/{stem}/cache.db` | SQLite cache for resume |

### Common Scenarios

**Story collection** — each H3 story on its own page:
```yaml
epub:
  toc_depth: 3
  chapter_level: 3
```

**Novel** — paginate by H1 chapter:
```yaml
epub:
  toc_depth: 2
  chapter_level: 1
```

**Scanned book with cover/TOC** — page classifier auto-identifies decorative pages, no manual `skip_pages` needed.

## Configuration

`pdf2book` auto-discovers `config.yaml` in the current directory. Full example in [`config.yaml`](config.yaml):

```yaml
work_dir: workspace
cache_db: workspace/cache.db
input_dir: inbox
output_dir: library

ocr:
  backend: paddle_pp        # paddle_pp | rapid_ocr | paddle_vl | cloud_ocr
  dpi: 300
  use_region_detection: true

postprocess:
  drop_header_footer: true
  merge_cross_page: true
  infer_title_level: true
  chapter_patterns:
    - "第[一二三四五六七八九十百千0-9]+[章回节卷篇]"
    - "Chapter\\s+[IVX0-9]+"

epub:
  toc_depth: 2              # TOC depth
  chapter_level: 1          # Pandoc --split-level
  css_path: null            # Custom CSS (default: built-in kindle.css)

ai_review:
  enabled: false            # Explicit false disables even with api_key (Skill path)
  api_url: ""               # OpenAI-compatible endpoint
  api_key: ""               # Fill to auto-enable AI review
  model: "gpt-4o-mini"
  max_tokens: 8192
  multimodal: false         # Visual review (requires vision model)
  max_images: 8
```

**Environment variables** — manage API keys via `.env` to avoid hardcoding:

```bash
cp .env.example .env
# Edit .env: PDF2BOOK_API_KEY=sk-your-key
```

Use `${VAR:-default}` syntax in `config.yaml`:
```yaml
ai_review:
  api_key: ${PDF2BOOK_API_KEY:-}
  api_url: ${PDF2BOOK_API_URL:-https://api.openai.com/v1/chat/completions}
```

> `.env` is gitignored; `.env.example` is safe to commit.

**Layout tuning guide**:

| Book type | toc_depth | chapter_level | Description |
|---|---|---|---|
| Story collection | 3 | 3 | Each H3 story on its own page |
| Long novel | 2 | 1 | Paginate by H1 chapter |
| Sectioned book | 2 | 2 | Paginate by H2 section |
| No chapter structure | 1 | 1 | Whole book as one page |

## Project Structure

```
PDF2BOOK/
├── inbox/                 # Drop PDFs here (zero-config entry)
├── library/               # Generated EPUBs
├── workspace/             # Intermediate artifacts (workspace/{stem}/)
├── config.yaml            # Config (auto-loaded)
├── AGENTS.md              # Universal AI agent entry point
├── skills/pdf2book/SKILL.md  # Portable AI Skill file
└── src/pdf2book/
    ├── cli.py              # Typer CLI entry
    ├── __main__.py         # python -m pdf2book support
    ├── pipeline.py         # Two-stage pipeline orchestration
    ├── batch.py            # Batch parallel conversion
    ├── config.py           # Pydantic config models
    ├── ocr/                # OCR backends (paddle_pp/rapid_ocr/cloud_ocr)
    ├── postprocess/        # Post-processing
    │   ├── processor.py        # Orchestration
    │   ├── header_footer.py    # Header/footer removal
    │   ├── merger.py           # Cross-page merging (CJK-aware)
    │   ├── structure.py        # Heading inference + page classification
    │   ├── page_classifier.py  # Rule-based page type ID
    │   ├── cip_extractor.py    # CIP metadata (GB/T 12451)
    │   ├── confidence_filter.py # Confidence filtering & marking
    │   ├── typography.py       # Chinese typography rules
    │   ├── decorations.py      # Decoration stripping (pHash)
    │   └── images.py           # Illustration cropping
    ├── review/             # AI review pipeline (auto-enables with api_key)
    │   ├── markdown_review.py  # Collector + Prompt + Applier
    │   ├── ai_client.py        # LLM calls + retry + JSON repair
    │   └── constraints.py      # Constraint validation
    ├── epub/               # EPUB construction
    │   ├── builder.py          # Pandoc + post-processing
    │   ├── metadata.py         # Metadata YAML + BookMetadata
    │   ├── toc_links.py        # TOC linkification
    │   └── templates/kindle.css # Kindle-optimized CSS
    ├── progress.py         # Progress reporting abstraction
    ├── pdf/                # PDF rendering & metadata
    └── utils/              # SQLite cache, logging, .env writer
```

## AI Skill (Cross-Platform)

The project includes a portable Skill file that lets any AI agent automate the complete conversion flow without an external LLM API key.

- **Location**: [`skills/pdf2book/SKILL.md`](skills/pdf2book/SKILL.md)
- **AGENTS.md**: [`AGENTS.md`](AGENTS.md) (universal entry point, auto-read by Claude Code, Cursor, Codex, etc.)
- **Flow**: 9-step decision chain (OCR → page analysis → metadata extraction → proofreading → layout inference → EPUB generation)

### One-Line Skill Install

Paste this into your AI agent's chat — it fetches the skill file and completes the full installation:

```bash
curl -fsSL https://raw.githubusercontent.com/charlesilcn/PDF2BOOK/main/skills/pdf2book/SKILL.md
```

That's it. The skill file teaches the agent how to install the project, check dependencies, and use all commands. The agent auto-runs environment check → clone repo → install dependencies, then you just say "convert XX.pdf to EPUB" to start.

## Contributing

Contributions welcome! Please follow this workflow:

1. **Fork** the repository and clone locally
2. **Create a branch**: `git checkout -b feature/your-feature-name`
3. **Install dev dependencies**: `pip install -e ".[ocr,dev]"`
4. **Write code** and ensure checks pass:
   ```bash
   ruff check src/          # Lint
   ruff format src/         # Format
   pytest -v -m "not slow"  # Fast tests (no OCR model needed)
   ```
5. **Commit changes**: Use conventional commits (e.g., `feat: add xxx` / `fix: resolve xxx`)
6. **Create a Pull Request**: Describe changes and motivation

### Development Guidelines

- **Code style**: [ruff](https://docs.astral.sh/ruff/) for linting and formatting, line width 100
- **Type annotations**: Required on all public functions
- **Testing**: New features require tests; `slow`-marked tests need real OCR models
- **Commit convention**: [Conventional Commits](https://www.conventionalcommits.org/)

### Architecture

Modular design with core layers:

- **OCR layer** (`ocr/`): Pluggable backends with unified `OCRBackend` abstract base
- **Post-processing layer** (`postprocess/`): Rule-based text processing — page classification, CIP extraction, confidence filtering
- **AI review layer** (`review/`): Optional LLM proofreading with constraint-validation retry loop
- **EPUB layer** (`epub/`): Pandoc-driven generation with Kindle-optimized CSS

## License

[MIT License](LICENSE) — Copyright (c) 2026 pdf2book
