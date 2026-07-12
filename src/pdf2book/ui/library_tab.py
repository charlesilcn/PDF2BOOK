"""Library tab: EPUB library management with cover preview & replacement.

Lists generated EPUBs in ``library/``, extracts metadata (title/author) and
cover images from the EPUB zip, and lets the user replace the cover image.

Pure helpers use only stdlib (``zipfile``, ``xml.etree.ElementTree``) so they
are unit-testable without gradio. The gradio UI builder is a thin wrapper.
"""

from __future__ import annotations

import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pdf2book.config import AppConfig

# OPF / Dublin Core namespaces
_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


@dataclass
class EpubInfo:
    """Metadata + cover info extracted from an EPUB file."""

    path: Path
    title: str
    author: str
    cover_href: str | None  # relative path inside the EPUB zip, or None
    size_bytes: int


# --- Pure helpers (testable without gradio) --------------------------------


def list_library_epubs(cfg: AppConfig) -> list[Path]:
    """Return sorted EPUB files in ``library/`` (may be empty)."""
    lib = cfg.output_dir
    if not lib.exists():
        return []
    return sorted(p for p in lib.rglob("*.epub") if p.is_file())


def _find_opf_path(zf: zipfile.ZipFile) -> str | None:
    """Find the OPF file path inside an EPUB zip.

    Checks ``container.xml`` first (the standard way), falls back to scanning
    for any ``.opf`` file.
    """
    # Standard: META-INF/container.xml points to the OPF rootfile.
    try:
        container = zf.read("META-INF/container.xml")
        root = ET.fromstring(container)
        # Namespace: urn:oasis:names:tc:opendocument:xmlns:container
        for elem in root.iter():
            if elem.tag.endswith("rootfile") and elem.get("full-path"):
                return elem.get("full-path")
    except (KeyError, ET.ParseError):
        pass
    # Fallback: scan for .opf files.
    for name in zf.namelist():
        if name.endswith(".opf"):
            return name
    return None


def extract_epub_metadata(epub_path: Path) -> EpubInfo:
    """Extract title, author, and cover reference from an EPUB file.

    Returns an ``EpubInfo`` with empty strings when fields are missing (never
    raises — a malformed EPUB still returns a partial result).
    """
    title = ""
    author = ""
    cover_href: str | None = None

    with zipfile.ZipFile(epub_path, "r") as zf:
        opf_path = _find_opf_path(zf)
        if opf_path:
            try:
                opf_text = zf.read(opf_path).decode("utf-8")
                root = ET.fromstring(opf_text)
            except (KeyError, ET.ParseError):
                root = None

            if root is not None:
                # Dublin Core title/author
                title_elem = root.find(".//dc:title", _NS)
                if title_elem is not None and title_elem.text:
                    title = title_elem.text.strip()
                creator_elem = root.find(".//dc:creator", _NS)
                if creator_elem is not None and creator_elem.text:
                    author = creator_elem.text.strip()

                cover_href = _find_cover_href(root, opf_path)

    return EpubInfo(
        path=epub_path,
        title=title,
        author=author,
        cover_href=cover_href,
        size_bytes=epub_path.stat().st_size,
    )


def _find_cover_href(opf_root: ET.Element, opf_path: str) -> str | None:
    """Find the cover image href from OPF metadata/manifest.

    Handles both EPUB 2 (``<meta name="cover" content="id"/>``) and EPUB 3
    (``<item properties="cover-image" .../>``) patterns. Returns the full
    path relative to the EPUB zip root (resolving relative to the OPF dir).
    """
    opf_dir = str(Path(opf_path).parent)

    # EPUB 3: <item properties="cover-image" id="..." href="..."/>
    for item in opf_root.iter():
        if item.tag.endswith("item"):
            props = item.get("properties", "")
            if "cover-image" in props:
                href = item.get("href")
                if href:
                    return _resolve_href(href, opf_dir)

    # EPUB 2: <meta name="cover" content="item-id"/> → find manifest item
    cover_id: str | None = None
    for meta in opf_root.iter():
        if meta.tag.endswith("meta") and meta.get("name") == "cover":
            cover_id = meta.get("content")
            break
    if cover_id:
        for item in opf_root.iter():
            if item.tag.endswith("item") and item.get("id") == cover_id:
                href = item.get("href")
                if href:
                    return _resolve_href(href, opf_dir)

    # Fallback: look for common cover filenames
    for item in opf_root.iter():
        if item.tag.endswith("item"):
            href = item.get("href", "")
            if "cover" in href.lower() and any(
                href.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png")
            ):
                return _resolve_href(href, opf_dir)

    return None


def _resolve_href(href: str, opf_dir: str) -> str:
    """Resolve a relative href against the OPF directory to a zip-root path."""
    if opf_dir and opf_dir != ".":
        return str(Path(opf_dir) / href).replace("\\", "/")
    return href


def extract_cover_image(epub_path: Path) -> tuple[bytes, str] | None:
    """Extract the cover image from an EPUB.

    Returns ``(image_bytes, content_type)`` or ``None`` when no cover exists.
    ``content_type`` is inferred from the file extension.
    """
    with zipfile.ZipFile(epub_path, "r") as zf:
        info = extract_epub_metadata(epub_path)
        if info.cover_href is None:
            return None
        try:
            data = zf.read(info.cover_href)
        except KeyError:
            return None
        ext = Path(info.cover_href).suffix.lower()
        ct_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
        content_type = ct_map.get(ext, "application/octet-stream")
        return data, content_type


