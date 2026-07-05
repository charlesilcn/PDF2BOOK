"""Spike: run PP-StructureV3 on a sample page and dump the JSON structure.

Usage:
    python scripts/spike_pp_structure.py [image_path]

This script is a one-off investigation tool for T2. It prints:
1. The result object type and its public attributes
2. The JSON structure (first 5000 chars) to confirm field names
3. Saves full JSON + markdown to tests/fixtures/spike_output/

The findings feed back into src/pdf2book/ocr/paddle_pp.py parsing logic.
"""

import json
import sys
from pathlib import Path


def main() -> None:
    image = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/sample_page1.png"
    image_path = Path(image)
    if not image_path.exists():
        print(f"ERROR: {image_path} not found")
        sys.exit(1)

    from paddleocr import PPStructureV3

    print(f"[spike] Initializing PP-StructureV3...")
    pipe = PPStructureV3(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=True,
    )

    print(f"[spike] Predict on {image_path}")
    results = list(pipe.predict(input=str(image_path)))
    print(f"[spike] Got {len(results)} result(s)")

    if not results:
        print("[spike] No results")
        return

    res = results[0]

    print("\n=== Result type ===")
    print(type(res).__name__)

    print("\n=== Public attributes ===")
    attrs = [a for a in dir(res) if not a.startswith("_")]
    print(attrs)

    print("\n=== save_to_json / save_to_markdown ===")
    out_dir = Path("tests/fixtures/spike_output")
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        res.save_to_json(str(out_dir))
        print(f"  save_to_json -> {out_dir}")
    except Exception as e:
        print(f"  save_to_json failed: {e}")
    try:
        res.save_to_markdown(str(out_dir))
        print(f"  save_to_markdown -> {out_dir}")
    except Exception as e:
        print(f"  save_to_markdown failed: {e}")

    print("\n=== JSON content (first 5000 chars) ===")
    json_data = None
    if hasattr(res, "json"):
        json_data = res.json
        print("(via .json attribute)")
    elif hasattr(res, "to_json"):
        json_data = res.to_json()
        print("(via .to_json() method)")

    if json_data is not None:
        text = json.dumps(json_data, indent=2, ensure_ascii=False, default=str)
        print(text[:5000])
        if len(text) > 5000:
            print(f"\n... ({len(text)} chars total, truncated)")

    print("\n=== Saved files ===")
    for p in out_dir.iterdir():
        print(f"  {p} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
