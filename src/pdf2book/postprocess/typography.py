"""CJK punctuation normalization (Phase 5).

Runs as post-process step 0.5 (before confidence filtering) so downstream
stages (header/footer detection, merging, title inference) see clean text.
Three transformations, all CJK-aware (pure ASCII text is untouched):

1. **Half-width → full-width**: ``你好,世界.`` → ``你好，世界。``
   Only converts when the punctuation is adjacent to a CJK character (so
   English sentences keep their half-width ``,``/``.``).

2. **Repeated punctuation compression**: ``好！！！`` → ``好！``；
   ``完了。。`` → ``完了。``. Collapses runs of identical CJK terminators.

3. **ASCII quote pairing**: ``他说"你好"走了`` → ``他说"你好"走了``.
   Walks the text toggling between opening (``"``) and closing (``"``)
   quotes; same for single quotes ``'`` ↔ ``'``. Only fires when the text
   contains at least one CJK character (otherwise English contractions
   like ``don't`` would be mangled).

Mutates ``Element.text`` in place; returns the same list for chaining.
"""

from __future__ import annotations

import re

from pdf2book.config import PostprocessConfig
from pdf2book.ocr.models import PageResult

# Half-width → full-width punctuation map (applied only in CJK context).
_HALF_TO_FULL: dict[str, str] = {
    ",": "，",
    ".": "。",  # only when followed by CJK or end-of-text (see _normalize_half_width)
    "!": "！",
    "?": "？",
    ":": "：",
    ";": "；",
}

# Repeated CJK punctuation to compress (one copy retained).
_REPEAT_RE = re.compile(r"([。！？，；：])\1+")

# ASCII double/single quotes → CJK paired quotes (toggled by state machine).
_ASCII_DQUOTE = '"'
_ASCII_SQUOTE = "'"
_CJK_DQUOTE_OPEN = "\u201c"  # "
_CJK_DQUOTE_CLOSE = "\u201d"  # "
_CJK_SQUOTE_OPEN = "\u2018"  # '
_CJK_SQUOTE_CLOSE = "\u2019"  # '


def normalize_punctuation(
    pages: list[PageResult], cfg: PostprocessConfig
) -> list[PageResult]:
    """Normalize CJK punctuation in text elements.

    Mutates elements in place; returns the same list for chaining.
    No-op when ``cfg.normalize_punctuation`` is False or pages is empty.
    """
    if not cfg.normalize_punctuation or not pages:
        return pages

    for page in pages:
        for el in page.elements:
            if el.dropped:
                continue
            if not el.text:
                continue
            el.text = _normalize_text(el.text)
    return pages


def _normalize_text(text: str) -> str:
    """Apply the three normalization rules to a single string."""
    if not _has_cjk(text):
        return text  # pure ASCII: leave untouched
    text = _normalize_half_width(text)
    text = _compress_repeats(text)
    text = _pair_quotes(text)
    return text


def _has_cjk(text: str) -> bool:
    """Return True if the text contains any CJK character or punctuation."""
    return any(_is_cjk(ch) for ch in text)


def _is_cjk(ch: str) -> bool:
    """Check if a character is CJK (ideograph or CJK punctuation).

    Mirrors ``merger._is_cjk`` exactly; duplicated here to avoid a circular
    import (merger imports from postprocess package).
    """
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0x20000 <= code <= 0x2A6DF
        or 0x3000 <= code <= 0x303F
        or 0xFF00 <= code <= 0xFFEF
    )


def _normalize_half_width(text: str) -> str:
    """Convert half-width punctuation to full-width in CJK context.

    A half-width punctuation char is converted when:
      - the previous char is CJK, OR
      - the next char is CJK
    This avoids converting ``,``/``.`` in English embedded in CJK text
    (e.g. "see RFC 1234, section 2" inside a Chinese paragraph stays as-is
    when both neighbors are ASCII).
    """
    if not text:
        return text
    chars = list(text)
    n = len(chars)
    for i, ch in enumerate(chars):
        if ch not in _HALF_TO_FULL:
            continue
        prev_cjk = i > 0 and _is_cjk(chars[i - 1])
        next_cjk = i + 1 < n and _is_cjk(chars[i + 1])
        # Special-case "." : only convert when prev is CJK (avoid converting
        # decimal points like "3.14" or English sentence periods).
        if ch == ".":
            if prev_cjk:
                chars[i] = _HALF_TO_FULL[ch]
        else:
            if prev_cjk or next_cjk:
                chars[i] = _HALF_TO_FULL[ch]
    return "".join(chars)


def _compress_repeats(text: str) -> str:
    """Collapse runs of identical CJK terminators: 。。→。 ！！！→！."""
    return _REPEAT_RE.sub(r"\1", text)


def _pair_quotes(text: str) -> str:
    """Pair ASCII ``"``/``'`` into CJK ``""``/``''`` via a state machine.

    Only fires when the text contains CJK (caller ``_normalize_text`` already
    gates on this, but we re-check for safety). Walks left-to-right, toggling
    a ``next_is_open`` flag per quote type.
    """
    if not _has_cjk(text):
        return text
    chars: list[str] = []
    dquote_open = True  # next " becomes opening "
    squote_open = True  # next ' becomes opening '
    for ch in text:
        if ch == _ASCII_DQUOTE:
            chars.append(_CJK_DQUOTE_OPEN if dquote_open else _CJK_DQUOTE_CLOSE)
            dquote_open = not dquote_open
        elif ch == _ASCII_SQUOTE:
            chars.append(_CJK_SQUOTE_OPEN if squote_open else _CJK_SQUOTE_CLOSE)
            squote_open = not squote_open
        else:
            chars.append(ch)
    return "".join(chars)


__all__ = ["normalize_punctuation"]
