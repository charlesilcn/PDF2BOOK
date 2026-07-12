"""Glass theme + CSS animations for the pdf2book Web UI.

Glassmorphism design: frosted-glass panels over a soft gradient background,
minimalist layout, and subtle animations (card fade-in/up, button hover,
smooth progress bars, tab transitions).

This module splits presentation into two layers so the CSS is testable
without gradio installed:

  - ``glass_css()`` — returns the CSS string (pure function, no gradio import)
  - ``build_theme()`` — lazily imports gradio and returns a ``gr.themes.Theme``
    with a matching color palette (only called when the GUI is launched)

The CSS is applied via ``gr.Blocks(css=glass_css())`` in ``app.py``.
"""

from __future__ import annotations


def glass_css() -> str:
    """Return the Glass-theme CSS string.

    Pure function (no gradio dependency) so it can be unit-tested. Contains:
      - Soft gradient body background
      - Frosted-glass panels (``backdrop-filter: blur``)
      - ``@keyframes fadeInUp`` for card entrance
      - Button hover transitions
      - Smooth tab/step transitions
      - Progress bar styling
    """
    return """
/* ===== Glass Theme — pdf2book Web UI ===== */

/* --- Body: soft gradient background --- */
.gradio-container, .gr-app {
    background: linear-gradient(135deg, #e0e7ff 0%, #f0f4ff 40%, #fdf2f8 100%) !important;
    min-height: 100vh;
}

/* --- Frosted-glass panels --- */
.gr-block, .gr-panel, .form,
.gradio-container .block,
.gradio-container .gr-box,
.gradio-container .gr-panel {
    background: rgba(255, 255, 255, 0.65) !important;
    backdrop-filter: blur(20px) saturate(1.2);
    -webkit-backdrop-filter: blur(20px) saturate(1.2);
    border: 1px solid rgba(255, 255, 255, 0.35) !important;
    border-radius: 16px !important;
    box-shadow: 0 8px 32px rgba(31, 38, 135, 0.12) !important;
    animation: fadeInUp 0.5s ease-out both;
}

/* --- Animations --- */
@keyframes fadeInUp {
    from {
        opacity: 0;
        transform: translateY(16px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

@keyframes fadeIn {
    from { opacity: 0; }
    to   { opacity: 1; }
}

/* Stagger card entrance for a cascading feel */
.gradio-container .block:nth-child(1) { animation-delay: 0.05s; }
.gradio-container .block:nth-child(2) { animation-delay: 0.12s; }
.gradio-container .block:nth-child(3) { animation-delay: 0.20s; }
.gradio-container .block:nth-child(4) { animation-delay: 0.28s; }

/* --- Buttons: smooth hover transition --- */
.gr-button, button {
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
    border-radius: 10px !important;
}
.gr-button:hover, button:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(79, 70, 229, 0.25) !important;
}
.gr-button:active, button:active {
    transform: translateY(0);
}

/* --- Tabs: smooth transition --- */
.tabitem, .tab-nav button {
    transition: all 0.3s ease !important;
}
.tab-nav button.selected {
    font-weight: 600;
}

/* --- Progress bar: smooth width transition --- */
.gr-progress-bar, .progress-bar-wrap > div {
    transition: width 0.4s cubic-bezier(0.4, 0, 0.2, 1) !important;
    border-radius: 999px !important;
}

/* --- Headings: clean typography --- */
h1, h2, h3 {
    color: #1e293b !important;
    font-weight: 600 !important;
    letter-spacing: -0.02em;
}

/* --- Status badges (✓/✗) --- */
.status-ok {
    color: #16a34a;
    font-weight: 600;
}
.status-fail {
    color: #dc2626;
    font-weight: 600;
}

/* --- Markdown preview area --- */
.gradio-container .prose {
    background: rgba(255, 255, 255, 0.5) !important;
    border-radius: 12px !important;
    padding: 16px !important;
}
"""


def build_theme():  # type: ignore[no-untyped-def]
    """Build and return a ``gr.themes.Theme`` matching the Glass palette.

    Lazily imports gradio (so this module loads without the gui extra). Called
    only by ``app.build_app()`` after gradio availability is confirmed.

    Color palette (indigo/slate glass):
      - primary:   indigo-600  (#4f46e5)
      - secondary: slate-400   (#94a3b8)
      - neutral:   slate-100   (#f1f5f9)  for panel backgrounds
    """
    import gradio as gr

    return gr.themes.Soft(
        primary_hue="indigo",
        secondary_hue="slate",
        neutral_hue="slate",
        font=gr.themes.GoogleFont("Inter"),
        font_mono=gr.themes.GoogleFont("JetBrains Mono"),
    ).set(
        body_background_fill="linear-gradient(135deg, #e0e7ff 0%, #f0f4ff 40%, #fdf2f8 100%)",
        block_background_fill="rgba(255, 255, 255, 0.65)",
        block_border_width="1px",
        block_border_color="rgba(255, 255, 255, 0.35)",
        block_shadow="0 8px 32px rgba(31, 38, 135, 0.12)",
        block_radius="16px",
        button_primary_background_fill="#4f46e5",
        button_primary_background_fill_hover="#4338ca",
        button_primary_text_color="white",
    )


__all__ = ["build_theme", "glass_css"]
