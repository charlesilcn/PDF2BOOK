"""SQLite page-level cache for PP-Structure JSON (resume support).

The OCR stage is by far the most expensive (1-3s/page on CPU). Post-processing
is cheap and deterministic, so we cache only the raw PP-Structure JSON per
page and re-run post-processing on every resume.

Cache key = (pdf_sha1, page_index, dpi, cfg_hash). cfg_hash includes
paddleocr.__version__ so a paddleocr upgrade silently invalidates the cache
rather than serving stale JSON with a different schema.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pdf2book.config import OCRConfig


def pdf_sha1(pdf_path: Path) -> str:
    """SHA1 of a PDF file's bytes (chunked for large books)."""
    h = hashlib.sha1()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cfg_hash(cfg: OCRConfig) -> str:
    """Hash OCR config + engine version. Version bump invalidates cache.

    The engine version is looked up per-backend so a RapidOCR upgrade
    doesn't invalidate PaddlePP cache entries (and vice versa). Backends
    without a local library (e.g. cloud_ocr) fall back to a static tag.
    """
    payload = {
        "backend": cfg.backend,
        "dpi": cfg.dpi,
        "use_table_recognition": cfg.use_table_recognition,
        "use_formula_recognition": cfg.use_formula_recognition,
        "use_region_detection": cfg.use_region_detection,
        "engine_version": _engine_version(cfg.backend),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _engine_version(backend: str) -> str:
    """Look up the OCR library version for a backend.

    Returns "unknown" if the library isn't installed (e.g. optional
    dependency not installed) — the cache key still differs by backend,
    so a missing library doesn't collide with a present one across
    backends, only within the same backend (which is fine: if the library
    is missing, OCR fails before cache lookup).
    """
    if backend in ("paddle_pp", "paddle_vl"):
        return _try_version("paddleocr")
    if backend == "rapid_ocr":
        return _try_version("rapidocr_onnxruntime")
    if backend == "cloud_ocr":
        # No local OCR library; the remote API version is opaque to us.
        # Use a static tag so cloud cache entries are isolated from local
        # backends and don't churn when httpx is upgraded.
        return "cloud-v1"
    return "unknown"


def _try_version(module_name: str) -> str:
    try:
        mod = __import__(module_name)  # type: ignore[import-not-found]
    except ImportError:
        return "unknown"
    return getattr(mod, "__version__", "unknown")


class Cache:
    """SQLite page cache for PP-Structure JSON results.

    Use as a context manager to ensure the connection is opened and closed::

        with Cache(db_path) as cache:
            if cache.has(ph, idx, dpi, ch):
                page_json = cache.load(ph, idx, dpi, ch)
            else:
                page_json = ...
                cache.save(ph, idx, dpi, ch, page_json)
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> Cache:
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS page_cache (
                pdf_hash TEXT NOT NULL,
                page_index INTEGER NOT NULL,
                dpi INTEGER NOT NULL,
                cfg_hash TEXT NOT NULL,
                page_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (pdf_hash, page_index, dpi, cfg_hash)
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS job_state (
                pdf_hash TEXT PRIMARY KEY,
                total_pages INTEGER NOT NULL
            )"""
        )
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Cache is not open; use `with Cache(...)` or call open()")
        return self._conn

    def has(self, pdf_hash: str, page_index: int, dpi: int, cfg_hash: str) -> bool:
        cur = self._require().execute(
            "SELECT 1 FROM page_cache WHERE pdf_hash=? AND page_index=? AND dpi=? AND cfg_hash=?",
            (pdf_hash, page_index, dpi, cfg_hash),
        )
        return cur.fetchone() is not None

    def save(
        self,
        pdf_hash: str,
        page_index: int,
        dpi: int,
        cfg_hash: str,
        page_json: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._require().execute(
            """INSERT OR REPLACE INTO page_cache
               (pdf_hash, page_index, dpi, cfg_hash, page_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pdf_hash, page_index, dpi, cfg_hash, page_json, now),
        )
        self._require().commit()

    def load(
        self, pdf_hash: str, page_index: int, dpi: int, cfg_hash: str
    ) -> str | None:
        cur = self._require().execute(
            "SELECT page_json FROM page_cache "
            "WHERE pdf_hash=? AND page_index=? AND dpi=? AND cfg_hash=?",
            (pdf_hash, page_index, dpi, cfg_hash),
        )
        row = cur.fetchone()
        return row[0] if row is not None else None

    def done_pages(self, pdf_hash: str, dpi: int, cfg_hash: str) -> set[int]:
        """Return cached page indices for this pdf+dpi+cfg (authoritative)."""
        cur = self._require().execute(
            "SELECT page_index FROM page_cache WHERE pdf_hash=? AND dpi=? AND cfg_hash=?",
            (pdf_hash, dpi, cfg_hash),
        )
        return {int(row[0]) for row in cur.fetchall()}

    def job_state(self, pdf_hash: str) -> int | None:
        """Return recorded total page count for this pdf, or None if unknown."""
        cur = self._require().execute(
            "SELECT total_pages FROM job_state WHERE pdf_hash=?",
            (pdf_hash,),
        )
        row = cur.fetchone()
        return int(row[0]) if row is not None else None

    def set_job_state(self, pdf_hash: str, total_pages: int) -> None:
        self._require().execute(
            """INSERT OR REPLACE INTO job_state (pdf_hash, total_pages)
               VALUES (?, ?)""",
            (pdf_hash, total_pages),
        )
        self._require().commit()
