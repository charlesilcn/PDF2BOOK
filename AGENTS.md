# AGENTS.md

## Project Overview

PDF2BOOK converts scanned PDF books to Kindle-friendly EPUB via OCR + AI-assisted typesetting. The pipeline: PDF → OCR → post-processing → Markdown → AI review → EPUB.

## Tech Stack

- Python 3.10+, Pydantic, Typer (CLI), PyMuPDF, PaddleOCR/RapidOCR, Pandoc
- AI review: OpenAI-compatible LLM API (optional, auto-enables with api_key)
- Config: YAML + environment variables (`${VAR:-default}` syntax)

## Setup Commands

```bash
git clone https://github.com/charlesilcn/PDF2BOOK.git
cd PDF2BOOK
pip install -e ".[ocr,dev]"          # OCR + dev tools
pip install -e ".[rapid]"            # lightweight OCR (optional)
pip install -e ".[gui]"              # Gradio Web UI (optional, not yet stable)
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

## Coding Conventions

- Python 3.10+ with `from __future__ import annotations`
- Type hints required on all public functions
- Ruff for linting/formatting (line length 100)
- Tests: pytest in `tests/` (not shipped to public repo)
- Config via Pydantic models in `src/pdf2book/config.py`
- OCR output saved to `workspace/{stem}/book.md` (relative paths for images)

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
