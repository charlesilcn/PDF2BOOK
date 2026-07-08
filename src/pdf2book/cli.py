"""CLI entry point for pdf2book.

Standard three-folder layout (zero-config UX):

  ``inbox/``    — drop scanned PDFs here
  ``library/``  — generated EPUBs (named after the source PDF stem)
  ``workspace/``— per-book intermediate artifacts (``workspace/{stem}/``)

Default behavior: running ``pdf2book`` with no arguments scans ``inbox/``
and converts every PDF to ``library/{stem}.epub``.

Four subcommands for explicit control:

  ``ocr`` — Stage 1: PDF -> OCR -> postprocess -> workspace/{stem}/book.md + meta.md
            Use this to generate a previewable Markdown; edit it, then run
            ``epub`` to build the final EPUB.

  ``epub`` — Stage 2: Markdown + meta.md -> .epub via Pandoc
             Reads metadata from a sibling ``meta.md`` (or ``--meta``).

  ``convert`` — One-shot: ``ocr`` + ``epub`` chained. With no PDF argument,
                falls back to the default ``inbox/`` -> ``library/`` behavior.
                With a PDF but no ``-o``, defaults to ``library/{stem}.epub``.

  ``batch`` — Batch convert a directory of PDFs to EPUBs in parallel.
              Defaults to ``inbox/`` -> ``library/``.

AI review auto-enable: when ``config.yaml`` has ``ai_review.api_key`` set
and ``enabled`` is not explicitly ``false``, AI review turns on automatically.
Use ``--no-ai-review`` to force it off (e.g. the Skill path, which relies on
the agent's own reasoning instead of an external LLM API).

Usage::

    pdf2book                                # inbox/ -> library/ (zero-config)
    pdf2book ocr inbox/book.pdf [--resume] [--config config.yaml] [-v]
    pdf2book epub workspace/book/book.md -o library/book.epub [-v]
    pdf2book convert inbox/book.pdf         # -> library/book.epub
    pdf2book batch                          # inbox/ -> library/
"""

from __future__ import annotations

from pathlib import Path

import typer

from pdf2book.batch import BatchProcessor
from pdf2book.config import AppConfig, isolate_work_dir
from pdf2book.pipeline import ConversionPipeline
from pdf2book.utils.logger import setup_logger

app = typer.Typer(
    name="pdf2book",
    help="Convert scanned PDF books to Kindle-friendly EPUB via OCR.",
    no_args_is_help=False,
    invoke_without_command=True,
)

_NO_AI_REVIEW_HELP = (
    "强制关闭 AI 审查（即使 config.yaml 配置了 api_key）。"
    "Skill 路径用此标志确保不触发外部 LLM 调用。"
)
_BACKEND_HELP = "OCR 后端: paddle_pp | rapid_ocr | paddle_vl | cloud_ocr"


def _load_config_or_default(config: Path | None) -> AppConfig:
    """Load config from explicit path, else auto-discover ``config.yaml`` in cwd.

    Falls back to ``AppConfig.default()`` when neither is found. This lets
    ``pdf2book`` (no args) and ``pdf2book convert x.pdf`` pick up user
    configuration (AI review, OCR backend, etc.) without ``--config``.
    """
    if config is not None:
        return AppConfig.load(config)
    cwd_config = Path("config.yaml")
    if cwd_config.exists():
        return AppConfig.load(cwd_config)
    return AppConfig.default()


def ensure_standard_dirs(cfg: AppConfig) -> None:
    """Create the standard inbox/library/workspace folders (idempotent)."""
    cfg.input_dir.mkdir(parents=True, exist_ok=True)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    cfg.work_dir.mkdir(parents=True, exist_ok=True)


def _run_default() -> None:
    """Default behavior: scan ``inbox/`` and convert all PDFs to ``library/``.

    Reuses ``BatchProcessor`` so each PDF gets an isolated
    ``workspace/{stem}/`` subdirectory. Reads ``config.yaml`` from cwd when
    present (AI review, OCR backend, workers, etc.).
    """
    cfg = _load_config_or_default(None)
    ensure_standard_dirs(cfg)
    log = setup_logger("INFO")

    pdf_paths = sorted(p for p in cfg.input_dir.rglob("*.pdf") if p.is_file())
    if not pdf_paths:
        typer.echo(
            f"inbox/ 为空。请将 PDF 文件放入 {cfg.input_dir}/ 后重新运行。"
        )
        raise typer.Exit(code=0)

    typer.echo(f"发现 {len(pdf_paths)} 个 PDF，开始转换...")
    processor = BatchProcessor(cfg, max_workers=cfg.max_workers, log=log)
    succeeded = processor.run(pdf_paths, cfg.output_dir)

    typer.echo(
        f"完成：{len(succeeded)}/{len(pdf_paths)} 个 EPUB 已生成到 {cfg.output_dir}/"
    )
    if len(succeeded) < len(pdf_paths):
        raise typer.Exit(code=1)


