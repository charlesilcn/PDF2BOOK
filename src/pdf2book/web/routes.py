"""REST API routes for the web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from pdf2book.config import AppConfig
from pdf2book.web.models import (
    BookContentResponse,
    BookInfo,
    BookListResponse,
    ModuleData,
    ModuleListResponse,
    SaveBookRequest,
    SaveModulesRequest,
)


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

    # --- API: Module list (parsed from book.md) ---
    @app.get("/api/books/{stem}/modules")
    async def get_modules(stem: str) -> ModuleListResponse:
        from pdf2book.web.module_parser import parse_modules

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
