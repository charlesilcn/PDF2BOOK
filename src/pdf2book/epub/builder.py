"""EPUB builder abstraction + Pandoc-backed implementation (T10)."""

from __future__ import annotations

import re
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path

import pypandoc

from pdf2book.epub.metadata import BookMetadata, write_meta_yaml
from pdf2book.epub.templates import default_css_path
from pdf2book.utils.logger import get_logger

_log = get_logger()


class EpubBuilder(ABC):
    """Abstract EPUB builder: markdown + metadata -> .epub."""

    @abstractmethod
    def build(
        self,
        markdown: Path,
        meta: BookMetadata,
        out: Path,
        cover: Path | None = None,
        css: Path | None = None,
    ) -> Path:
        """Render `markdown` into `out` (.epub). Returns `out`.

        `cover` (optional image) and `css` (optional stylesheet) override
        any defaults the implementation may pick.
        """
        ...


class PandocBuilder(EpubBuilder):
    """Default EPUB builder backed by Pandoc (via pypandoc_binary).

    Pipeline:
      1. `write_meta_yaml` emits `meta.md` (a YAML metadata block) next to
         `book.md`.
      2. Pandoc reads `[meta.md, book.md]` as markdown inputs (meta first
         so its YAML block becomes document metadata), with the working
         directory set to `book.md`'s parent so relative image paths
         (`images/pN_eM.png`) resolve.
      3. `--split-level={meta.chapter_level}` splits the EPUB into one XHTML
         file per H1 (the Kindle page-break mechanism).
      4. Falls back to the bundled `kindle.css` when no `css` is given.
      5. Post-processing strips Pandoc's auto-generated title_page.xhtml,
         removes nav.xhtml from the spine, and deletes the metadata-title
         H1 injected at the top of the first chapter. This ensures the
         EPUB reading order exactly matches the book.md page order (which
         matches the PDF page order), rather than having Pandoc's title
         page and nav TOC appear before the book's front matter.
    """

    def build(
        self,
        markdown: Path,
        meta: BookMetadata,
        out: Path,
        cover: Path | None = None,
        css: Path | None = None,
    ) -> Path:
        out.parent.mkdir(parents=True, exist_ok=True)
        work_dir = markdown.parent

        meta_md = write_meta_yaml(meta, work_dir)
        css_path = css if css is not None else default_css_path()

        # sort_files=False so meta.md stays before book.md. Without this
        # pypandoc alphabetizes inputs and book.md would precede meta.md,
        # putting the YAML metadata block after the body (Pandoc still
        # parses it, but meta-first is the idiomatic, robust order).
        # Resolve to absolute paths: pypandoc._identify_path rejects relative.
        inputs = [str(meta_md.resolve()), str(markdown.resolve())]

        args: list[str] = [
            "--standalone",
            "--toc",
            f"--toc-depth={meta.toc_depth}",
            # `--split-level` replaces the deprecated `--epub-chapter-level`
            # in pandoc >= 3.x; same semantics (split into one XHTML file per
            # header at this level). This is the Kindle page-break mechanism.
            f"--split-level={meta.chapter_level}",
            f"--css={css_path.resolve()}",
        ]
        if cover is not None:
            args.append(f"--epub-cover-image={cover.resolve()}")

        _log.info(
            "Pandoc: %s + %s -> %s (toc-depth=%d, chapter-level=%d)",
            meta_md.name,
            markdown.name,
            out.name,
            meta.toc_depth,
            meta.chapter_level,
        )
        # Resolve `out` to an absolute path: pypandoc runs with `cworkdir`
        # set to `work_dir.resolve()`, so a relative `out` would be interpreted
        # relative to `cworkdir` (e.g. `.pdf2book_test/output.epub` becomes
        # `.pdf2book_test/.pdf2book_test/output.epub`).
        out_abs = out.resolve() if not out.is_absolute() else out
        pypandoc.convert_file(
            inputs,
            "epub",
            format="markdown",
            outputfile=str(out_abs),
            extra_args=args,
            sort_files=False,
            cworkdir=str(work_dir.resolve()),
        )

        _strip_pandoc_auto_pages(out_abs, meta)
        return out_abs


# ---------------------------------------------------------------------------
# Post-processing: strip Pandoc auto-generated pages to preserve PDF order
# ---------------------------------------------------------------------------

# Regex patterns for OPF manifest/spine entries. These are intentionally
# flexible (attribute-order agnostic) so they survive minor Pandoc version
# changes in how it emits the OPF XML.

