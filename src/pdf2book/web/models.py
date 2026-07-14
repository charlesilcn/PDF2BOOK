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
