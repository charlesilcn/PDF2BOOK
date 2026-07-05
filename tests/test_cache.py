"""Tests for SQLite page cache (T5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf2book.config import OCRConfig
from pdf2book.utils.cache import Cache, cfg_hash, pdf_sha1


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.db"


def _fake_pdf(tmp_path: Path, name: str = "book.pdf", content: bytes = b"pdf-bytes") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def test_save_load_roundtrip(db_path: Path) -> None:
    with Cache(db_path) as c:
        c.save("hash-a", 0, 300, "cfg-x", '{"page":0}')
        assert c.has("hash-a", 0, 300, "cfg-x")
        assert c.load("hash-a", 0, 300, "cfg-x") == '{"page":0}'


def test_has_returns_false_for_missing(db_path: Path) -> None:
    with Cache(db_path) as c:
        c.save("hash-a", 0, 300, "cfg-x", "{}")
        assert not c.has("hash-a", 1, 300, "cfg-x")
        assert not c.has("hash-a", 0, 200, "cfg-x")
        assert not c.has("hash-a", 0, 300, "cfg-y")


def test_load_returns_none_for_missing(db_path: Path) -> None:
    with Cache(db_path) as c:
        assert c.load("nope", 0, 300, "cfg-x") is None


def test_save_overwrites_same_key(db_path: Path) -> None:
    with Cache(db_path) as c:
        c.save("hash-a", 0, 300, "cfg-x", "v1")
        c.save("hash-a", 0, 300, "cfg-x", "v2")
        assert c.load("hash-a", 0, 300, "cfg-x") == "v2"


def test_done_pages_returns_cached_indices(db_path: Path) -> None:
    with Cache(db_path) as c:
        c.save("hash-a", 0, 300, "cfg-x", "{}")
        c.save("hash-a", 2, 300, "cfg-x", "{}")
        c.save("hash-a", 5, 300, "cfg-x", "{}")
        assert c.done_pages("hash-a", 300, "cfg-x") == {0, 2, 5}


def test_done_pages_isolates_by_dpi(db_path: Path) -> None:
    """Changing dpi must invalidate the cache (re-render at new resolution)."""
    with Cache(db_path) as c:
        c.save("hash-a", 0, 300, "cfg-x", "{}")
        c.save("hash-a", 1, 300, "cfg-x", "{}")
        assert c.done_pages("hash-a", 200, "cfg-x") == set()
        assert c.done_pages("hash-a", 300, "cfg-x") == {0, 1}


def test_done_pages_isolates_by_cfg_hash(db_path: Path) -> None:
    """Changing OCR config (e.g. enabling table rec) must invalidate cache."""
    with Cache(db_path) as c:
        c.save("hash-a", 0, 300, "cfg-x", "{}")
        assert c.done_pages("hash-a", 300, "cfg-y") == set()


def test_done_pages_isolates_by_pdf_hash(db_path: Path) -> None:
    with Cache(db_path) as c:
        c.save("hash-a", 0, 300, "cfg-x", "{}")
        assert c.done_pages("hash-b", 300, "cfg-x") == set()


def test_resume_scenario(db_path: Path) -> None:
    """Run 2 of 5 pages, interrupt, resume: only 3 pages remain to compute."""
    with Cache(db_path) as c:
        c.set_job_state("hash-a", 5)
        c.save("hash-a", 0, 300, "cfg-x", "{}")
        c.save("hash-a", 1, 300, "cfg-x", "{}")
        done = c.done_pages("hash-a", 300, "cfg-x")
        total = c.job_state("hash-a")
        remaining = set(range(total)) - done
        assert total == 5
        assert done == {0, 1}
        assert remaining == {2, 3, 4}


def test_job_state_set_and_overwrite(db_path: Path) -> None:
    with Cache(db_path) as c:
        assert c.job_state("hash-a") is None
        c.set_job_state("hash-a", 10)
        assert c.job_state("hash-a") == 10
        c.set_job_state("hash-a", 12)  # page count changed (file replaced)
        assert c.job_state("hash-a") == 12


def test_job_state_isolates_by_pdf_hash(db_path: Path) -> None:
    with Cache(db_path) as c:
        c.set_job_state("hash-a", 10)
        assert c.job_state("hash-b") is None


def test_context_manager_closes_connection(db_path: Path) -> None:
    c = Cache(db_path)
    with c:
        c.save("hash-a", 0, 300, "cfg-x", "{}")
    assert c._conn is None  # closed


def test_operations_require_open(db_path: Path) -> None:
    c = Cache(db_path)
    with pytest.raises(RuntimeError, match="not open"):
        c.has("hash-a", 0, 300, "cfg-x")


def test_pdf_sha1_stable_and_distinct(tmp_path: Path) -> None:
    pdf_a = _fake_pdf(tmp_path, "a.pdf", b"content-a")
    pdf_b = _fake_pdf(tmp_path, "b.pdf", b"content-b")
    assert pdf_sha1(pdf_a) == pdf_sha1(pdf_a)  # stable
    assert pdf_sha1(pdf_a) != pdf_sha1(pdf_b)  # distinct


def test_cfg_hash_stable_for_same_cfg() -> None:
    cfg1 = OCRConfig()
    cfg2 = OCRConfig()
    assert cfg_hash(cfg1) == cfg_hash(cfg2)


def test_cfg_hash_changes_when_dpi_changes() -> None:
    assert cfg_hash(OCRConfig(dpi=300)) != cfg_hash(OCRConfig(dpi=200))


def test_cfg_hash_changes_when_backend_options_change() -> None:
    base = OCRConfig()
    assert cfg_hash(base) != cfg_hash(OCRConfig(use_table_recognition=True))
    assert cfg_hash(base) != cfg_hash(OCRConfig(use_formula_recognition=True))
    assert cfg_hash(base) != cfg_hash(OCRConfig(use_region_detection=False))


def test_cfg_hash_changes_when_paddleocr_version_changes(monkeypatch) -> None:
    """A paddleocr upgrade must invalidate the cache (cfg_hash changes)."""
    from pdf2book.utils import cache as cache_mod

    monkeypatch.setattr(cache_mod, "_paddleocr_version", lambda: "3.7.0")
    h1 = cfg_hash(OCRConfig())
    monkeypatch.setattr(cache_mod, "_paddleocr_version", lambda: "3.8.0")
    h2 = cfg_hash(OCRConfig())
    assert h1 != h2


def test_persists_across_reopen(db_path: Path) -> None:
    with Cache(db_path) as c:
        c.save("hash-a", 0, 300, "cfg-x", '{"persist":true}')
        c.set_job_state("hash-a", 5)
    # Reopen the same db file: data must survive.
    with Cache(db_path) as c:
        assert c.has("hash-a", 0, 300, "cfg-x")
        assert c.load("hash-a", 0, 300, "cfg-x") == '{"persist":true}'
        assert c.job_state("hash-a") == 5
        assert c.done_pages("hash-a", 300, "cfg-x") == {0}