@app.callback()
def _main(ctx: typer.Context) -> None:
    """pdf2book — PDF to Kindle EPUB converter.

    Running ``pdf2book`` with no subcommand scans ``inbox/`` and converts
    every PDF to ``library/{stem}.epub`` (zero-config mode).
    """  # noqa: D401
    if ctx.invoked_subcommand is None:
        _run_default()


@app.command()
def ocr(
    pdf: Path = typer.Argument(..., help="Input scanned PDF path", exists=True),
    resume: bool = typer.Option(False, "--resume", help="Resume from cache (skip OCR'd pages)"),
    config: Path | None = typer.Option(None, "--config", help="Config YAML path"),
    backend: str = typer.Option(None, "--backend", help=_BACKEND_HELP),
    no_ai_review: bool = typer.Option(False, "--no-ai-review", help=_NO_AI_REVIEW_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG logging"),
) -> None:
    """Stage 1: PDF -> OCR -> Markdown (previewable).

    Generates ``workspace/{stem}/book.md`` and ``workspace/{stem}/meta.md``.
    Preview or edit the Markdown, then run ``pdf2book epub`` to build the EPUB.

    AI review auto-enables when ``config.yaml`` has ``api_key`` set; pass
    ``--no-ai-review`` to force it off.
    """
    cfg = _load_config_or_default(config)
    if backend is not None:
        cfg.ocr.backend = backend
    if no_ai_review:
        cfg.ai_review.enabled = False
    log = setup_logger("DEBUG" if verbose else "INFO")

    # Isolate work_dir per book BEFORE pipeline construction (PostProcessor
    # captures work_dir at init time).
    isolate_work_dir(cfg, pdf.stem)
    ensure_standard_dirs(cfg)

    pipeline = ConversionPipeline(cfg, log)
    book_md = pipeline.run_to_markdown(pdf, resume=resume)
    typer.echo(f"Markdown written: {book_md}")
    typer.echo(f"Metadata written: {book_md.parent / 'meta.md'}")
    typer.echo("Preview the Markdown, then run:")
    typer.echo(f"  pdf2book epub {book_md} -o library/{pdf.stem}.epub")


@app.command()
def epub(
    markdown: Path = typer.Argument(..., help="Markdown path (from `ocr` stage)", exists=True),
    output: Path = typer.Option(..., "-o", "--output", help="Output EPUB path"),
    meta: Path | None = typer.Option(None, "--meta", help="Metadata YAML (default: meta.md)"),
    cover: Path | None = typer.Option(None, "--cover", help="Cover image path", exists=True),
    css: Path | None = typer.Option(None, "--css", help="CSS stylesheet path", exists=True),
    config: Path | None = typer.Option(None, "--config", help="Config YAML path"),
    no_ai_review: bool = typer.Option(False, "--no-ai-review", help=_NO_AI_REVIEW_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG logging"),
) -> None:
    """Stage 2: Markdown -> EPUB via Pandoc.

    Reads metadata from a sibling ``meta.md`` (generated by ``ocr``) or from
    ``--meta``. Use ``--cover`` and ``--css`` to customize the EPUB.

    When AI review is enabled and the Markdown still carries
    ``>[low-confidence]`` markers (OCR stage ran without AI review), this
    stage supplements AI review before building the EPUB. Idempotent: if
    markers are already gone, no review runs.
    """
    cfg = _load_config_or_default(config)
    if no_ai_review:
        cfg.ai_review.enabled = False
    log = setup_logger("DEBUG" if verbose else "INFO")

    output.parent.mkdir(parents=True, exist_ok=True)

    pipeline = ConversionPipeline(cfg, log)
    out = pipeline.build_epub(markdown, output, meta_path=meta, cover=cover, css=css)
    typer.echo(f"Done: {out}")


@app.command()
def convert(
    pdf: Path = typer.Argument(
        None, help="Input scanned PDF path (omit to process inbox/)", exists=True
    ),
    output: Path = typer.Option(
        None, "-o", "--output", help="Output EPUB path (default: library/{stem}.epub)"
    ),
    resume: bool = typer.Option(False, "--resume", help="Resume from cache (skip OCR'd pages)"),
    config: Path | None = typer.Option(None, "--config", help="Config YAML path"),
    backend: str = typer.Option(None, "--backend", help=_BACKEND_HELP),
    cover: Path | None = typer.Option(None, "--cover", help="Cover image path", exists=True),
    no_ai_review: bool = typer.Option(False, "--no-ai-review", help=_NO_AI_REVIEW_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG logging"),
) -> None:
    """One-shot: PDF -> OCR -> Markdown -> EPUB (``ocr`` + ``epub`` chained).

    With no PDF argument, falls back to the default ``inbox/`` -> ``library/``
    behavior. With a PDF but no ``-o``, defaults output to
    ``library/{stem}.epub``.

    AI review auto-enables when ``config.yaml`` has ``api_key`` set; pass
    ``--no-ai-review`` to force it off.
    """
    cfg = _load_config_or_default(config)
    if backend is not None:
        cfg.ocr.backend = backend
    if no_ai_review:
        cfg.ai_review.enabled = False
    log = setup_logger("DEBUG" if verbose else "INFO")

    # No PDF argument: process inbox/ -> library/ (default behavior).
    if pdf is None:
        _run_default()
        return

    # Single PDF mode: default output to library/{stem}.epub.
    if output is None:
        output = cfg.output_dir / f"{pdf.stem.rstrip()}.epub"

    # Isolate work_dir per book BEFORE pipeline construction.
    isolate_work_dir(cfg, pdf.stem)
    ensure_standard_dirs(cfg)

    output.parent.mkdir(parents=True, exist_ok=True)

    pipeline = ConversionPipeline(cfg, log)
    out = pipeline.run(pdf, output, resume=resume, cover=cover)
    typer.echo(f"Done: {out}")


@app.command()
def batch(
    input_dir: Path = typer.Argument(
        Path("inbox"), help="Directory containing PDFs (default: inbox/)"
    ),
    output: Path = typer.Option(
        Path("library"), "-o", "--output", help="Output directory for EPUBs (default: library/)"
    ),
    workers: int = typer.Option(
        1, "--workers", help="Parallel worker processes (memory scales linearly)"
    ),
    resume: bool = typer.Option(False, "--resume", help="Resume from cache (skip OCR'd pages)"),
    config: Path | None = typer.Option(None, "--config", help="Config YAML path"),
    backend: str = typer.Option(None, "--backend", help=_BACKEND_HELP),
    no_ai_review: bool = typer.Option(False, "--no-ai-review", help=_NO_AI_REVIEW_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG logging"),
) -> None:
    """Batch convert all PDFs in a directory to EPUBs in parallel.

    Defaults to ``inbox/`` -> ``library/``. Each PDF gets an isolated
    ``workspace/{stem}/`` subdirectory and SQLite cache so concurrent workers
    don't contend. Output files are named ``{pdf_stem}.epub``.

    Memory note: each worker loads its own OCR model. RapidOCR ~50MB/worker
    (high concurrency OK); PaddlePP ~1.5GB/worker (recommend --workers 1-2).
    """
    cfg = _load_config_or_default(config)
    if backend is not None:
        cfg.ocr.backend = backend
    if no_ai_review:
        cfg.ai_review.enabled = False
    log = setup_logger("DEBUG" if verbose else "INFO")

    # Create input_dir if it doesn't exist (e.g. default inbox/ on first run).
    input_dir.mkdir(parents=True, exist_ok=True)

    pdf_paths = sorted(p for p in input_dir.rglob("*.pdf") if p.is_file())
    if not pdf_paths:
        typer.echo(f"No PDFs found in {input_dir}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Found {len(pdf_paths)} PDF(s); converting with {workers} worker(s)...")
    processor = BatchProcessor(cfg, max_workers=workers, log=log)
    succeeded = processor.run(pdf_paths, output, resume=resume)

    typer.echo(f"Done: {len(succeeded)}/{len(pdf_paths)} EPUB(s) generated in {output}")
    if len(succeeded) < len(pdf_paths):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
