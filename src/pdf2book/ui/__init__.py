"""Gradio Web UI extension for pdf2book.

This package is a **pure extension layer**: it adds a visual interface over
the existing ``ConversionPipeline`` and ``AppConfig`` without changing any
CLI behavior. Gradio is an optional dependency (``pip install 'pdf2book[gui]'``);
importing this package when gradio is absent raises ``ImportError``, which
the CLI ``gui`` subcommand catches and reports gracefully.

Modules:
  - ``detect``    — environment/dependency detection (drives the onboarding page)
  - ``theme``     — Glass theme + CSS animations
  - ``onboarding``— first-run setup page (OCR engine install + API key)
  - ``convert_tab``— PDF -> EPUB conversion tab with live progress
  - ``edit_tab``  — Markdown preview/edit tab (stage-1 output inspection)
  - ``review_tab``— AI correction before/after diff tab
  - ``library_tab``— cover preview/replace + generated EPUB library
  - ``app``       — assembles all tabs into a ``gr.Blocks`` and returns it
"""
