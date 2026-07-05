"""Kindle-friendly EPUB stylesheet templates.

Exposes `default_css_path()` returning the path to `kindle.css` bundled
with the package. `PandocBuilder` falls back to this when no custom CSS is
configured.
"""

from __future__ import annotations

from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_CSS = _PKG_DIR / "kindle.css"


def default_css_path() -> Path:
    """Return the bundled Kindle-safe CSS path."""
    return _CSS


__all__ = ["default_css_path"]
