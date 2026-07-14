# Web UI Preview & Per-Module Typesetting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a FastAPI-based web UI with split-view preview and per-module typesetting controls, reusing the existing `pdf2book-ui/` static HTML DEMO.

**Architecture:** FastAPI serves static HTML from `pdf2book-ui/` and provides REST API for book.md operations. A Python module parser converts book.md ↔ structured modules. Frontend uses Alpine.js + marked.js for reactive split-view preview. Per-module typesetting is implemented via Pandoc fenced divs with CSS utility classes — extending the existing `::: {.chapter}` / `::: {.dialogue}` pattern.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, Pydantic (backend); Alpine.js 3.x, marked.js, SortableJS (frontend, all via CDN, no build step)

## Global Constraints

- Python 3.10+ with `from __future__ import annotations`
- Ruff line-length 100, quote-style "double"
- All public functions have type hints
- New code lives in `src/pdf2book/web/` (separate from shelved `src/pdf2book/ui/` Gradio code)
- CLI core (`ocr`/`epub`/`convert`/`batch`) must remain unaffected — `web` is an optional extra
- `pdf2book-ui/` static HTML is served as-is; JS is added via `<script>` tags (no bundler)
- Per-module typesetting uses fenced divs (`::: {.class}`) — single source of truth in book.md
- Existing `::: {.chapter}`, `::: {.dialogue}`, `::: {.toc-list}` patterns are preserved
- Kindle CSS (`kindle.css`) remains KDP-safe (no font-size on body/p, no flexbox/grid)
- Tests: pytest in `tests/` (gitignored); new tests follow existing `test_*.py` pattern
- FastAPI/uvicorn are optional dependencies under `[web]` extra

---

## File Structure

```
src/pdf2book/web/
├── __init__.py              # Package init, exports create_app
├── server.py                # FastAPI app factory, static file mounting
├── module_parser.py         # book.md ↔ list[Module] parser/serializer
├── routes.py                # REST API endpoints (books, modules)
└── models.py                # Pydantic models for API request/response

pdf2book-ui/
├── pages/edit.html          # Modified: split-view layout (existing file)
├── assets/
│   ├── kindle-preview.css   # New: Kindle CSS for browser preview + utility classes
│   └── js/
│       └── edit.js          # New: Alpine.js app for edit page
└── (existing files unchanged)

src/pdf2book/epub/templates/
└── kindle.css               # Modified: add utility classes for per-module排版

src/pdf2book/cli.py          # Modified: add `web` subcommand

pyproject.toml               # Modified: add [web] extra

tests/
├── test_module_parser.py    # New: parser/serializer unit tests
└── test_web_routes.py       # New: API route unit tests
```

---

### Task 1: FastAPI Server Scaffold + CLI `web` Subcommand

**Files:**
- Create: `src/pdf2book/web/__init__.py`
- Create: `src/pdf2book/web/server.py`
- Create: `src/pdf2book/web/models.py`
- Modify: `src/pdf2book/cli.py` (add `web` subcommand after `gui` subcommand, ~line 363)
- Modify: `pyproject.toml` (add `[web]` extra after `[gui]`)
- Test: `tests/test_web_routes.py`

**Interfaces:**
- Produces: `create_app(cfg: AppConfig, log: Logger) -> FastAPI` — factory used by CLI `web` subcommand
- Produces: `web` subcommand in CLI with `--port`, `--host`, `--config` options

- [ ] **Step 1: Add `[web]` extra to pyproject.toml**

```toml
# In [project.optional-dependencies], after gui = [...]:
web = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
]
```

- [ ] **Step 2: Create `src/pdf2book/web/__init__.py`**

```python
"""FastAPI-based web UI for pdf2book (optional, requires '[web]' extra)."""

from __future__ import annotations

from pdf2book.web.server import create_app

__all__ = ["create_app"]
```

- [ ] **Step 3: Create `src/pdf2book/web/models.py`**

```python
"""Pydantic models for web API request/response."""

from __future__ import annotations

from pydantic import BaseModel


class BookInfo(BaseModel):
    """Book listing item."""
    stem: str
    has_book_md: bool
    has_meta_md: bool


class BookListResponse(BaseModel):
    """Response for GET /api/books."""
    books: list[BookInfo]


class BookContentResponse(BaseModel):
    """Response for GET /api/books/{stem}."""
    stem: str
    book_md: str
    meta_md: str


class SaveBookRequest(BaseModel):
    """Request for PUT /api/books/{stem}."""
    book_md: str
    meta_md: str | None = None
```

- [ ] **Step 4: Create `src/pdf2book/web/server.py`**

```python
"""FastAPI app factory — serves static HTML + REST API."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pdf2book.config import AppConfig

# pdf2book-ui/ lives at project root (two levels up from this file).
_UI_DIR = Path(__file__).resolve().parents[3] / "pdf2book-ui"


def create_app(cfg: AppConfig, log: logging.Logger) -> FastAPI:
    """Create and configure the FastAPI application.

    Serves static files from ``pdf2book-ui/`` and mounts REST API routes
    under ``/api``. The ``cfg`` is stored in app state for route handlers.
    """
    app = FastAPI(title="PDF2BOOK Web UI", version="0.1.0")
    app.state.cfg = cfg
    app.state.log = log

    # Mount static assets (CSS, JS, icons)
    if _UI_DIR.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=_UI_DIR / "assets"),
            name="assets",
        )

    # Register API routes (imported here to avoid circular deps)
    from pdf2book.web.routes import register_routes

    register_routes(app, _UI_DIR)

    log.info("Web UI serving from %s", _UI_DIR)
    return app
```

- [ ] **Step 5: Create minimal `src/pdf2book/web/routes.py` (will be expanded in Task 3)**

```python
"""REST API routes for the web UI."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from pdf2book.config import AppConfig
from pdf2book.web.models import BookContentResponse, BookInfo, BookListResponse, SaveBookRequest


def _cfg(app: FastAPI) -> AppConfig:
    return app.state.cfg


def _work_dir(app: FastAPI, stem: str) -> Path:
    wd = _cfg(app).work_dir / stem
    if not wd.exists():
        raise HTTPException(status_code=404, detail=f"Book '{stem}' not found")
    return wd


def register_routes(app: FastAPI, ui_dir: Path) -> None:
    """Register all API and page routes on the FastAPI app."""

    # --- Page routes (serve HTML files) ---
    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(ui_dir / "pages" / "welcome.html")

    @app.get("/pages/{page_name}")
    async def serve_page(page_name: str) -> FileResponse:
        path = ui_dir / "pages" / f"{page_name}.html"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Page not found")
        return FileResponse(path)

    # --- API: Book listing ---
    @app.get("/api/books")
    async def list_books() -> BookListResponse:
        ws = _cfg(app).work_dir
        books: list[BookInfo] = []
        if ws.exists():
            for child in sorted(ws.iterdir()):
                if child.is_dir():
                    books.append(BookInfo(
                        stem=child.name,
                        has_book_md=(child / "book.md").exists(),
                        has_meta_md=(child / "meta.md").exists(),
                    ))
        return BookListResponse(books=books)

    # --- API: Load book content ---
    @app.get("/api/books/{stem}")
    async def get_book(stem: str) -> BookContentResponse:
        wd = _work_dir(app, stem)
        book_md = ""
        meta_md = ""
        book_path = wd / "book.md"
        meta_path = wd / "meta.md"
        if book_path.exists():
            book_md = book_path.read_text(encoding="utf-8")
        if meta_path.exists():
            meta_md = meta_path.read_text(encoding="utf-8")
        return BookContentResponse(stem=stem, book_md=book_md, meta_md=meta_md)

    # --- API: Save book content ---
    @app.put("/api/books/{stem}")
    async def save_book(stem: str, req: SaveBookRequest) -> dict:
        wd = _work_dir(app, stem)
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "book.md").write_text(req.book_md, encoding="utf-8")
        if req.meta_md is not None:
            (wd / "meta.md").write_text(req.meta_md, encoding="utf-8")
        return {"status": "ok", "stem": stem}
```

