"""Enable `python -m pdf2book` invocation.

Thin wrapper around the Typer `app` defined in `pdf2book.cli`; no logic of
its own. Equivalent to the `pdf2book` console_script registered in
pyproject.toml.
"""

from pdf2book.cli import app

if __name__ == "__main__":
    app()
