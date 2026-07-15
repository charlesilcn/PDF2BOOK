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


class LibraryBook(BaseModel):
    """An EPUB file in the library/ directory."""

    stem: str
    filename: str
    size_bytes: int
    modified_at: str  # ISO 8601 timestamp


class LibraryListResponse(BaseModel):
    """Response for GET /api/library."""

    books: list[LibraryBook]
    total_size_bytes: int


class InboxFile(BaseModel):
    """A PDF file in the inbox/ directory."""

    filename: str
    stem: str
    size_bytes: int
    modified_at: str  # ISO 8601 timestamp


class InboxListResponse(BaseModel):
    """Response for GET /api/inbox."""

    files: list[InboxFile]


class DashboardStats(BaseModel):
    """Aggregated statistics for the dashboard."""

    workspace_count: int
    library_count: int
    inbox_count: int
    library_size_bytes: int
    workspace_size_bytes: int
