"""Unit tests for PaddlePPBackend parsing logic.

Uses the static spike fixture (sample_page1_res.json) to avoid loading the
1.5GB PP-StructureV3 model. Covers _extract_json, _parse_elements, _page_size.
The end-to-end `recognize` path is exercised in T12 (tests/test_e2e.py).
"""

from __future__ import annotations

import json
from pathlib import Path

from pdf2book.config import OCRConfig
from pdf2book.ocr.paddle_pp import PaddlePPBackend

FIXTURE = Path(__file__).parent / "fixtures" / "spike_output" / "sample_page1_res.json"


def _backend() -> PaddlePPBackend:
    """Build a backend without initializing the model (parser-only use)."""
    return PaddlePPBackend(OCRConfig())


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_extract_json_handles_save_to_json_shape() -> None:
    # The fixture is save_to_json output: top-level keys, no "res" wrapper.
    raw = _load_fixture()
    backend = _backend()
    data = backend._extract_json(raw)
    assert "parsing_res_list" in data
    assert data["width"] == 2480
    assert data["height"] == 3509


def test_extract_json_strips_res_wrapper() -> None:
    # Runtime res.json wraps the payload in {"res": {...}}.
    backend = _backend()

    class FakeRes:
        json = {"res": {"width": 100, "parsing_res_list": []}}

    data = backend._extract_json(FakeRes())
    assert data["width"] == 100
    assert data["parsing_res_list"] == []


def test_extract_json_handles_empty_result() -> None:
    backend = _backend()
    assert backend._extract_json(object()) == {}
    assert backend._extract_json(None) == {}


def test_parse_elements_field_names() -> None:
    backend = _backend()
    raw = _load_fixture()
    elements = backend._parse_elements(raw)
    assert len(elements) == 4

    first = elements[0]
    assert first.type == "paragraph_title"
    assert first.text == "Chapter 1"
    assert first.order_index == 1  # block_order, not block_id (which is 0)
    assert first.bbox.x1 == 293
    assert first.bbox.y2 == 433
    assert first.title_level is None  # PP does not serialize title_level
    assert first.dropped is False

    second = elements[1]
    assert second.type == "text"
    assert second.order_index == 2
    assert second.bbox.x1 == 291

    assert elements[2].type == "paragraph_title"
    assert elements[2].text == "Chapter 2"
    assert elements[3].type == "text"


def test_parse_elements_empty_input() -> None:
    backend = _backend()
    assert backend._parse_elements({}) == []
    assert backend._parse_elements({"parsing_res_list": []}) == []


def test_parse_elements_alias_fallback() -> None:
    # Simulate a future PP version that reverts to unprefixed field names.
    backend = _backend()
    data = {
        "parsing_res_list": [
            {
                "label": "text",
                "bbox": [10, 20, 30, 40],
                "content": "alias content",
                "order_index": 5,
            }
        ]
    }
    elements = backend._parse_elements(data)
    assert len(elements) == 1
    assert elements[0].type == "text"
    assert elements[0].text == "alias content"
    assert elements[0].order_index == 5
    assert elements[0].bbox.x2 == 30


def test_page_size_from_top_level_keys() -> None:
    backend = _backend()
    raw = _load_fixture()
    width, height = backend._page_size(raw)
    assert width == 2480
    assert height == 3509


def test_page_size_fallback_on_missing() -> None:
    backend = _backend()
    assert backend._page_size({}) == (0.0, 0.0)
    assert backend._page_size({"width": "not-a-number", "height": 10}) == (0.0, 0.0)


def test_page_size_fallback_on_partial() -> None:
    backend = _backend()
    # Only width present -> KeyError on height -> fallback.
    assert backend._page_size({"width": 100}) == (0.0, 0.0)