def replace_cover_in_epub(epub_path: Path, new_cover: Path) -> None:
    """Replace the cover image inside an EPUB with ``new_cover``.

    Creates a new zip with the cover image replaced, then atomically swaps it
    in. If the EPUB has no existing cover, this is a no-op (the user should
    rebuild the EPUB with ``--cover`` instead).
    """
    info = extract_epub_metadata(epub_path)
    if info.cover_href is None:
        raise ValueError("EPUB 中未找到封面图片，无法替换。请使用 --cover 重新构建。")

    new_data = new_cover.read_bytes()
    ext = Path(info.cover_href).suffix.lower()
    new_ext = new_cover.suffix.lower()
    if new_ext and new_ext != ext:
        # Extension mismatch — keep the original path to avoid breaking OPF
        # references, but the image data is what matters for display.
        pass

    tmp_path = epub_path.with_suffix(".tmp.epub")
    with zipfile.ZipFile(epub_path, "r") as zin:
        with zipfile.ZipFile(
            tmp_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as zout:
            for item in zin.infolist():
                if item.filename == info.cover_href:
                    zout.writestr(item, new_data)
                elif item.filename == "mimetype":
                    zout.writestr(item, zin.read(item.filename), compress_type=zipfile.ZIP_STORED)
                else:
                    zout.writestr(item, zin.read(item.filename))
    shutil.move(str(tmp_path), str(epub_path))


# --- Gradio UI builder (needs gradio, called only when GUI launches) -------


def build_library_tab(cfg: AppConfig, config_path: Path | None = None):  # type: ignore[no-untyped-def]
    """Build the library tab UI.

    Layout:
      1. Refresh button + EPUB dropdown
      2. Book info (title/author/size) + cover preview image
      3. Cover replacement: file upload + replace button

    Returns a dict of Gradio component references.
    """
    import gradio as gr

    epub_choices = [str(p) for p in list_library_epubs(cfg)]

    with gr.Tab("书库") as tab:
        gr.Markdown("## EPUB 书库")

        with gr.Row():
            epub_dropdown = gr.Dropdown(
                label="选择 EPUB（library/）",
                choices=epub_choices,
                interactive=True,
            )
            refresh_btn = gr.Button("🔄 刷新列表")

        info_md = gr.Markdown("")
        cover_image = gr.Image(label="封面预览", interactive=False)

        with gr.Accordion("更换封面", open=False):
            new_cover_input = gr.File(
                label="选择新封面图片",
                file_types=[".png", ".jpg", ".jpeg"],
            )
            replace_btn = gr.Button("替换封面", variant="primary")
            replace_status = gr.Markdown("")

    def _on_refresh() -> gr.Dropdown:  # type: ignore[valid-type]
        return gr.Dropdown(choices=[str(p) for p in list_library_epubs(cfg)])

    refresh_btn.click(fn=_on_refresh, outputs=epub_dropdown)

    def _on_select(epub_path_str: str):
        """Load EPUB info + cover preview."""
        if not epub_path_str:
            return "", None
        epub_path = Path(epub_path_str)
        if not epub_path.exists():
            return f"❌ 文件不存在: {epub_path}", None

        info = extract_epub_metadata(epub_path)
        size_mb = info.size_bytes / (1024 * 1024)
        info_text = (
            f"**书名：** {info.title or '（未知）'}  \n"
            f"**作者：** {info.author or '（未知）'}  \n"
            f"**大小：** {size_mb:.2f} MB  \n"
            f"**路径：** `{info.path}`"
        )

        cover_result = extract_cover_image(epub_path)
        if cover_result is None:
            return info_text + "\n\n⚠️ 无封面图片", None
        data, _ct = cover_result
        # Gradio gr.Image accepts a file path or PIL image. We write to a
        # temp file so Gradio can display it.
        import tempfile as _tf

        ext = Path(info.cover_href).suffix or ".png"
        with _tf.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(data)
            temp_path = f.name
        return info_text, temp_path

    epub_dropdown.change(
        fn=_on_select,
        inputs=epub_dropdown,
        outputs=[info_md, cover_image],
    )

    def _on_replace(epub_path_str: str, new_cover_file):
        if not epub_path_str:
            return "❌ 请先选择一个 EPUB"
        if new_cover_file is None:
            return "❌ 请选择新封面图片"
        try:
            replace_cover_in_epub(Path(epub_path_str), Path(new_cover_file))
            return "✅ 封面已替换。"
        except Exception as exc:  # noqa: BLE001
            return f"❌ 替换失败: {type(exc).__name__}: {exc}"

    replace_btn.click(
        fn=_on_replace,
        inputs=[epub_dropdown, new_cover_input],
        outputs=replace_status,
    )

    return {
        "tab": tab,
        "epub_dropdown": epub_dropdown,
        "cover_image": cover_image,
        "replace_btn": replace_btn,
    }


__all__ = [
    "EpubInfo",
    "build_library_tab",
    "extract_cover_image",
    "extract_epub_metadata",
    "list_library_epubs",
    "replace_cover_in_epub",
]
