"""Post-processing subpackage.

Stages run in order (orchestrated by pipeline.py):
  1. header_footer.remove        — drop PP-labeled headers/footers + page numbers
                                   + cross-page repeated running heads.
  2. merger.merge_paragraphs     — stitch paragraphs split across page breaks.
  3. structure.infer_title_levels — assign H1/H2/H3 from literary rules.
  4. structure.to_markdown       — assemble single book.md from PageResult list.
"""
