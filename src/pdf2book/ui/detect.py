"""Environment & dependency detection for the Web UI onboarding page.

Pure-stdlib (no gradio import) so it can be unit-tested without the optional
``gui`` extra. The onboarding page calls ``system_status(cfg)`` to decide
which setup steps to show the user.

Detection uses ``importlib.util.find_spec`` (does NOT actually import the
package) so checking heavy dependencies like ``paddleocr`` is fast and won't
load models. Version strings come from ``importlib.metadata``.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
from dataclasses import dataclass

from pdf2book.config import AppConfig

# Maps OCR backend name -> top-level import that must be importable.
# ``find_spec`` checks existence without loading the module, so paddleocr
# detection stays instant.
_OCR_BACKEND_IMPORTS: dict[str, tuple[str, str]] = {
    # backend: (module_name, install_hint)
    "paddle_pp": ("paddleocr", "pip install 'pdf2book[ocr]'"),
    "rapid_ocr": ("rapidocr_onnxruntime", "pip install 'pdf2book[rapid]'"),
    "paddle_vl": ("paddleocr_vl", "pip install paddleocr-vl  (需要 NVIDIA GPU)"),
    "cloud_ocr": ("httpx", "pip install 'pdf2book[cloud]'"),
}


@dataclass
class DependencyStatus:
    """Status of a single dependency/component.

    ``available`` is True only when the component is ready to use right now.
    ``detail`` carries a version string, install hint, or short error note.
    """

    name: str
    available: bool
    detail: str = ""


@dataclass
class SystemStatus:
    """Aggregated status of all components, drives the onboarding page."""

    gradio: DependencyStatus
    ocr: DependencyStatus
    pandoc: DependencyStatus
    ai_review: DependencyStatus

    @property
    def ready(self) -> bool:
        """True when the core conversion path works (gradio + ocr + pandoc).

        AI review is optional — a user can convert without it, so it's not
        part of ``ready``. Check ``ai_ready`` separately.
        """
        return self.gradio.available and self.ocr.available and self.pandoc.available

    @property
    def ai_ready(self) -> bool:
        return self.ai_review.available

    def all_ok(self) -> bool:
        """True when everything (including optional AI review) is configured."""
        return self.ready and self.ai_ready


def _is_installed(module_name: str) -> tuple[bool, str]:
    """Check whether ``module_name`` is importable WITHOUT importing it.

    Returns ``(True, version_string)`` when installed (version may be empty if
    metadata lookup fails), ``(False, "")`` when not found.
    """
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        return False, ""
    if spec is None:
        return False, ""
    try:
        version = importlib.metadata.version(module_name)
    except importlib.metadata.PackageNotFoundError:
        version = ""
    return True, version


def check_gradio() -> DependencyStatus:
    ok, detail = _is_installed("gradio")
    return DependencyStatus("Gradio (Web UI)", ok, detail)


def check_pandoc() -> DependencyStatus:
    ok, detail = _is_installed("pypandoc")
    return DependencyStatus("Pandoc (EPUB 构建)", ok, detail)


def check_ocr_backend(cfg: AppConfig) -> DependencyStatus:
    """Check whether the configured OCR backend's dependency is installed."""
    backend = cfg.ocr.backend
    module_name, hint = _OCR_BACKEND_IMPORTS.get(
        backend, ("", f"未知后端: {backend}")
    )
    display = f"OCR 引擎 ({backend})"
    if not module_name:
        return DependencyStatus(display, False, hint)
    ok, detail = _is_installed(module_name)
    if not ok and hint:
        detail = hint
    return DependencyStatus(display, ok, detail)


def check_ai_review(cfg: AppConfig) -> DependencyStatus:
    """Check whether AI review is enabled, has an API key, and httpx is installed."""
    if not cfg.ai_review.enabled:
        return DependencyStatus("AI 审查", False, "未启用（可在设置中开启）")
    if not cfg.ai_review.api_key:
        return DependencyStatus("AI 审查", False, "已启用但未配置 api_key")
    ok, _ = _is_installed("httpx")
    if not ok:
        return DependencyStatus(
            "AI 审查", False, "缺少 httpx: pip install 'pdf2book[cloud]'"
        )
    return DependencyStatus("AI 审查", True, f"model={cfg.ai_review.model}")


def system_status(cfg: AppConfig) -> SystemStatus:
    """Aggregate all component checks into a ``SystemStatus`` for onboarding."""
    return SystemStatus(
        gradio=check_gradio(),
        ocr=check_ocr_backend(cfg),
        pandoc=check_pandoc(),
        ai_review=check_ai_review(cfg),
    )


__all__ = [
    "DependencyStatus",
    "SystemStatus",
    "check_ai_review",
    "check_gradio",
    "check_ocr_backend",
    "check_pandoc",
    "system_status",
]
