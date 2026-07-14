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
