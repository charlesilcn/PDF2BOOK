# AGENTS.md

## Project Overview

PDF2BOOK converts scanned PDF books to Kindle-friendly EPUB via OCR + AI-assisted typesetting. The pipeline: PDF → OCR → post-processing → Markdown → AI review → EPUB.

## Usage Priority

The project supports three usage modes with a clear recommended priority:

1. **CLI mode (recommended)** — Core command-line interface. Always available, no optional deps required for the engine. Install with `pip install -e ".[ocr]"`. Fill `api_key` in `config.yaml` to enable AI review.
2. **Skill mode (recommended when no API key)** — Agent-driven path via `skills/pdf2book/SKILL.md`. Uses the agent's own reasoning instead of an external LLM. Install by pasting the `curl` one-liner into any AI agent chat.
3. **WebUI mode (optional extension)** — Browser-based UI via `pdf2book web` subcommand. Requires the `[web]` extra (`fastapi` + `uvicorn`). Non-essential: core CLI works without it; if deps are missing, `pdf2book web` prints install instructions and exits cleanly.

> WebUI is a **non-essential** extension module. It builds on top of the CLI engine and must never affect `ocr`/`epub`/`convert`/`batch` commands. All WebUI imports in core code paths must be lazy (try/except ImportError) so a missing `fastapi` cannot break the CLI.

## Tech Stack

- Python 3.10+, Pydantic, Typer (CLI), PyMuPDF, PaddleOCR/RapidOCR, Pandoc
- AI review: OpenAI-compatible LLM API (optional, auto-enables with api_key)
- WebUI (optional): FastAPI + Uvicorn + static HTML/CSS/JS in `pdf2book-ui/`
- Config: YAML + environment variables (`${VAR:-default}` syntax)

## Setup Commands

```bash
git clone https://github.com/charlesilcn/PDF2BOOK.git
cd PDF2BOOK
pip install -e ".[ocr,dev]"          # Core CLI + OCR + dev tools (recommended)
pip install -e ".[rapid]"            # Lightweight OCR backend (optional)
pip install -e ".[web]"              # WebUI only (optional, non-essential)
pip install -e ".[ocr,web]"          # Core + WebUI
```

## Quick Start (Zero-Config)

```bash
cp your_book.pdf inbox/              # drop PDF
pdf2book                             # convert all → library/{stem}.epub
```

## Key Architecture

- **Three-folder layout**: `inbox/` (PDFs) → `library/` (EPUBs) → `workspace/{stem}/` (intermediate)
- **OCR pipeline**: PaddleOCR PP-StructureV3 (default) or RapidOCR (lightweight)
- **Post-processing**: page classification, CIP metadata extraction, cross-page merge, confidence filtering, decoration stripping, TOC linkification
- **AI review**: auto-enables when `config.yaml` has `api_key`; `--no-ai-review` forces off (Skill path)
- **Skill path**: agent does AI work itself via Read/Grep/Edit, no external API needed
- **WebUI (optional)**: `src/pdf2book/web/` (FastAPI backend) + `pdf2book-ui/` (static frontend). Loaded lazily via the `web` subcommand; never imported by core CLI code paths.

## Coding Conventions

- Python 3.10+ with `from __future__ import annotations`
- Type hints required on all public functions
- Ruff for linting/formatting (line length 100)
- Tests: pytest in `tests/` (not shipped to public repo)
- Config via Pydantic models in `src/pdf2book/config.py`
- OCR output saved to `workspace/{stem}/book.md` (relative paths for images)
- **WebUI isolation rule**: any `from pdf2book.web.*` import inside core code paths (`cli.py` core subcommands, `pipeline.py`, `batch.py`, etc.) MUST be wrapped in `try/except ImportError` so the core CLI works without the `[web]` extra installed.

## Security Rules

- **Never commit API keys.** Use `.env` file with `PDF2BOOK_API_KEY` variable; reference in config.yaml as `${PDF2BOOK_API_KEY:-}`
- `.env` is gitignored; `.env.example` is the template (safe to commit)
- `test_config.yaml` and `tests/` are gitignored (may contain test fixtures)
- Never log or print API keys, tokens, or credentials

## Skill Usage

The project includes a portable Skill at `skills/pdf2book/SKILL.md` that teaches any AI agent (Claude Code, Cursor, Codex, Trae) how to convert scanned PDF to EPUB without external API calls.

**One-line install**: paste this into your AI agent's chat:

```bash
curl -fsSL https://raw.githubusercontent.com/charlesilcn/PDF2BOOK/main/skills/pdf2book/SKILL.md
```

The agent will fetch the skill, read the 9-step workflow, and execute it autonomously.

## Things to Avoid

- Don't run `pdf2book` without `--no-ai-review` in the Skill path (it triggers external API calls)
- Don't hardcode API keys in config.yaml — use environment variables
- Don't commit `tests/`, `test_config.yaml`, `.env`, `workspace/`, `inbox/*.pdf`, `library/*.epub`
- Don't skip the `--resume` flag when re-running OCR (avoids reprocessing)
- Don't import `pdf2book.web.*` (or `fastapi`/`uvicorn`) at module top level in core code — always lazy-import inside the `web` subcommand to keep WebUI truly optional
