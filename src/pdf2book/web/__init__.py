"""FastAPI-based web UI for pdf2book (optional, requires '[web]' extra)."""

from __future__ import annotations

from pdf2book.web.server import create_app

__all__ = ["create_app"]