- [ ] **Step 6: Add `web` subcommand to `src/pdf2book/cli.py`**

Insert after the `gui` command (after line 363, before `if __name__`):

```python
@app.command()
def web(
    config: Path | None = typer.Option(None, "--config", help="Config YAML path"),
    port: int = typer.Option(8000, "--port", help="Port for the Web UI server"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind the server to"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG logging"),
) -> None:
    """Launch the FastAPI Web UI (optional extension; requires the 'web' extra).

    Provides a browser-based interface with split-view preview and
    per-module typesetting controls. Install with:

        pip install 'pdf2book[web]'

    When FastAPI is not installed, prints install instructions and exits.
    """
    cfg = _load_config_or_default(config)
    log = setup_logger("DEBUG" if verbose else "INFO")
    ensure_standard_dirs(cfg)

    try:
        import uvicorn

        from pdf2book.web.server import create_app
    except ImportError as exc:
        typer.echo(
            "Web UI 需要安装可选依赖（fastapi + uvicorn）。请运行:\n"
            "  pip install 'pdf2book[web]'\n"
            f"原始错误: {exc}",
            err=True,
        )
        raise typer.Exit(code=1)

    app = create_app(cfg, log)
    typer.echo(f"Web UI 启动中 → http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
```

- [ ] **Step 7: Write failing test for server creation**

Create `tests/test_web_routes.py`:

```python
"""Tests for web API routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def web_app(tmp_path):
    """Create a FastAPI app with a temp workspace."""
    from pdf2book.config import AppConfig
    from pdf2book.web.server import create_app

    cfg = AppConfig()
    cfg.work_dir = tmp_path / "workspace"
    cfg.work_dir.mkdir()
    import logging
    app = create_app(cfg, logging.getLogger("test"))
    return app


@pytest.fixture
def client(web_app):
    return TestClient(web_app)


class TestBookRoutes:
    def test_list_books_empty(self, client):
        resp = client.get("/api/books")
        assert resp.status_code == 200
        data = resp.json()
        assert data["books"] == []

    def test_list_books_with_content(self, client, tmp_path):
        # Create a fake book
        book_dir = tmp_path / "workspace" / "test_book"
        book_dir.mkdir(parents=True)
        (book_dir / "book.md").write_text("# Test", encoding="utf-8")

        resp = client.get("/api/books")
        assert resp.status_code == 200
        books = resp.json()["books"]
        assert len(books) == 1
        assert books[0]["stem"] == "test_book"
        assert books[0]["has_book_md"] is True

    def test_get_book_content(self, client, tmp_path):
        book_dir = tmp_path / "workspace" / "test_book"
        book_dir.mkdir(parents=True)
        (book_dir / "book.md").write_text("# Chapter 1\n\nHello.", encoding="utf-8")

        resp = client.get("/api/books/test_book")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stem"] == "test_book"
        assert "# Chapter 1" in data["book_md"]

    def test_get_book_not_found(self, client):
        resp = client.get("/api/books/nonexistent")
        assert resp.status_code == 404

    def test_save_book(self, client, tmp_path):
        book_dir = tmp_path / "workspace" / "test_book"
        book_dir.mkdir(parents=True)

        resp = client.put("/api/books/test_book", json={
            "book_md": "# Saved Content",
            "meta_md": "title: Test",
        })
        assert resp.status_code == 200
        assert (book_dir / "book.md").read_text(encoding="utf-8") == "# Saved Content"
        assert (book_dir / "meta.md").read_text(encoding="utf-8") == "title: Test"
```

- [ ] **Step 8: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_routes.py -v`
Expected: FAIL (fastapi not installed or import errors)

- [ ] **Step 9: Install web dependencies and run tests**

Run: `pip install -e ".[web]" && python -m pytest tests/test_web_routes.py -v`
Expected: PASS

- [ ] **Step 10: Verify CLI subcommand**

Run: `python -m pdf2book.cli web --help`
Expected: Shows `web` command with `--port`, `--host`, `--config` options

- [ ] **Step 11: Commit**

```bash
git add src/pdf2book/web/ tests/test_web_routes.py pyproject.toml src/pdf2book/cli.py
git commit -m "feat: add FastAPI web server scaffold with CLI web subcommand"
```

---

### Task 2: Module Parser — Parse book.md into Modules

**Files:**
- Create: `src/pdf2book/web/module_parser.py`
- Test: `tests/test_module_parser.py`

**Interfaces:**
- Produces: `Module` dataclass with fields: `id`, `type`, `content`, `layout_classes`, `word_count`, `heading_level`, `heading_id`
- Produces: `ModuleType` enum: `chapter`, `paragraph`, `image`, `divider`, `quote`, `dialogue`, `toc`, `cover`, `other`
- Produces: `parse_modules(md_text: str) -> list[Module]`
- Consumes: book.md format with `::: {.chapter}`, `::: {.dialogue}`, `::: {.toc-list}`, `# Heading {#anchor}`, `![](path)`, `---`, `>[low-confidence]`

- [ ] **Step 1: Write failing tests for parser**

Create `tests/test_module_parser.py`:

