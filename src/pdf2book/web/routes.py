"""REST API routes for the web UI."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from pdf2book.config import AppConfig
from pdf2book.web.convert_manager import get_convert_manager
from pdf2book.web.models import (
    BookContentResponse,
    BookInfo,
    BookListResponse,
    DashboardStats,
    InboxFile,
    InboxListResponse,
    LibraryBook,
    LibraryListResponse,
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

    # --- API: Serve raw workspace files (images, pages, etc.) ---
    @app.get("/api/books/{stem}/raw/{file_path:path}")
    async def serve_workspace_file(stem: str, file_path: str) -> FileResponse:
        wd = _work_dir(app, stem)
        full_path = (wd / file_path).resolve()
        # Security: ensure the path stays within the workspace directory
        try:
            full_path.relative_to(wd.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")
        if not full_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(full_path)

    # --- API: Inbox listing (PDFs waiting for conversion) ---
    @app.get("/api/inbox")
    async def list_inbox() -> InboxListResponse:
        inbox = _cfg(app).input_dir
        files: list[InboxFile] = []
        if inbox.exists():
            for child in sorted(inbox.iterdir()):
                if child.is_file() and child.suffix.lower() == ".pdf":
                    stat = child.stat()
                    files.append(InboxFile(
                        filename=child.name,
                        stem=child.stem,
                        size_bytes=stat.st_size,
                        modified_at=_dt.datetime.fromtimestamp(
                            stat.st_mtime, tz=_dt.timezone.utc
                        ).isoformat(),
                    ))
        return InboxListResponse(files=files)

    # --- API: Library listing (generated EPUBs) ---
    @app.get("/api/library")
    async def list_library() -> LibraryListResponse:
        lib = _cfg(app).output_dir
        books: list[LibraryBook] = []
        total_size = 0
        if lib.exists():
            for child in sorted(lib.iterdir()):
                if child.is_file() and child.suffix.lower() == ".epub":
                    stat = child.stat()
                    size = stat.st_size
                    total_size += size
                    books.append(LibraryBook(
                        stem=child.stem,
                        filename=child.name,
                        size_bytes=size,
                        modified_at=_dt.datetime.fromtimestamp(
                            stat.st_mtime, tz=_dt.timezone.utc
                        ).isoformat(),
                    ))
        return LibraryListResponse(books=books, total_size_bytes=total_size)

    # --- API: Download an EPUB ---
    @app.get("/api/library/{stem}/download")
    async def download_epub(stem: str) -> FileResponse:
        lib = _cfg(app).output_dir
        epub_path = lib / f"{stem}.epub"
        if not epub_path.exists():
            raise HTTPException(status_code=404, detail=f"EPUB '{stem}' not found")
        return FileResponse(
            epub_path,
            media_type="application/epub+zip",
            filename=epub_path.name,
        )

    # --- API: Delete an EPUB ---
    @app.delete("/api/library/{stem}")
    async def delete_epub(stem: str) -> dict:
        lib = _cfg(app).output_dir
        epub_path = lib / f"{stem}.epub"
        if not epub_path.exists():
            raise HTTPException(status_code=404, detail=f"EPUB '{stem}' not found")
        epub_path.unlink()
        return {"status": "ok", "stem": stem}

    # --- API: Dashboard aggregated stats ---
    @app.get("/api/stats")
    async def get_stats() -> DashboardStats:
        cfg = _cfg(app)
        ws = cfg.work_dir
        lib = cfg.output_dir
        inbox = cfg.input_dir

        def _dir_size(path: Path) -> int:
            if not path.exists():
                return 0
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

        def _count_files(path: Path, suffix: str) -> int:
            if not path.exists():
                return 0
            return sum(1 for f in path.iterdir() if f.is_file() and f.suffix.lower() == suffix)

        def _count_dirs(path: Path) -> int:
            if not path.exists():
                return 0
            return sum(1 for f in path.iterdir() if f.is_dir())

        return DashboardStats(
            workspace_count=_count_dirs(ws),
            library_count=_count_files(lib, ".epub"),
            inbox_count=_count_files(inbox, ".pdf"),
            library_size_bytes=_dir_size(lib),
            workspace_size_bytes=_dir_size(ws),
        )

    # --- API: Start a conversion (POST /api/convert) ---
    class ConvertRequest(BaseModel):
        """Request body for POST /api/convert."""
        stem: str
        resume: bool = False
        ai_review: bool = True

    @app.post("/api/convert")
    async def start_convert(req: ConvertRequest) -> dict:
        mgr = get_convert_manager()
        cfg = _cfg(app)
        try:
            status = mgr.start_conversion(
                cfg, req.stem, resume=req.resume, ai_review=req.ai_review
            )
            return status
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # --- API: Poll conversion status (GET /api/convert/{stem}/status) ---
    @app.get("/api/convert/{stem}/status")
    async def convert_status(stem: str) -> dict:
        mgr = get_convert_manager()
        status = mgr.get_status(stem)
        if status is None:
            raise HTTPException(status_code=404, detail=f"No conversion found for '{stem}'")
        return status

    # --- API: Collect AI review issues from book.md (GET /api/review/{stem}/issues) ---
    @app.get("/api/review/{stem}/issues")
    async def review_issues(stem: str) -> dict:
        """Scan book.md for low-confidence blocks, title issues, and other
        problems that the AI review stage would address. Returns the issues
        list so the review page can display them without running the AI.

        This endpoint is read-only: it does not modify book.md or call any
        LLM API. The actual AI correction happens in the conversion pipeline
        (``ai_review`` stage) or via the Skill path.
        """
        from pdf2book.epub.metadata import read_meta_yaml
        from pdf2book.review import collect_markdown_issues

        wd = _work_dir(app, stem)
        book_path = wd / "book.md"
        if not book_path.exists():
            raise HTTPException(status_code=404, detail="book.md not found")

        meta_path = wd / "meta.md"
        meta = read_meta_yaml(meta_path) if meta_path.exists() else None

        issues = collect_markdown_issues(book_path, meta, work_dir=wd)
        # Serialize Path objects and make the response JSON-friendly
        low_conf = []
        for item in issues.get("low_confidence_texts", []):
            entry = {
                "id": item["id"],
                "line": item["line"],
                "text": item["original_text"],
                "context_before": item.get("context_before", ""),
                "context_after": item.get("context_after", ""),
                "page_index": item.get("page_index"),
            }
            low_conf.append(entry)
        titles = []
        for item in issues.get("title_candidates", []):
            entry = {
                "id": item["id"],
                "line": item["line"],
                "title": item["title"],
                "issue": item.get("issue", ""),
                "context": item.get("context", ""),
            }
            titles.append(entry)
        para_issues = []
        for item in issues.get("paragraph_issues", []):
            entry = {
                "id": item.get("id", ""),
                "line": item.get("line", 0),
                "description": item.get("description", ""),
            }
            para_issues.append(entry)
        chapter_issues = []
        for item in issues.get("chapter_structure_issues", []):
            entry = {
                "id": item.get("id", ""),
                "line": item.get("line", 0),
                "description": item.get("description", ""),
            }
            chapter_issues.append(entry)
        toc_issues = []
        for item in issues.get("toc_issues", []):
            entry = {
                "id": item.get("id", ""),
                "line": item.get("line", 0),
                "description": item.get("description", ""),
            }
            toc_issues.append(entry)

        return {
            "stem": stem,
            "low_confidence_texts": low_conf,
            "title_candidates": titles,
            "paragraph_issues": para_issues,
            "chapter_structure_issues": chapter_issues,
            "toc_issues": toc_issues,
            "metadata": issues.get("metadata", {}),
            "total_issues": (
                len(low_conf) + len(titles) + len(para_issues)
                + len(chapter_issues) + len(toc_issues)
            ),
        }
