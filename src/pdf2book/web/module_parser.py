"""Parse book.md into structured modules and serialize back.

Handles the Pandoc-flavored Markdown used by pdf2book:
  - ``::: {.chapter}`` — structural wrapper, stripped on parse,
    reconstructed on serialize around H1 sections.
  - ``::: {.dialogue}`` — paragraph-level wrapper, preserved as
    ``layout_classes=["dialogue"]`` on the module.
  - ``::: {.toc-list}`` — TOC block, classified as TOC module.
  - ``::: {.no-indent .center}`` — per-module layout classes.
  - ``# Heading {#anchor}`` — chapter/section heading.
  - ``![](path)`` — image (cover if path starts with ``pages/``).
  - ``---`` — divider.
  - ``> text`` — blockquote / low-confidence marker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class ModuleType(str, Enum):
    """Display-level module types for the editor."""
    CHAPTER = "chapter"
    PARAGRAPH = "paragraph"
    IMAGE = "image"
    COVER = "cover"
    DIVIDER = "divider"
    QUOTE = "quote"
    DIALOGUE = "dialogue"
    TOC = "toc"
    OTHER = "other"


@dataclass
class Module:
    """A single editable block in the module editor."""
    id: str
    type: ModuleType
    content: str
    layout_classes: list[str] = field(default_factory=list)
    word_count: int = 0
    heading_level: int | None = None
    heading_id: str | None = None


# Regex patterns
_FENCED_DIV_START = re.compile(r"^:::\s*\{([^}]+)\}")
_FENCED_DIV_END = re.compile(r"^:::\s*$")
_HEADING = re.compile(r"^(#{1,4})\s+(.+?)(?:\s*\{#([^}]+)\})?\s*$")
_IMAGE = re.compile(r"^!\[.*?\]\((.+?)\)")
_HR = re.compile(r"^---+\s*$")
_LOW_CONF = re.compile(r"^>\[low-confidence\]")

# Structural classes that are stripped (not per-module layout)
_STRUCTURAL_CLASSES = {"chapter"}


def _parse_fenced_classes(line: str) -> list[str] | None:
    """Extract classes from a ``::: {.class1 .class2}`` line.

    Returns None if not a fenced div start.
    """
    m = _FENCED_DIV_START.match(line)
    if not m:
        return None
    raw = m.group(1)
    # Classes are space-separated, each prefixed with a dot
    classes = [c.strip().lstrip(".") for c in raw.split() if c.strip().startswith(".")]
    return classes


def _count_chars(text: str) -> int:
    """Count meaningful characters (non-whitespace, non-markup)."""
    # Strip markdown syntax characters
    clean = re.sub(r"[#>*\-\[\]!(){}=|`~]", "", text)
    clean = re.sub(r"\s+", "", clean)
    return len(clean)


def _classify_image(path_str: str) -> ModuleType:
    """Classify image as COVER (full-page) or IMAGE (inline/cropped)."""
    if "pages/" in path_str or path_str.startswith("pages"):
        return ModuleType.COVER
    return ModuleType.IMAGE


def parse_modules(md_text: str) -> list[Module]:
    """Parse book.md text into a list of Module objects.

    See module docstring for the supported Markdown format.
    """
    lines = md_text.split("\n")
    modules: list[Module] = []
    module_counter = 0

    # Fenced div state
    div_stack: list[list[str]] = []  # Stack of class lists for nested divs
    # Track if current div is structural (chapter/toc) — its content is
    # parsed as inner modules, not wrapped.
    div_is_structural: list[bool] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for fenced div start
        div_classes = _parse_fenced_classes(line)
        if div_classes is not None:
            is_structural = any(c in _STRUCTURAL_CLASSES for c in div_classes)
            div_stack.append(div_classes)
            div_is_structural.append(is_structural)
            i += 1
            continue

        # Check for fenced div end
        if _FENCED_DIV_END.match(line):
            if div_stack:
                div_stack.pop()
                div_is_structural.pop()
            i += 1
            continue

        # Skip empty lines
        if not line.strip():
            i += 1
            continue

        # Determine layout classes from non-structural divs on the stack
        layout_classes = []
        for idx, classes in enumerate(div_stack):
            if not div_is_structural[idx]:
                layout_classes.extend(classes)

        # Classify the block starting at this line
        # Collect the full block (until empty line, fenced div, or end)
        block_lines = [line]
        j = i + 1
        while j < len(lines):
            next_line = lines[j]
            # Stop at empty line, fenced div start/end, or new block type
            if (not next_line.strip()
                    or _FENCED_DIV_START.match(next_line)
                    or _FENCED_DIV_END.match(next_line)):
                break
            block_lines.append(next_line)
            j += 1

        block_text = "\n".join(block_lines)
        module_counter += 1
        mid = f"m{module_counter}"

        # Determine module type
        # Check heading
        heading_match = _HEADING.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2)
            hid = heading_match.group(3)
            modules.append(Module(
                id=mid,
                type=ModuleType.CHAPTER,
                content=block_text,
                layout_classes=layout_classes,
                word_count=_count_chars(title),
                heading_level=level,
                heading_id=hid,
            ))
        # Check image
        elif _IMAGE.match(line):
            img_match = _IMAGE.match(line)
            path_str = img_match.group(1)
            mtype = _classify_image(path_str)
            modules.append(Module(
                id=mid,
                type=mtype,
                content=block_text,
                layout_classes=layout_classes,
                word_count=0,
            ))
        # Check HR (divider)
        elif _HR.match(line):
            modules.append(Module(
                id=mid,
                type=ModuleType.DIVIDER,
                content=block_text,
                layout_classes=layout_classes,
                word_count=0,
            ))
        # Check low-confidence or blockquote
        elif line.startswith(">"):
            mtype = ModuleType.QUOTE
            # Low-confidence blocks are stored as quotes with preserved marker
            modules.append(Module(
                id=mid,
                type=mtype,
                content=block_text,
                layout_classes=layout_classes,
                word_count=_count_chars(block_text),
            ))
        # Check TOC
        elif "toc-list" in layout_classes:
            modules.append(Module(
                id=mid,
                type=ModuleType.TOC,
                content=block_text,
                layout_classes=layout_classes,
                word_count=0,
            ))
        # Check dialogue
        elif "dialogue" in layout_classes:
            modules.append(Module(
                id=mid,
                type=ModuleType.DIALOGUE,
                content=block_text,
                layout_classes=layout_classes,
                word_count=_count_chars(block_text),
            ))
        else:
            # Default: paragraph
            modules.append(Module(
                id=mid,
                type=ModuleType.PARAGRAPH,
                content=block_text,
                layout_classes=layout_classes,
                word_count=_count_chars(block_text),
            ))

        i = j

    return modules


def serialize_modules(modules: list[Module]) -> str:
    """Serialize a list of Module objects back to book.md text.

    Reconstructs:
    - ``::: {.chapter}`` wrappers around H1 sections
    - ``::: {.class1 .class2}`` wrappers for per-module layout classes
    - ``::: {.dialogue}`` for dialogue modules
    - ``::: {.toc-list}`` for TOC modules

    Structural classes (``chapter``) are NEVER emitted as per-module
    layout — they wrap H1 sections at the top level.
    """
    if not modules:
        return ""

    blocks: list[str] = []
    in_chapter = False

    for mod in modules:
        content = mod.content.strip()

        # H1 heading → start a new chapter wrapper
        if mod.heading_level == 1:
            # Close previous chapter wrapper
            if in_chapter:
                blocks.append(":::")
            blocks.append("::: {.chapter}")
            blocks.append(content)
            in_chapter = True
            continue

        # Non-H1 module inside a chapter wrapper
        # Determine wrapper classes
        wrapper_classes: list[str] = []

        # Preserve dialogue class
        if mod.type == ModuleType.DIALOGUE and "dialogue" not in mod.layout_classes:
            wrapper_classes.append("dialogue")
        # Preserve toc-list class
        if mod.type == ModuleType.TOC and "toc-list" not in mod.layout_classes:
            wrapper_classes.append("toc-list")

        # Add user layout classes (exclude structural ones)
        for cls in mod.layout_classes:
            if cls not in _STRUCTURAL_CLASSES and cls not in wrapper_classes:
                wrapper_classes.append(cls)

        if wrapper_classes:
            class_str = " ".join(f".{c}" for c in wrapper_classes)
            blocks.append(f"::: {{{class_str}}}")
            blocks.append(content)
            blocks.append(":::")
        else:
            blocks.append(content)

    # Close final chapter wrapper
    if in_chapter:
        blocks.append(":::")

    # Join with double newlines, clean up excessive blank lines
    result = "\n\n".join(blocks)
    # Collapse 3+ newlines to 2
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip() + "\n"
