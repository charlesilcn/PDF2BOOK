"""Post-processing subpackage.

Stages run in order (orchestrated by processor.py):
  0. typography.normalize_punctuation  — CJK punctuation normalization
                                         (half→full width, compress repeats,
                                         pair ASCII quotes into CJK quotes).
  1. confidence_filter.filter_by_confidence — drop low-confidence text elements
                                             (pre-filter for AI review cost).
  2. header_footer.remove        — drop PP-labeled headers/footers + page numbers
                                   + cross-page repeated running heads.
  3. merger.merge_paragraphs     — stitch paragraphs split across page breaks.
  4. structure.infer_title_levels — assign H1/H2/H3 from literary rules.
  5. structure.to_markdown       — assemble single book.md from PageResult list.
"""
