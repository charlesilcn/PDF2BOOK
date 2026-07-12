"""Persist sensitive config (API keys) to a local ``.env`` file.

The project loads ``.env`` at startup via ``python-dotenv``
(``config.py`` ``load_dotenv``), and ``.env`` is in ``.gitignore`` so
secrets never reach version control. This module lets the Web UI's
"保存到 .env" button write API key / api_url / model entries safely:
existing entries with the same key are overwritten in place, comments
and blank lines are preserved, and empty values are skipped.
"""

from __future__ import annotations

from pathlib import Path


def save_to_env(env_path: Path, mapping: dict[str, str]) -> None:
    """Merge ``mapping`` into the ``.env`` file at ``env_path``.

    - Existing ``KEY=VALUE`` lines whose key is in ``mapping`` are overwritten
      in place (keeps file ordering stable).
    - Keys in ``mapping`` that are not yet present are appended.
    - Comment lines (``# ...``) and blank lines are preserved untouched.
    - Entries in ``mapping`` with a falsy value (``None`` / ``""``) are
      skipped — they neither overwrite nor append, so the UI can pass through
      empty fields without clobbering an existing value.
    - The file ends with a trailing newline.
    """
    existing_lines: list[str] = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()

    seen_keys: set[str] = set()
    out_lines: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in seen_keys:
                # Duplicate key already seen earlier in the file — drop the
                # later occurrence so the output has exactly one line per key.
                continue
            seen_keys.add(key)
            value = mapping.get(key)
            if value is not None and value != "":
                out_lines.append(f"{key}={value}")
            else:
                # Empty/missing value in mapping → keep the existing line
                # untouched (don't clobber an existing secret with a blank).
                out_lines.append(line)
        else:
            out_lines.append(line)

    for key, value in mapping.items():
        if key in seen_keys:
            continue
        if value is None or value == "":
            continue
        out_lines.append(f"{key}={value}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(out_lines)
    if out_lines:
        text += "\n"
    env_path.write_text(text, encoding="utf-8")