```python
"""Tests for book.md module parser."""

from __future__ import annotations

from pdf2book.web.module_parser import ModuleType, parse_modules, serialize_modules


class TestParseModules:
    def test_single_paragraph(self):
        md = "这是一段文字。"
        modules = parse_modules(md)
        assert len(modules) == 1
        assert modules[0].type == ModuleType.PARAGRAPH
        assert modules[0].content == "这是一段文字。"
        assert modules[0].layout_classes == []

    def test_chapter_heading(self):
        md = "# 第一章 科学边界 {#ch-1}\n\n正文内容。"
        modules = parse_modules(md)
        assert len(modules) == 2
        assert modules[0].type == ModuleType.CHAPTER
        assert modules[0].heading_level == 1
        assert modules[0].heading_id == "ch-1"
        assert modules[0].content == "# 第一章 科学边界 {#ch-1}"
        assert modules[1].type == ModuleType.PARAGRAPH

    def test_chapter_fenced_div_stripped(self):
        """::: {.chapter} wrapper is stripped; H1 inside becomes chapter module."""
        md = "::: {.chapter}\n# 第一章 科学边界 {#ch-1}\n\n正文内容。\n:::"
        modules = parse_modules(md)
        assert len(modules) == 2
        assert modules[0].type == ModuleType.CHAPTER
        assert modules[0].heading_level == 1
        assert modules[1].type == ModuleType.PARAGRAPH
        # layout_classes should NOT contain "chapter" (it's structural)
        assert "chapter" not in modules[0].layout_classes

    def test_dialogue_fenced_div(self):
        """::: {.dialogue} becomes paragraph with .dialogue layout class."""
        md = '::: {.dialogue}\n"对话内容"\n:::'
        modules = parse_modules(md)
        assert len(modules) == 1
        assert modules[0].type == ModuleType.DIALOGUE
        assert "dialogue" in modules[0].layout_classes
        assert modules[0].content == '"对话内容"'

    def test_image(self):
        md = "![](pages/page_0000.png)"
        modules = parse_modules(md)
        assert len(modules) == 1
        assert modules[0].type == ModuleType.IMAGE

    def test_cover_image(self):
        """Full-page images (pages/) are classified as cover."""
        md = "![](pages/page_0000.png)"
        modules = parse_modules(md)
        assert modules[0].type == ModuleType.COVER

    def test_inline_image(self):
        """Cropped images (images/) are classified as image, not cover."""
        md = "![](images/p1_e0.png)"
        modules = parse_modules(md)
        assert modules[0].type == ModuleType.IMAGE

    def test_divider(self):
        md = "段落一\n\n---\n\n段落二"
        modules = parse_modules(md)
        assert len(modules) == 3
        assert modules[0].type == ModuleType.PARAGRAPH
        assert modules[1].type == ModuleType.DIVIDER
        assert modules[2].type == ModuleType.PARAGRAPH

    def test_quote(self):
        md = "> 引文内容"
        modules = parse_modules(md)
        assert len(modules) == 1
        assert modules[0].type == ModuleType.QUOTE

    def test_toc_list(self):
        md = "::: {.toc-list}\n- [第一章](#ch-1)\n- [第二章](#ch-2)\n:::"
        modules = parse_modules(md)
        assert len(modules) == 1
        assert modules[0].type == ModuleType.TOC

    def test_layout_classes(self):
        """Per-module layout classes are extracted from fenced divs."""
        md = "::: {.no-indent .center}\n段落内容\n:::"
        modules = parse_modules(md)
        assert len(modules) == 1
        assert "no-indent" in modules[0].layout_classes
        assert "center" in modules[0].layout_classes

    def test_multiple_paragraphs(self):
        md = "第一段。\n\n第二段。\n\n第三段。"
        modules = parse_modules(md)
        assert len(modules) == 3
        assert all(m.type == ModuleType.PARAGRAPH for m in modules)

    def test_word_count(self):
        md = "这是一段测试文字。"
        modules = parse_modules(md)
        assert modules[0].word_count == 9

    def test_module_ids_sequential(self):
        md = "段一\n\n段二\n\n段三"
        modules = parse_modules(md)
        assert modules[0].id == "m1"
        assert modules[1].id == "m2"
        assert modules[2].id == "m3"

    def test_low_confidence_marker(self):
        """>[low-confidence] markers are preserved in content."""
        md = ">[low-confidence] 疑似文字"
        modules = parse_modules(md)
        assert len(modules) == 1
        assert "[low-confidence]" in modules[0].content

    def test_h2_heading(self):
        md = "## 第一节 背景\n\n正文。"
        modules = parse_modules(md)
        assert modules[0].heading_level == 2
        assert modules[0].type == ModuleType.CHAPTER
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_module_parser.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement the module parser**

Create `src/pdf2book/web/module_parser.py`:

```python
"""Parse book.md into structured modules and serialize back.

Handles the Pandoc-flavored Markdown used by pdf2book:
  - ``::: {.chapter}`` — structural wrapper, stripped on parse,
    reconstructed on serialize around H1 sections.
  - ``::: {.dialogue}`` — paragraph-level wrapper, preserved as
    ``layout_classes=["dialogue"]`` on the module.
  - ``::: {.toc-list}`` — TOC block, classified as TOC module.
  - ``::: {.no-indent .center}`` — per-module layout classes.
  - ``# Heading {#anchor}`` — chapter/section heading.
  - ``![](path)`` — image (cover if path starts with ``pages/``).
  - ``---`` — divider.
  - ``> text`` — blockquote / low-confidence marker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ModuleType(str, Enum):
    """Display-level module types for the editor."""
    CHAPTER = "chapter"
    PARAGRAPH = "paragraph"
    IMAGE = "image"
    COVER = "cover"
    DIVIDER = "divider"
    QUOTE = "quote"
    DIALOGUE = "dialogue"
    TOC = "toc"
    OTHER = "other"


@dataclass
class Module:
    """A single editable block in the module editor."""
    id: str
    type: ModuleType
    content: str
    layout_classes: list[str] = field(default_factory=list)
    word_count: int = 0
    heading_level: int | None = None
    heading_id: str | None = None


# Regex patterns
_FENCED_DIV_START = re.compile(r"^:::\s*\{\.([^}]+)\}")
_FENCED_DIV_END = re.compile(r"^:::\s*$")
_HEADING = re.compile(r"^(#{1,4})\s+(.+?)(?:\s*\{#([^}]+)\})?\s*$")
_IMAGE = re.compile(r"^!\[.*?\]\((.+?)\)")
_HR = re.compile(r"^---+\s*$")
_LOW_CONF = re.compile(r"^>\[low-confidence\]")

# Structural classes that are stripped (not per-module layout)
_STRUCTURAL_CLASSES = {"chapter"}


def _parse_fenced_classes(line: str) -> list[str] | None:
    """Extract classes from a ``::: {.class1 .class2}`` line. Returns None if not a fenced div start."""
    m = _FENCED_DIV_START.match(line)
    if not m:
        return None
    raw = m.group(1)
    # Classes are space-separated, each prefixed with a dot
    classes = [c.strip().lstrip(".") for c in raw.split() if c.strip().startswith(".")]
    return classes


def _count_chars(text: str) -> int:
    """Count meaningful characters (non-whitespace, non-markup)."""
    # Strip markdown syntax characters
    clean = re.sub(r"[#>*\-\[\]!(){}=|`~]", "", text)
    clean = re.sub(r"\s+", "", clean)
    return len(clean)


def _classify_image(path_str: str) -> ModuleType:
    """Classify image as COVER (full-page) or IMAGE (inline/cropped)."""
    if "pages/" in path_str or path_str.startswith("pages"):
        return ModuleType.COVER
    return ModuleType.IMAGE


def parse_modules(md_text: str) -> list[Module]:
    """Parse book.md text into a list of Module objects.

    See module docstring for the supported Markdown format.
    """
    lines = md_text.split("\n")
    modules: list[Module] = []
    module_counter = 0

    # Fenced div state
    div_stack: list[list[str]] = []  # Stack of class lists for nested divs
    # Track if current div is structural (chapter/toc) — its content is
    # parsed as inner modules, not wrapped.
    div_is_structural: list[bool] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for fenced div start
        div_classes = _parse_fenced_classes(line)
        if div_classes is not None:
            is_structural = any(c in _STRUCTURAL_CLASSES for c in div_classes)
            div_stack.append(div_classes)
            div_is_structural.append(is_structural)
            i += 1
            continue

        # Check for fenced div end
        if _FENCED_DIV_END.match(line):
            if div_stack:
                div_stack.pop()
                div_is_structural.pop()
            i += 1
            continue

        # Skip empty lines
        if not line.strip():
            i += 1
            continue

        # Determine layout classes from non-structural divs on the stack
        layout_classes = []
        for idx, classes in enumerate(div_stack):
            if not div_is_structural[idx]:
                layout_classes.extend(classes)

        # Classify the block starting at this line
        # Collect the full block (until empty line, fenced div, or end)
        block_lines = [line]
        j = i + 1
        while j < len(lines):
            next_line = lines[j]
            # Stop at empty line, fenced div start/end, or new block type
            if (not next_line.strip()
                    or _FENCED_DIV_START.match(next_line)
                    or _FENCED_DIV_END.match(next_line)):
                break
            block_lines.append(next_line)
            j += 1

        block_text = "\n".join(block_lines)
        module_counter += 1
        mid = f"m{module_counter}"

        # Determine module type
        # Check heading
        heading_match = _HEADING.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2)
            hid = heading_match.group(3)
            modules.append(Module(
                id=mid,
                type=ModuleType.CHAPTER,
                content=block_text,
                layout_classes=layout_classes,
                word_count=_count_chars(title),
                heading_level=level,
                heading_id=hid,
            ))
        # Check image
        elif _IMAGE.match(line):
            img_match = _IMAGE.match(line)
            path_str = img_match.group(1)
            mtype = _classify_image(path_str)
            modules.append(Module(
                id=mid,
                type=mtype,
                content=block_text,
                layout_classes=layout_classes,
                word_count=0,
            ))
        # Check HR (divider)
        elif _HR.match(line):
            modules.append(Module(
                id=mid,
                type=ModuleType.DIVIDER,
                content=block_text,
                layout_classes=layout_classes,
                word_count=0,
            ))
        # Check low-confidence or blockquote
        elif line.startswith(">"):
            mtype = ModuleType.QUOTE
            # Low-confidence blocks are stored as quotes with preserved marker
            modules.append(Module(
                id=mid,
                type=mtype,
                content=block_text,
                layout_classes=layout_classes,
                word_count=_count_chars(block_text),
            ))
        # Check TOC
        elif "toc-list" in layout_classes:
            modules.append(Module(
                id=mid,
                type=ModuleType.TOC,
                content=block_text,
                layout_classes=layout_classes,
                word_count=0,
            ))
        # Check dialogue
        elif "dialogue" in layout_classes:
            modules.append(Module(
                id=mid,
                type=ModuleType.DIALOGUE,
                content=block_text,
                layout_classes=layout_classes,
                word_count=_count_chars(block_text),
            ))
        else:
            # Default: paragraph
            modules.append(Module(
                id=mid,
                type=ModuleType.PARAGRAPH,
                content=block_text,
                layout_classes=layout_classes,
                word_count=_count_chars(block_text),
            ))

        i = j

    return modules
```

- [ ] **Step 4: Run parse tests**

Run: `python -m pytest tests/test_module_parser.py::TestParseModules -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pdf2book/web/module_parser.py tests/test_module_parser.py
git commit -m "feat: add module parser for book.md ↔ structured modules"
```

---

### Task 3: Module Serializer — Serialize Modules back to book.md

**Files:**
- Modify: `src/pdf2book/web/module_parser.py` (add `serialize_modules` function)
- Test: `tests/test_module_parser.py` (add `TestSerializeModules` class)

**Interfaces:**
- Produces: `serialize_modules(modules: list[Module]) -> str`
- Round-trip property: `serialize_modules(parse_modules(md)) ≈ md` (whitespace may differ slightly)

- [ ] **Step 1: Write failing tests for serializer**

Add to `tests/test_module_parser.py`:

```python
class TestSerializeModules:
    def test_serialize_simple_paragraph(self):
        from pdf2book.web.module_parser import Module, ModuleType
        modules = [Module(id="m1", type=ModuleType.PARAGRAPH, content="文字内容", word_count=4)]
        md = serialize_modules(modules)
        assert md.strip() == "文字内容"

    def test_serialize_chapter_heading(self):
        from pdf2book.web.module_parser import Module, ModuleType
        modules = [
            Module(id="m1", type=ModuleType.CHAPTER, content="# 第一章 {#ch-1}",
                   heading_level=1, heading_id="ch-1", word_count=3),
            Module(id="m2", type=ModuleType.PARAGRAPH, content="正文。", word_count=2),
        ]
        md = serialize_modules(modules)
        # Chapter wrapper should be reconstructed
        assert "::: {.chapter}" in md
        assert "# 第一章 {#ch-1}" in md
        assert "正文。" in md
        assert md.strip().endswith(":::")

    def test_serialize_layout_classes(self):
        from pdf2book.web.module_parser import Module, ModuleType
        modules = [
            Module(id="m1", type=ModuleType.PARAGRAPH, content="无缩进段落",
                   layout_classes=["no-indent", "center"], word_count=5),
        ]
        md = serialize_modules(modules)
        assert "::: {.no-indent .center}" in md
        assert "无缩进段落" in md

    def test_serialize_dialogue(self):
        from pdf2book.web.module_parser import Module, ModuleType
        modules = [
            Module(id="m1", type=ModuleType.DIALOGUE, content='"对话"',
                   layout_classes=["dialogue"], word_count=2),
        ]
        md = serialize_modules(modules)
        assert "::: {.dialogue}" in md

    def test_round_trip_paragraph(self):
        md = "第一段。\n\n第二段。"
        modules = parse_modules(md)
        result = serialize_modules(modules)
        assert "第一段。" in result
        assert "第二段。" in result

    def test_round_trip_chapter(self):
        md = "# 第一章 测试 {#ch-1}\n\n正文内容。"
        modules = parse_modules(md)
        result = serialize_modules(modules)
        assert "# 第一章 测试 {#ch-1}" in result
        assert "正文内容。" in result
        assert "::: {.chapter}" in result

    def test_round_trip_dialogue(self):
        md = '::: {.dialogue}\n"对话"\n:::'
        modules = parse_modules(md)
        result = serialize_modules(modules)
        assert "::: {.dialogue}" in result
        assert '"对话"' in result

    def test_round_trip_layout_classes(self):
        md = "::: {.no-indent}\n无缩进\n:::"
        modules = parse_modules(md)
        result = serialize_modules(modules)
        assert "::: {.no-indent}" in result
        assert "无缩进" in result

    def test_round_trip_divider(self):
        md = "段一\n\n---\n\n段二"
        modules = parse_modules(md)
        result = serialize_modules(modules)
        assert "---" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_module_parser.py::TestSerializeModules -v`
Expected: FAIL (serialize_modules not defined)

- [ ] **Step 3: Implement `serialize_modules`**

Add to `src/pdf2book/web/module_parser.py`:

```python
def serialize_modules(modules: list[Module]) -> str:
    """Serialize a list of Module objects back to book.md text.

    Reconstructs:
    - ``::: {.chapter}`` wrappers around H1 sections
    - ``::: {.class1 .class2}`` wrappers for per-module layout classes
    - ``::: {.dialogue}`` for dialogue modules
    - ``::: {.toc-list}`` for TOC modules

    Structural classes (``chapter``) are NEVER emitted as per-module
    layout — they wrap H1 sections at the top level.
    """
    if not modules:
        return ""

    blocks: list[str] = []
    in_chapter = False

    for mod in modules:
        content = mod.content.strip()

        # H1 heading → start a new chapter wrapper
        if mod.heading_level == 1:
            # Close previous chapter wrapper
            if in_chapter:
                blocks.append(":::")
            blocks.append("::: {.chapter}")
            blocks.append(content)
            in_chapter = True
            continue

        # Non-H1 module inside a chapter wrapper
        # Determine wrapper classes
        wrapper_classes: list[str] = []

        # Preserve dialogue class
        if mod.type == ModuleType.DIALOGUE and "dialogue" not in mod.layout_classes:
            wrapper_classes.append("dialogue")
        # Preserve toc-list class
        if mod.type == ModuleType.TOC and "toc-list" not in mod.layout_classes:
            wrapper_classes.append("toc-list")

        # Add user layout classes (exclude structural ones)
        for cls in mod.layout_classes:
            if cls not in _STRUCTURAL_CLASSES and cls not in wrapper_classes:
                wrapper_classes.append(cls)

        if wrapper_classes:
            class_str = " ".join(f".{c}" for c in wrapper_classes)
            blocks.append(f"::: {{{class_str}}}")
            blocks.append(content)
            blocks.append(":::")
        else:
            blocks.append(content)

    # Close final chapter wrapper
    if in_chapter:
        blocks.append(":::")

    # Join with double newlines, clean up excessive blank lines
    result = "\n\n".join(blocks)
    # Collapse 3+ newlines to 2
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip() + "\n"
```

- [ ] **Step 4: Run all parser tests**

Run: `python -m pytest tests/test_module_parser.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/pdf2book/web/module_parser.py tests/test_module_parser.py
git commit -m "feat: add module serializer with chapter wrapper reconstruction"
```

---

### Task 4: Module API Routes

**Files:**
- Modify: `src/pdf2book/web/routes.py` (add module endpoints)
- Modify: `src/pdf2book/web/models.py` (add module models)
- Test: `tests/test_web_routes.py` (add module route tests)

**Interfaces:**
- Produces: `GET /api/books/{stem}/modules` → `ModuleListResponse`
- Produces: `PUT /api/books/{stem}/modules` → saves modules (serializes to book.md)

- [ ] **Step 1: Add module models to `src/pdf2book/web/models.py`**

Append to existing file:

```python
class ModuleData(BaseModel):
    """A single module in the editor."""
    id: str
    type: str
    content: str
    layout_classes: list[str] = []
    word_count: int = 0
    heading_level: int | None = None
    heading_id: str | None = None


class ModuleListResponse(BaseModel):
    """Response for GET /api/books/{stem}/modules."""
    stem: str
    modules: list[ModuleData]


class SaveModulesRequest(BaseModel):
    """Request for PUT /api/books/{stem}/modules."""
    modules: list[ModuleData]
```

- [ ] **Step 2: Add module routes to `src/pdf2book/web/routes.py`**

Add inside `register_routes()`, after the existing book routes:

```python
    # --- API: Module list (parsed from book.md) ---
    @app.get("/api/books/{stem}/modules")
    async def get_modules(stem: str) -> ModuleListResponse:
        from pdf2book.web.module_parser import parse_modules
        from pdf2book.web.models import ModuleData, ModuleListResponse

        wd = _work_dir(app, stem)
        book_path = wd / "book.md"
        if not book_path.exists():
            raise HTTPException(status_code=404, detail="book.md not found")
        md_text = book_path.read_text(encoding="utf-8")
        modules = parse_modules(md_text)
        return ModuleListResponse(
            stem=stem,
            modules=[
                ModuleData(
                    id=m.id,
                    type=m.type.value,
                    content=m.content,
                    layout_classes=m.layout_classes,
                    word_count=m.word_count,
                    heading_level=m.heading_level,
                    heading_id=m.heading_id,
                )
                for m in modules
            ],
        )

    # --- API: Save modules (serialize to book.md) ---
    @app.put("/api/books/{stem}/modules")
    async def save_modules(stem: str, req: SaveModulesRequest) -> dict:
        from pdf2book.web.module_parser import Module, ModuleType, serialize_modules

        wd = _work_dir(app, stem)
        wd.mkdir(parents=True, exist_ok=True)

        modules = [
            Module(
                id=m.id,
                type=ModuleType(m.type),
                content=m.content,
                layout_classes=m.layout_classes,
                word_count=m.word_count,
                heading_level=m.heading_level,
                heading_id=m.heading_id,
            )
            for m in req.modules
        ]
        md_text = serialize_modules(modules)
        (wd / "book.md").write_text(md_text, encoding="utf-8")
        return {"status": "ok", "stem": stem, "module_count": len(modules)}
```

Also add `SaveModulesRequest` to the imports at the top of routes.py.

- [ ] **Step 3: Write failing tests for module routes**

Add to `tests/test_web_routes.py`:

```python
class TestModuleRoutes:
    def test_get_modules(self, client, tmp_path):
        book_dir = tmp_path / "workspace" / "test_book"
        book_dir.mkdir(parents=True)
        (book_dir / "book.md").write_text(
            "# 第一章 测试 {#ch-1}\n\n正文段落。",
            encoding="utf-8",
        )

        resp = client.get("/api/books/test_book/modules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stem"] == "test_book"
        modules = data["modules"]
        assert len(modules) == 2
        assert modules[0]["type"] == "chapter"
        assert modules[0]["heading_level"] == 1
        assert modules[0]["heading_id"] == "ch-1"
        assert modules[1]["type"] == "paragraph"

    def test_save_modules(self, client, tmp_path):
        book_dir = tmp_path / "workspace" / "test_book"
        book_dir.mkdir(parents=True)

        resp = client.put("/api/books/test_book/modules", json={
            "modules": [
                {
                    "id": "m1",
                    "type": "paragraph",
                    "content": "保存的段落",
                    "layout_classes": ["no-indent"],
                    "word_count": 5,
                    "heading_level": None,
                    "heading_id": None,
                }
            ]
        })
        assert resp.status_code == 200
        saved = (book_dir / "book.md").read_text(encoding="utf-8")
        assert "no-indent" in saved
        assert "保存的段落" in saved

    def test_get_modules_not_found(self, client):
        resp = client.get("/api/books/nonexistent/modules")
        assert resp.status_code == 404
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_web_routes.py::TestModuleRoutes -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pdf2book/web/routes.py src/pdf2book/web/models.py tests/test_web_routes.py
git commit -m "feat: add module API routes (list + save modules)"
```

---

### Task 5: CSS Utility Classes for Per-Module Typesetting

**Files:**
- Modify: `src/pdf2book/epub/templates/kindle.css` (add utility classes at end)
- Create: `pdf2book-ui/assets/kindle-preview.css` (browser preview CSS)

**Interfaces:**
- Produces: CSS utility classes: `.no-indent`, `.align-left`, `.align-center`, `.align-justify`, `.spacing-tight`, `.spacing-normal`, `.spacing-loose`
- Produces: `kindle-preview.css` — wraps kindle.css with browser-friendly dark mode support

- [ ] **Step 1: Add utility classes to kindle.css**

Append to `src/pdf2book/epub/templates/kindle.css`:

```css
/* --- Per-module typesetting utility classes (Web UI) ---
   Applied via Pandoc fenced divs: ::: {.no-indent} ... :::
   These override the global defaults for specific modules. */

/* Disable first-line indent (e.g., chapter opening, dialogue). */
.no-indent p {
  text-indent: 0;
}

/* Alignment overrides. */
.align-left p {
  text-align: left;
}
.align-center p {
  text-align: center;
}
.align-justify p {
  text-align: justify;
}

/* Paragraph spacing overrides. */
.spacing-tight p + p {
  margin-top: 0.2em;
}
.spacing-normal p + p {
  margin-top: 0.5em;
}
.spacing-loose p + p {
  margin-top: 1.2em;
}
```

- [ ] **Step 2: Create `pdf2book-ui/assets/kindle-preview.css`**

This CSS mirrors kindle.css for browser preview, with dark-mode wrapper support:

```css
/* Kindle preview CSS for browser rendering.
 * Mirrors epub/templates/kindle.css + utility classes.
 * Applied inside #preview-pane in edit.html.
 */

.kindle-preview {
  line-height: 1.75;
  margin: 0;
  padding: 2em 3em;
  font-family: "Songti SC", "STSong", "SimSun", "Noto Serif CJK SC", serif;
  color: #e0e0e0;
  background: #1a1a1a;
  max-width: 720px;
  margin: 0 auto;
}

.kindle-preview p {
  text-indent: 2em;
  margin: 0;
  text-align: justify;
}

.kindle-preview h1,
.kindle-preview h2,
.kindle-preview h3,
.kindle-preview h4 {
  text-align: center;
  font-weight: bold;
  text-indent: 0;
}

.kindle-preview h1 {
  margin-top: 3em;
  margin-bottom: 2.5em;
  font-size: 1.5em;
}

.kindle-preview h2 {
  margin-top: 2em;
  margin-bottom: 1.2em;
  border-left: 4px solid #666;
  padding-left: 0.8em;
  text-align: left;
  font-size: 1.25em;
}

.kindle-preview h3 {
  margin-top: 2em;
  margin-bottom: 1.5em;
  font-size: 1.15em;
}

.kindle-preview h1 + p,
.kindle-preview h2 + p,
.kindle-preview h3 + p,
.kindle-preview h4 + p {
  text-indent: 0;
}

.kindle-preview p + p {
  margin-top: 0.3em;
}

.kindle-preview img {
  max-width: 100%;
  max-height: 80vh;
  height: auto;
  display: block;
  margin: 0.5em auto;
}

.kindle-preview img[src*="pages/"] {
  max-height: 80vh;
  height: 80vh;
  width: auto;
  object-fit: contain;
}

.kindle-preview hr {
  border: none;
  border-top: 1px solid #555;
  margin: 2em auto;
  width: 40%;
}

.kindle-preview blockquote {
  margin: 1em 0;
  padding: 0.5em 1em;
  border-left: 3px solid #888;
  background-color: #2a2a2a;
  color: #bbb;
}

.kindle-preview blockquote p {
  text-indent: 0;
}

/* Chapter decorative borders. */
.kindle-preview .chapter {
  border-top: 2px solid #555;
  border-bottom: 1px solid #444;
  padding: 1.5em 0;
  margin-top: 2em;
  margin-bottom: 2em;
  text-align: center;
}

.kindle-preview .chapter h1 {
  margin-top: 0;
}

/* Dialogue styling. */
.kindle-preview .dialogue p {
  margin-left: 1.5em;
  border-left: 2px solid #666;
  padding-left: 0.8em;
  text-indent: 0;
}

/* TOC list. */
.kindle-preview .toc-list ul {
  list-style: none;
  margin: 0.5em 0;
  padding-left: 0;
}

.kindle-preview .toc-list li {
  margin: 0.3em 0;
  text-indent: 0;
  text-align: left;
}

.kindle-preview .toc-list a {
  text-decoration: none;
  color: #7ab;
}

/* --- Utility classes (per-module typesetting) --- */

.kindle-preview .no-indent p {
  text-indent: 0;
}

.kindle-preview .align-left p {
  text-align: left;
}

.kindle-preview .align-center p {
  text-align: center;
}

.kindle-preview .align-justify p {
  text-align: justify;
}

.kindle-preview .spacing-tight p + p {
  margin-top: 0.2em;
}

.kindle-preview .spacing-normal p + p {
  margin-top: 0.5em;
}

.kindle-preview .spacing-loose p + p {
  margin-top: 1.2em;
}
```

- [ ] **Step 3: Commit**

```bash
git add src/pdf2book/epub/templates/kindle.css pdf2book-ui/assets/kindle-preview.css
git commit -m "feat: add per-module typesetting CSS utility classes + preview stylesheet"
```

---

### Task 6: Frontend — Split View Layout + Preview Rendering

**Files:**
- Modify: `pdf2book-ui/pages/edit.html` (change center panel to split layout, add script tags)
- Create: `pdf2book-ui/assets/js/edit.js` (Alpine.js app for edit page)

**Interfaces:**
- Produces: Split-view edit page with module canvas (left) + live preview (right)
- Produces: `edit.js` with Alpine.js component handling: book loading, module rendering, preview, layout class toggling, save

- [ ] **Step 1: Modify edit.html — change center panel to split view**

**IMPORTANT Alpine.js scoping:** The `x-data="editPage()"` must be placed on `.edit-shell` (the parent of sidebar, edit-center, and inspector-panel) so that Alpine.js reactivity covers ALL three panels. Do NOT place it on `.edit-center` alone — the inspector panel (Task 7) needs access to `selectedModule`, `toggleLayoutClass`, etc.

In `pdf2book-ui/pages/edit.html`, find the `<!-- ============ Panel 2: Center - Module Drag Area ============ -->` section and the `edit-tabs` in the toolbar.

Replace the tab buttons:
```html
<div class="edit-tabs" role="tablist">
  <button class="edit-tab active" role="tab" aria-selected="true">排版</button>
  <button class="edit-tab disabled" role="tab" aria-selected="false" disabled>预览</button>
</div>
```

With (note: NO separate `x-data` — viewMode lives on the editPage() component):
```html
<div class="edit-tabs" role="tablist">
  <button class="edit-tab" :class="{ active: viewMode === 'edit' }" @click="viewMode = 'edit'" role="tab">排版</button>
  <button class="edit-tab" :class="{ active: viewMode === 'split' }" @click="viewMode = 'split'" role="tab">分栏</button>
  <button class="edit-tab" :class="{ active: viewMode === 'preview' }" @click="viewMode = 'preview'" role="tab">预览</button>
</div>
```

Then, find `<div class="edit-shell">` and add the Alpine.js binding there:
```html
<div class="edit-shell" x-data="editPage()" x-init="init()">
```

Then, replace the `.edit-center` content (remove old x-data from .edit-center since it's now on .edit-shell):

```html
<div class="edit-center">
  <!-- Sticky toolbar -->
  <header class="edit-toolbar">
    <div class="edit-toolbar-left">
      <div class="book-title-wrap">
        <h1 class="book-title" x-text="currentBook || '选择书目'"></h1>
        <span class="book-meta" x-text="modules.length + ' 个模块 · 约 ' + totalWords + ' 字'"></span>
      </div>
    </div>
    <div class="edit-tabs" role="tablist">
      <button class="edit-tab" :class="{ active: viewMode === 'edit' }" @click="viewMode = 'edit'" role="tab">排版</button>
      <button class="edit-tab" :class="{ active: viewMode === 'split' }" @click="viewMode = 'split'" role="tab">分栏</button>
      <button class="edit-tab" :class="{ active: viewMode === 'preview' }" @click="viewMode = 'preview'" role="tab">预览</button>
    </div>
    <div class="edit-toolbar-right">
      <!-- Book selector dropdown -->
      <select class="select-shell" x-model="currentBook" @change="loadBook($event.target.value)">
        <option value="">选择书目…</option>
        <template x-for="book in books" :key="book.stem">
          <option :value="book.stem" x-text="book.stem"></option>
        </template>
      </select>
      <button class="btn btn-primary" @click="saveModules()">
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M15 3v5h5"/></svg>
        <span>保存</span>
      </button>
    </div>
  </header>

  <!-- Split view container (no x-data — viewMode from editPage() on .edit-shell) -->
  <div class="split-container" style="display:flex; flex:1; overflow:hidden;">
    <!-- Module canvas (left) — hidden in preview-only mode -->
    <div class="module-canvas-wrap" x-show="viewMode !== 'preview'">
      <div class="module-canvas">
        <template x-for="(mod, idx) in modules" :key="mod.id">
          <div class="module-card" :class="{ selected: selectedId === mod.id, ['module-' + mod.type]: true }"
               @click="selectModule(mod.id)" :data-block-id="mod.id">
            <div class="module-drag-handle" aria-label="拖拽">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="18" r="1"/></svg>
            </div>
            <div class="module-type-icon">
              <!-- Icon based on type -->
              <template x-if="mod.type === 'chapter'">
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 7v14"/><path d="M16 12h2"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/></svg>
              </template>
              <template x-if="mod.type === 'paragraph' || mod.type === 'dialogue'">
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" x2="21" y1="6" y2="6"/><line x1="3" x2="21" y1="12" y2="12"/><line x1="3" x2="21" y1="18" y2="18"/></svg>
              </template>
              <template x-if="mod.type === 'image' || mod.type === 'cover'">
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg>
              </template>
              <template x-if="mod.type === 'divider'">
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14"/></svg>
              </template>
            </div>
            <div class="module-body">
              <div class="module-type-tag" x-text="typeLabel(mod.type)"></div>
              <template x-if="mod.type === 'chapter'">
                <div class="module-chapter-title" x-text="mod.content.replace(/^#+\s*/, '').replace(/\{#.*\}/, '').trim()"></div>
              </template>
              <template x-if="mod.type === 'paragraph' || mod.type === 'dialogue' || mod.type === 'quote'">
                <div class="module-text module-text-body" x-text="mod.content"></div>
              </template>
              <template x-if="mod.type === 'image' || mod.type === 'cover'">
                <div class="image-info">
                  <div class="image-filename" x-text="mod.content.match(/\((.+?)\)/)?.[1] || 'image'"></div>
                </div>
              </template>
              <template x-if="mod.type === 'divider'">
                <div class="divider-visual"><span class="divider-line"></span><span class="divider-label">分节</span><span class="divider-line"></span></div>
              </template>
            </div>
            <div class="module-meta">
              <span class="word-count" x-text="mod.word_count > 0 ? mod.word_count + ' 字' : typeLabel(mod.type)"></span>
            </div>
            <div class="module-actions">
              <button class="module-action-btn danger" title="删除" @click.stop="deleteModule(idx)">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/></svg>
              </button>
            </div>
          </div>
        </template>
        <template x-if="modules.length === 0">
          <div class="empty-drop-zone">
            <span>请选择书目加载模块</span>
          </div>
        </template>
      </div>
    </div>

    <!-- Preview pane (right) — hidden in edit-only mode -->
    <div class="preview-pane" x-show="viewMode !== 'edit'">
      <div class="preview-header">
        <span class="preview-label">预览</span>
        <span class="preview-book" x-text="currentBook"></span>
      </div>
      <div class="preview-scroll">
        <div class="kindle-preview" x-html="renderedPreview"></div>
      </div>
    </div>
  </div>

  <!-- Inspector panel stays as-is (existing HTML, wired in Task 7) -->
</div>
```

Add CSS for the split container and preview pane in the `<style>` block:

```css
/* Split view container */
.split-container { display: flex; flex: 1; overflow: hidden; }
.module-canvas-wrap { display: flex; flex-direction: column; overflow: hidden; border-right: 1px solid var(--border); }
.module-canvas-wrap .module-canvas { max-width: none; }

/* Preview pane */
.preview-pane { display: flex; flex-direction: column; overflow: hidden; background: #1a1a1a; }
.preview-header { display: flex; align-items: center; gap: 8px; padding: 12px 20px; border-bottom: 1px solid #333; flex: none; }
.preview-label { font: 500 10.5px/1 var(--font-mono); text-transform: uppercase; letter-spacing: .06em; color: var(--muted-foreground); }
.preview-book { font: 500 13px/1 var(--font-sans); color: var(--foreground); }
.preview-scroll { flex: 1; overflow-y: auto; padding: 0; }
```

Add CDN script tags before `</body>`:

```html
<!-- Alpine.js (reactivity, ~15KB) -->
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<!-- marked.js (Markdown renderer, ~20KB) -->
<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<!-- Edit page logic -->
<script src="../assets/js/edit.js"></script>
```

- [ ] **Step 2: Create `pdf2book-ui/assets/js/edit.js`**

```javascript
// PDF2BOOK Edit Page — Alpine.js component
// Handles: book loading, module rendering, preview, layout toggling, save

function editPage() {
  return {
    books: [],
    currentBook: '',
    modules: [],
    selectedId: null,
    renderedPreview: '',
    totalWords: 0,
    viewMode: 'split',  // 'edit' | 'split' | 'preview' — controls panel visibility

    async init() {
      await this.loadBooks();
      // Watch for module changes → update preview
      this.$watch('modules', () => this.updatePreview(), { deep: true });
    },

    async loadBooks() {
      try {
        const resp = await fetch('/api/books');
        const data = await resp.json();
        this.books = data.books.filter(b => b.has_book_md);
      } catch (e) {
        console.error('Failed to load books:', e);
      }
    },

    async loadBook(stem) {
      if (!stem) return;
      try {
        const resp = await fetch(`/api/books/${encodeURIComponent(stem)}/modules`);
        const data = await resp.json();
        this.modules = data.modules;
        this.totalWords = this.modules.reduce((sum, m) => sum + m.word_count, 0);
        if (this.modules.length > 0) {
          this.selectedId = this.modules[0].id;
        }
        this.updatePreview();
      } catch (e) {
        console.error('Failed to load book modules:', e);
      }
    },

    selectModule(id) {
      this.selectedId = id;
    },

    get selectedModule() {
      return this.modules.find(m => m.id === this.selectedId);
    },

    // Toggle a layout class on the selected module
    toggleLayoutClass(className) {
      const mod = this.selectedModule;
      if (!mod) return;
      const idx = mod.layout_classes.indexOf(className);
      if (idx >= 0) {
        mod.layout_classes.splice(idx, 1);
      } else {
        mod.layout_classes.push(className);
      }
    },

    // Check if selected module has a layout class
    hasLayoutClass(className) {
      const mod = this.selectedModule;
      return mod && mod.layout_classes.includes(className);
    },

    // Set alignment (exclusive — only one at a time)
    setAlignment(align) {
      const mod = this.selectedModule;
      if (!mod) return;
      // Remove existing alignment classes
      mod.layout_classes = mod.layout_classes.filter(
        c => !c.startsWith('align-')
      );
      if (align !== 'justify') {
        mod.layout_classes.push(`align-${align}`);
      }
    },

    // Set spacing (exclusive)
    setSpacing(spacing) {
      const mod = this.selectedModule;
      if (!mod) return;
      mod.layout_classes = mod.layout_classes.filter(
        c => !c.startsWith('spacing-')
      );
      if (spacing !== 'normal') {
        mod.layout_classes.push(`spacing-${spacing}`);
      }
    },

    // Delete a module
    deleteModule(idx) {
      this.modules.splice(idx, 1);
      this.totalWords = this.modules.reduce((sum, m) => sum + m.word_count, 0);
    },

    // Render preview from modules
    updatePreview() {
      const md = this.modulesToMarkdown();
      this.renderedPreview = marked.parse(md);
    },

    // Convert modules to Markdown for preview rendering
    modulesToMarkdown() {
      let lines = [];
      let inChapter = false;
      for (const mod of this.modules) {
        const content = mod.content.trim();
        if (mod.heading_level === 1) {
          if (inChapter) lines.push(':::');
          lines.push('::: {.chapter}');
          lines.push(content);
          inChapter = true;
        } else if (mod.layout_classes.length > 0) {
          const cls = mod.layout_classes.map(c => '.' + c).join(' ');
          lines.push(`::: {${cls}}`);
          lines.push(content);
          lines.push(':::');
        } else {
          lines.push(content);
        }
      }
      if (inChapter) lines.push(':::');
      return lines.join('\n\n');
    },

    // Save modules to server
    async saveModules() {
      if (!this.currentBook) return;
      try {
        const resp = await fetch(`/api/books/${encodeURIComponent(this.currentBook)}/modules`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ modules: this.modules }),
        });
        const data = await resp.json();
        if (data.status === 'ok') {
          console.log('Saved:', data.module_count, 'modules');
        }
      } catch (e) {
        console.error('Save failed:', e);
      }
    },

    // Get display label for module type
    typeLabel(type) {
      const labels = {
        chapter: '章节',
        paragraph: '正文段落',
        image: '图片',
        cover: '封面',
        divider: '分隔',
        quote: '引文',
        dialogue: '对话',
        toc: '目录',
        other: '其他',
      };
      return labels[type] || type;
    },
  };
}
```

- [ ] **Step 3: Verify the page loads**

Run: `python -m pdf2book.cli web --port 8000`
Open: `http://localhost:8000/pages/edit`
Expected: Page loads with Alpine.js, split view layout, book selector dropdown

- [ ] **Step 4: Commit**

```bash
git add pdf2book-ui/pages/edit.html pdf2book-ui/assets/js/edit.js
git commit -m "feat: add split-view edit page with live preview and Alpine.js"
```

---

### Task 7: Inspector Panel Wiring

**Files:**
- Modify: `pdf2book-ui/pages/edit.html` (wire inspector controls to Alpine.js)
- Modify: `pdf2book-ui/assets/js/edit.js` (add inspector-related methods)

- [ ] **Step 1: Wire inspector controls in edit.html**

Replace the static inspector content with Alpine-bound controls. Find the `<aside class="inspector-panel">` section and replace its inner content:

```html
<aside class="inspector-panel">
  <div class="inspector-header">
    <div class="inspector-title-wrap">
      <span class="inspector-label">属性</span>
      <span class="inspector-block-type" x-text="selectedModule ? typeLabel(selectedModule.type) : '未选择'"></span>
      <span class="inspector-block-id" x-text="selectedModule ? '#' + selectedModule.id : ''"></span>
    </div>
  </div>

  <div class="inspector-body" x-show="selectedModule" style="display:none">
    <!-- Content editor -->
    <div class="inspector-section">
      <label class="inspector-section-label">内容</label>
      <textarea class="inspector-textarea" rows="8"
                x-model="selectedModule.content"
                @input="updatePreview()"></textarea>
    </div>

    <!-- Typesetting settings -->
    <div class="inspector-section">
      <div class="inspector-section-header">
        <span class="inspector-section-label">排版设置</span>
      </div>
      <div class="inspector-fields">
        <!-- First line indent -->
        <div class="inspector-field-row">
          <span class="field-label">首行缩进</span>
          <div class="field-control">
            <button class="toggle-input" :class="{ 'is-on': !hasLayoutClass('no-indent') }"
                    @click="toggleLayoutClass('no-indent')">
              <div class="toggle-core"><span></span></div>
              <span class="toggle-label">2em</span>
            </button>
          </div>
        </div>
        <!-- Alignment -->
        <div class="inspector-field-row">
          <span class="field-label">对齐方式</span>
          <div class="field-control">
            <div class="pill-group">
              <button class="pill-btn" :class="{ active: hasLayoutClass('align-left') }"
                      @click="setAlignment('left')" title="左对齐">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" x2="21" y1="6" y2="6"/><line x1="3" x2="15" y1="12" y2="12"/><line x1="3" x2="18" y1="18" y2="18"/></svg>
              </button>
              <button class="pill-btn" :class="{ active: !hasLayoutClass('align-left') && !hasLayoutClass('align-center') }"
                      @click="setAlignment('justify')" title="两端对齐">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" x2="21" y1="6" y2="6"/><line x1="3" x2="21" y1="12" y2="12"/><line x1="3" x2="21" y1="18" y2="18"/></svg>
              </button>
              <button class="pill-btn" :class="{ active: hasLayoutClass('align-center') }"
                      @click="setAlignment('center')" title="居中">
                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" x2="21" y1="6" y2="6"/><line x1="6" x2="18" y1="12" y2="12"/><line x1="4" x2="20" y1="18" y2="18"/></svg>
              </button>
            </div>
          </div>
        </div>
        <!-- Paragraph spacing -->
        <div class="inspector-field-row">
          <span class="field-label">段间距</span>
          <div class="field-control">
            <div class="pill-group">
              <button class="pill-btn" :class="{ active: hasLayoutClass('spacing-tight') }"
                      @click="setSpacing('tight')">0.5em</button>
              <button class="pill-btn" :class="{ active: !hasLayoutClass('spacing-tight') && !hasLayoutClass('spacing-loose') }"
                      @click="setSpacing('normal')">1em</button>
              <button class="pill-btn" :class="{ active: hasLayoutClass('spacing-loose') }"
                      @click="setSpacing('loose')">1.5em</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Actions -->
    <div class="inspector-section inspector-section-actions">
      <div class="inspector-section-label">操作</div>
      <div class="action-buttons">
        <button class="btn btn-secondary sm btn-full" @click="splitModule()">
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 3v6h6"/></svg>
          拆分段落
        </button>
        <button class="btn btn-secondary sm btn-full" @click="mergeModule()">
          合并到上一段
        </button>
        <button class="btn btn-ghost sm btn-full danger-btn" @click="deleteSelectedModule()">
          <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/></svg>
          删除模块
        </button>
      </div>
    </div>
  </div>

  <!-- Empty state when no module selected -->
  <div class="inspector-body" x-show="!selectedModule" style="display:none">
    <div class="inspector-section">
      <p style="color: var(--muted-foreground); font-size: 13px; text-align: center; padding: 2em 0;">
        选择一个模块以编辑属性
      </p>
    </div>
  </div>
</aside>
```

- [ ] **Step 2: Add split/merge/delete methods to edit.js**

Add these methods to the `editPage()` return object in `pdf2book-ui/assets/js/edit.js`:

```javascript
    // Split selected module at cursor position (simplified: split in half)
    splitModule() {
      const mod = this.selectedModule;
      if (!mod || mod.type !== 'paragraph') return;
      const idx = this.modules.findIndex(m => m.id === mod.id);
      const content = mod.content.trim();
      const mid = Math.floor(content.length / 2);
      // Find nearest sentence break
      let splitPos = content.indexOf('。', mid);
      if (splitPos === -1 || splitPos > mid + 50) splitPos = mid;
      splitPos += 1; // Include the period in the first half

      const firstHalf = content.substring(0, splitPos).trim();
      const secondHalf = content.substring(splitPos).trim();

      mod.content = firstHalf;
      const newMod = {
        id: 'm' + (this.modules.length + 1),
        type: 'paragraph',
        content: secondHalf,
        layout_classes: [...mod.layout_classes],
        word_count: secondHalf.length,
        heading_level: null,
        heading_id: null,
      };
      this.modules.splice(idx + 1, 0, newMod);
      this.selectedId = newMod.id;
      this.totalWords = this.modules.reduce((sum, m) => sum + m.word_count, 0);
    },

    // Merge selected module with previous
    mergeModule() {
      const mod = this.selectedModule;
      if (!mod) return;
      const idx = this.modules.findIndex(m => m.id === mod.id);
      if (idx === 0) return;
      const prev = this.modules[idx - 1];
      if (prev.type !== 'paragraph' && prev.type !== 'dialogue') return;

      prev.content = prev.content.trim() + '\n\n' + mod.content.trim();
      prev.word_count = prev.content.length;
      this.modules.splice(idx, 1);
      this.selectedId = prev.id;
      this.totalWords = this.modules.reduce((sum, m) => sum + m.word_count, 0);
    },

    // Delete selected module
    deleteSelectedModule() {
      const mod = this.selectedModule;
      if (!mod) return;
      const idx = this.modules.findIndex(m => m.id === mod.id);
      this.modules.splice(idx, 1);
      this.selectedId = this.modules.length > 0
        ? this.modules[Math.min(idx, this.modules.length - 1)].id
        : null;
      this.totalWords = this.modules.reduce((sum, m) => sum + m.word_count, 0);
    },
```

- [ ] **Step 3: Verify inspector wiring**

Run: `python -m pdf2book.cli web --port 8000`
Open: `http://localhost:8000/pages/edit`
Test:
1. Select a book from dropdown → modules load
2. Click a module → inspector shows its content and type
3. Toggle "首行缩进" → preview updates (indent disappears)
4. Click "居中" → preview updates (text centers)
5. Edit content in textarea → preview updates live
6. Click "保存" → modules saved to book.md

- [ ] **Step 4: Commit**

```bash
git add pdf2book-ui/pages/edit.html pdf2book-ui/assets/js/edit.js
git commit -m "feat: wire inspector panel to per-module typesetting controls"
```

---

## Self-Review

### Spec coverage check
- ✅ Preview functionality → Task 5 (split view + marked.js rendering)
- ✅ 排版调整功能 → Task 5 + Task 7 (inspector panel wired to layout classes)
- ✅ Per-module排版 → Task 2-3 (parser) + Task 4 (API) + Task 5 (CSS utility classes)
- ✅ Split view → Task 5 (edit.html restructured for split layout)
- ✅ FastAPI + static HTML → Task 1 (server scaffold)
- ✅ CLI integration → Task 1 (`web` subcommand)
- ✅ Kindle CSS compatibility → Task 5 (utility classes follow KDP constraints)

### Placeholder scan
- No TBD/TODO in plan
- All code blocks are complete implementations
- All test code is concrete

### Type consistency
- `Module` dataclass fields consistent across parser, serializer, API models, and frontend
- `ModuleType` enum values match between Python and JS (lowercase strings)
- API route paths consistent between server and frontend fetch calls
- CSS class names consistent: `no-indent`, `align-left`, `align-center`, `align-justify`, `spacing-tight`, `spacing-normal`, `spacing-loose`