# <item id="title_page_xhtml" href="text/title_page.xhtml" .../>
_TITLE_PAGE_MANIFEST_RE = re.compile(
    r'<item[^>]*href="[^"]*title_page[^"]*"[^>]*/>\s*',
    re.IGNORECASE,
)

# <itemref idref="title_page_xhtml" .../>
_TITLE_PAGE_SPINE_RE = re.compile(
    r'<itemref[^>]*idref="title_page_xhtml"[^>]*/>\s*',
    re.IGNORECASE,
)

# <itemref idref="nav" .../>
# Nav stays in the manifest (EPUB3 spec requires a nav document) but is
# removed from the spine so it doesn't appear as a reading page.
_NAV_SPINE_RE = re.compile(
    r'<itemref[^>]*idref="nav"[^>]*/>\s*',
    re.IGNORECASE,
)


def _strip_pandoc_auto_pages(epub_path: Path, meta: BookMetadata) -> None:
    """Remove Pandoc-auto-generated pages from the EPUB.

    Pandoc inserts three types of auto-generated content when ``title``
    metadata is set and ``--toc`` is used:

    1. ``title_page.xhtml`` — a title page (book title/author/date) inserted
       at the front of the spine. This breaks the PDF page order by appearing
       before the book's actual front matter (frontispiece, copyright, etc.).
    2. ``nav.xhtml`` in the spine — the navigation TOC appears as a reading
       page. We keep it in the manifest (EPUB3 spec requires a nav document
       with ``properties="nav"``) but remove it from the spine so it's
       accessible via the reader's TOC button, not as a page in the flow.
    3. ``<h1 class="unnumbered">TITLE</h1>`` at the top of the first chapter
       file — the metadata title rendered as a body H1, which creates a
       spurious chapter and shifts the ``--split-level`` split point.
    4. ``<section id="TITLE" class="level1 unnumbered">`` wrapper around the
       first chapter — the section's ``id="TITLE"`` is referenced by
       nav.xhtml's TOC, creating a spurious "book title" entry above the
       real chapters. Stripping the wrapper (open + close tags) removes the
       false TOC target.

    Additionally, ``nav.xhtml`` is repaired:
    - The TOC entry pointing to ``#TITLE`` is removed, and its child entries
      (e.g. 前言) are promoted to the top level so they remain navigable.
    - The ``landmarks`` nav entry referencing the deleted ``title_page.xhtml``
      is removed (broken link).

    Finally, ``toc.ncx`` (used by older Kindle / EPUB2 readers for the TOC
    navigation index) is repaired the same way:
    - The ``navPoint`` pointing to the deleted ``title_page.xhtml`` is removed.
    - The outer ``navPoint`` whose ``content src`` references ``#TITLE`` is
      removed, and its child ``navPoint`` entries are promoted to the top
      level of ``navMap`` so they remain navigable.

    Stripping all of this ensures the EPUB's reading order exactly matches
    the ``book.md`` page order (which matches the PDF page order), and the
    reader's TOC navigation (both EPUB3 nav and EPUB2 NCX) shows only real
    chapters with working index links.
    """
    title_escaped = re.escape(meta.title)
    title_h1_re = re.compile(
        rf'<h1\s+class="unnumbered"\s*>\s*{title_escaped}\s*</h1>\s*',
        re.IGNORECASE,
    )
    # Section wrapper around first chapter: <section ... id="TITLE" ...>
    section_open_re = re.compile(
        rf'<section\s+[^>]*id="{title_escaped}"[^>]*>\s*',
        re.IGNORECASE,
    )
    # Matching close tag: the last </section> before </body>
    section_close_re = re.compile(
        r'\s*</section>(\s*</body>)',
        re.IGNORECASE,
    )
    # nav.xhtml TOC: <li ...><a href="...#TITLE">TITLE</a><ol>children</ol></li>
    # Replace with just the children (promote them to parent level)
    nav_toc_title_re = re.compile(
        rf'<li\s+[^>]*>\s*'
        rf'<a\s+[^>]*href="[^"]*#{title_escaped}"[^>]*>[^<]*</a>\s*'
        rf'<ol\s+class="toc">(.*?)</ol>\s*'
        rf'</li>',
        re.DOTALL | re.IGNORECASE,
    )
    # nav.xhtml landmarks: <li><a href="...title_page...">...</a></li>
    nav_landmark_title_re = re.compile(
        r'<li>\s*<a\s+[^>]*href="[^"]*title_page[^"]*"[^>]*>[^<]*</a>\s*</li>\s*',
        re.DOTALL | re.IGNORECASE,
    )

    tmp_path = epub_path.with_suffix(".epub.tmp")
    stripped_title_page = False
    stripped_title_h1 = False
    stripped_nav_spine = False
    stripped_section_wrapper = False
    fixed_nav_toc = False
    fixed_nav_landmarks = False
    fixed_ncx = False

    with zipfile.ZipFile(epub_path, "r") as zin:
        with zipfile.ZipFile(
            tmp_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as zout:
            for item in zin.infolist():
                name = item.filename

                # 1. Drop title_page.xhtml entirely from the archive.
                if "title_page" in name:
                    stripped_title_page = True
                    continue

                data = zin.read(name)

                # 2. Remove title_page + nav from OPF manifest/spine.
                if name.endswith(".opf"):
                    text = data.decode("utf-8")
                    new = _TITLE_PAGE_MANIFEST_RE.sub("", text)
                    new = _TITLE_PAGE_SPINE_RE.sub("", new)
                    new = _NAV_SPINE_RE.sub("", new)
                    if new != text:
                        stripped_nav_spine = True
                    data = new.encode("utf-8")

                # 3. Process XHTML: strip metadata H1, section wrapper,
                #    and repair nav.xhtml (TOC + landmarks).
                elif name.endswith(".xhtml"):
                    text = data.decode("utf-8")
                    new = title_h1_re.sub("", text)
                    if new != text:
                        stripped_title_h1 = True

                    # Strip section wrapper (open tag + matching close tag).
                    # Only apply close-tag stripping if open tag was found,
                    # to avoid removing a </section> from an unrelated file.
                    after_open = section_open_re.sub("", new)
                    if after_open != new:
                        stripped_section_wrapper = True
                        after_close = section_close_re.sub(r"\1", after_open)
                        new = after_close

                    # Repair nav.xhtml: remove book-title TOC entry (promote
                    # children) and remove title_page landmark (broken link).
                    if "nav" in name:
                        nav_new = nav_toc_title_re.sub(r"\1", new)
                        if nav_new != new:
                            fixed_nav_toc = True
                            new = nav_new
                        nav_new = nav_landmark_title_re.sub("", new)
                        if nav_new != new:
                            fixed_nav_landmarks = True
                            new = nav_new

                    if new != text:
                        data = new.encode("utf-8")

                # 4. Process NCX: repair toc.ncx so older readers (Kindle KF7,
                #    EPUB2) get a working TOC index. Removes the navPoint
                #    pointing at the deleted title_page.xhtml and promotes
                #    children of the #TITLE navPoint to the top level.
                elif name.endswith(".ncx"):
                    text = data.decode("utf-8")
                    new, fixed = _repair_toc_ncx(text, meta.title)
                    if fixed:
                        fixed_ncx = True
                        data = new.encode("utf-8")

                # Preserve original compression: mimetype MUST be stored
                # (not deflated) or the EPUB becomes invalid.
                ct = (
                    zipfile.ZIP_STORED
                    if name == "mimetype"
                    else item.compress_type
                )
                zout.writestr(item, data, compress_type=ct)

    tmp_path.replace(epub_path)

    parts = []
    if stripped_title_page:
        parts.append("title_page.xhtml")
    if stripped_nav_spine:
        parts.append("nav from spine")
    if stripped_title_h1:
        parts.append("metadata title H1")
    if stripped_section_wrapper:
        parts.append("section wrapper")
    if fixed_nav_toc:
        parts.append("nav TOC title entry")
    if fixed_nav_landmarks:
        parts.append("nav landmarks title_page link")
    if fixed_ncx:
        parts.append("toc.ncx broken links")
    if parts:
        _log.info("Stripped Pandoc auto pages: %s", ", ".join(parts))
    else:
        _log.debug("No Pandoc auto pages found to strip")


def _repair_toc_ncx(ncx_text: str, book_title: str) -> tuple[str, bool]:
    """Repair ``toc.ncx``: remove broken navPoints and promote children.

    Pandoc emits two spurious navPoints at the front of ``navMap``:

    1. A navPoint whose ``<content src="..."/>`` points at
       ``title_page.xhtml`` — that file is deleted in step 1 of
       ``_strip_pandoc_auto_pages``, so the link is broken.
    2. A navPoint whose ``<content src="..."/>`` references the
       ``#BOOK_TITLE`` anchor on the first chapter — that anchor was on the
       section wrapper we stripped, so the link is also broken. This
       navPoint typically wraps the real "前言" (preface) navPoint as a
       child; we promote the child to the top level of ``navMap``.

    The function parses ``navMap`` with a stack-based navPoint matcher
    (navPoint elements can nest), identifies the two spurious ones, and
    rewrites ``navMap`` with only the real chapter navPoints (children of
    the #TITLE navPoint are hoisted to the top level).

    Returns ``(new_ncx_text, fixed)`` where ``fixed`` is True if any
    repair was applied.
    """
    title_escaped = re.escape(book_title)

    # Locate <navMap>...</navMap> as a single string. NCX is small enough
    # that loading the whole navMap into memory is fine.
    navmap_re = re.compile(
        r"(<navMap>)(.*?)(</navMap>)", re.DOTALL | re.IGNORECASE
    )
    m = navmap_re.search(ncx_text)
    if not m:
        return ncx_text, False

    navmap_body = m.group(2)
    indent = _detect_navpoint_indent(navmap_body)

    # Parse navPoints into a tree. Each navPoint opens with
    # <navPoint ...>, contains a <navLabel>...</navLabel> and a
    # <content src="..." />, optionally contains child navPoints, and
    # closes with </navPoint>.
    navpoints, _ = _parse_navpoints(navmap_body)

    # Filter: drop navPoints whose content src points to title_page.xhtml
    # or anchors #BOOK_TITLE. Promote children of dropped navPoints to
    # the parent level (flatten), recursing into the dropped node's
    # children so grandchildren also propagate correctly.
    kept = _filter_and_promote_navpoints(navpoints, title_escaped)

    if kept is None:
        # Nothing to filter; original tree was already clean.
        return ncx_text, False

    # Re-serialize navMap with the kept navPoints at the top level.
    # Leading "\n" so the first <navPoint> starts on a new line after
    # <navMap>; trailing indent removed (the </navMap> close tag is on
    # its own line because each navPoint ends with "\n{pad}</navPoint>").
    new_body = "\n" + "".join(
        _serialize_navpoint(np, indent, depth=1) for np in kept
    )
    new_navmap = m.group(1) + new_body + m.group(3)
    new_ncx = ncx_text[: m.start()] + new_navmap + ncx_text[m.end():]
    return new_ncx, True


def _detect_navpoint_indent(navmap_body: str) -> str:
    """Detect the indentation unit used inside ``<navMap>``.

    Pandoc emits two spaces per nesting level. We detect this by looking
    at the indentation of the first ``<navPoint>`` tag and use that as
    the per-depth indent for re-serialization.
    """
    m = re.search(r"\n([ \t]*)<navPoint", navmap_body)
    return m.group(1) if m else "  "


def _parse_navpoints(text: str) -> tuple[list[dict], int]:
    """Parse navPoint elements from ``text`` into a tree.

    Returns ``(nodes, end_pos)`` where ``nodes`` is a list of top-level
    navPoint dicts and ``end_pos`` is the position in ``text`` after the
    last parsed navPoint. Each dict has keys:
      - ``open_tag``: full ``<navPoint ...>`` text (with attrs)
      - ``label``: text inside ``<navLabel><text>...</text></navLabel>``
      - ``content_src``: ``src`` attribute of ``<content />``
      - ``children``: list of child navPoint dicts (nested)
      - ``raw``: the entire navPoint text (open + body + close) — used
        only when we keep a node untouched.
    """
    nodes: list[dict] = []
    pos = 0
    open_re = re.compile(r"<navPoint\b[^>]*>", re.IGNORECASE)
    close_re = re.compile(r"</navPoint>", re.IGNORECASE)

    while True:
        open_m = open_re.search(text, pos)
        if not open_m:
            break
        # Skip to the start of this navPoint.
        start = open_m.start()
        open_tag = open_m.group(0)
        body_start = open_m.end()

        # Find the matching </navPoint>, accounting for nested navPoints.
        # We track depth: +1 for each <navPoint ...>, -1 for each </navPoint>.
        depth = 1
        scan = body_start
        while depth > 0:
            next_open = open_re.search(text, scan)
            next_close = close_re.search(text, scan)
            if not next_close:
                # Malformed NCX: bail out, treat the rest as body.
                body_end = len(text)
                break
            if next_open and next_open.start() < next_close.start():
                depth += 1
                scan = next_open.end()
            else:
                depth -= 1
                scan = next_close.end()
                if depth == 0:
                    body_end = next_close.start()
                    break
        else:
            body_end = scan

        body = text[body_start:body_end]

        # Extract label and content src from body.
        label = ""
        label_m = re.search(
            r"<navLabel>\s*<text>(.*?)</text>\s*</navLabel>",
            body,
            re.DOTALL | re.IGNORECASE,
        )
        if label_m:
            label = label_m.group(1).strip()

        content_src = ""
        content_m = re.search(
            r'<content\s+[^>]*src="([^"]*)"[^>]*/>',
            body,
            re.IGNORECASE,
        )
        if content_m:
            content_src = content_m.group(1)

        # Recurse into body to find nested navPoints. We strip the
        # label/content so they don't interfere, but since we match
        # <navPoint...> tags directly, leftover label/content text is
        # fine — _parse_navpoints only looks for navPoint tags.
        children, _ = _parse_navpoints(body)

        nodes.append(
            {
                "open_tag": open_tag,
                "label": label,
                "content_src": content_src,
                "children": children,
                "raw": text[start : (body_end + len("</navPoint>"))]
                if depth == 0
                else text[start:body_end],
            }
        )

        # Move pos past this navPoint's closing tag.
        pos = body_end + len("</navPoint>")
        if depth != 0:
            # Malformed; stop parsing to avoid infinite loop.
            break

    return nodes, pos


def _filter_and_promote_navpoints(
    nodes: list[dict],
    title_escaped: str,
) -> list[dict] | None:
    """Filter out broken navPoints, promoting their children to parent level.

    A navPoint is "broken" if its ``content_src``:
      - contains ``title_page`` (file was deleted), OR
      - contains ``#BOOK_TITLE`` anchor (section wrapper was stripped)

    When a broken navPoint has children, those children replace it in the
    parent's list (recursively — if a child is also broken, its children
    propagate too).

    Returns the filtered list, or ``None`` if no navPoint was removed
    (so the caller can skip the rewrite and preserve the original NCX
    byte-for-byte).
    """
    broken_re = re.compile(
        rf"(title_page|#{title_escaped})", re.IGNORECASE
    )

    def is_broken(np: dict) -> bool:
        return bool(broken_re.search(np["content_src"] or ""))

    def filter_recursive(items: list[dict]) -> tuple[list[dict], bool]:
        """Returns (filtered_list, any_removed)."""
        result: list[dict] = []
        any_removed = False
        for np in items:
            # First recurse into children so their broken nodes are
            # filtered/promoted before we decide what to do with this node.
            filtered_children, child_removed = filter_recursive(
                np["children"]
            )
            if child_removed:
                np["children"] = filtered_children
                any_removed = True

            if is_broken(np):
                # Drop this node, promote its (already-filtered) children.
                result.extend(np["children"])
                any_removed = True
            else:
                result.append(np)
        return result, any_removed

    filtered, any_removed = filter_recursive(nodes)
    if not any_removed:
        return None
    return filtered


def _serialize_navpoint(np: dict, indent: str, depth: int) -> str:
    """Serialize a navPoint dict back to NCX XML at the given depth.

    ``indent`` is the per-depth indentation unit. Depth 1 is the top
    level inside ``<navMap>`` (which itself is at depth 0).
    """
    pad = indent * depth
    child_pad = indent * (depth + 1)
    grandchild_pad = indent * (depth + 2)

    # Reuse the original open_tag (preserves id="navPoint-N" attrs and
    # any attribute ordering). If we ever need to rewrite ids, do it here.
    open_tag = np["open_tag"]

    # Build label and content. Pandoc's format is:
    #   <navLabel>
    #     <text>LABEL</text>
    #   </navLabel>
    #   <content src="SRC" />
    label_block = (
        f"{child_pad}<navLabel>\n"
        f"{grandchild_pad}<text>{np['label']}</text>\n"
        f"{child_pad}</navLabel>\n"
    )
    content_block = f'{child_pad}<content src="{np["content_src"]}" />\n'

    if not np["children"]:
        return (
            f"{pad}{open_tag}\n"
            f"{label_block}"
            f"{content_block}"
            f"{pad}</navPoint>\n"
        )

    children_block = "".join(
        _serialize_navpoint(child, indent, depth + 1)
        for child in np["children"]
    )
    return (
        f"{pad}{open_tag}\n"
        f"{label_block}"
        f"{content_block}"
        f"{children_block}"
        f"{pad}</navPoint>\n"
    )


def make_epub_builder() -> EpubBuilder:
    """Factory: return the default Pandoc-backed builder."""
    return PandocBuilder()


__all__ = ["EpubBuilder", "PandocBuilder", "make_epub_builder"]
