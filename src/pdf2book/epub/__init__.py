"""EPUB generation subpackage.

Owns the Pandoc-driven markdown -> EPUB conversion:
  - `metadata.BookMetadata`: pydantic model for title/author/lang/etc.
  - `metadata.write_meta_yaml`: emits a Pandoc-readable YAML metadata block.
  - `builder.EpubBuilder` (ABC) + `builder.PandocBuilder` (default impl).
  - `templates/kindle.css`: Kindle-safe stylesheet (no flex/grid/@media,
    no body font-size per KDP guidance).
"""
