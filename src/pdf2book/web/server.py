"""FastAPI app factory — serves static HTML + REST API."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pdf2book.config import AppConfig

# pdf2book-ui/ lives at project root (parents[3]: web/ -> pdf2book/ -> src/ -> root).
_UI_DIR = Path(__file__).resolve().parents[3] / "pdf2book-ui"


def create_app(cfg: AppConfig, log: logging.Logger) -> FastAPI:
    """Create and configure the FastAPI application.

    Serves static files from ``pdf2book-ui/`` and mounts REST API routes
    under ``/api``. The ``cfg`` is stored in app state for route handlers.

    Static files from the UI root are mounted at ``/`` as a catch-all,
    AFTER all API/page routes are registered so routes take precedence.
    This serves root-level CSS (colors_and_type.css, components.css) and
    the assets/ directory (JS, icons, kindle-preview.css) in one mount.
    """
    app = FastAPI(title="PDF2BOOK Web UI", version="0.1.0")
    app.state.cfg = cfg
    app.state.log = log

    # Register API and page routes first (they take precedence over the
    # root static mount below).
    from pdf2book.web.routes import register_routes

    register_routes(app, _UI_DIR)

    # Mount pdf2book-ui/ root as catch-all static files.
    # Serves: /colors_and_type.css, /components.css, /assets/js/edit.js,
    # /assets/kindle-preview.css, /assets/icons/*.svg, /favicon.ico, etc.
    if _UI_DIR.exists():
        app.mount("/", StaticFiles(directory=_UI_DIR), name="root")

    log.info("Web UI serving from %s", _UI_DIR)
    return app
