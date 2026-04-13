"""Canonical font mapping: source fonts → bundled serif/sans/mono."""
from __future__ import annotations
from dataclasses import dataclass
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from typing import Literal

FontFamily = Literal["serif", "sans", "mono"]

# Canonical font paths (DejaVu is widely available on Linux)
FONT_PATHS = {
    "serif": {
        "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "italic": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
        "bold_italic": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf",
    },
    "sans": {
        "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "italic": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        "bold_italic": "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    },
    "mono": {
        "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "italic": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf",
        "bold_italic": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-BoldOblique.ttf",
    },
}

# ReportLab font names
FONT_NAMES = {
    ("serif", False, False): "DejaVuSerif",
    ("serif", True, False): "DejaVuSerif-Bold",
    ("serif", False, True): "DejaVuSerif-Italic",
    ("serif", True, True): "DejaVuSerif-BoldItalic",
    ("sans", False, False): "DejaVuSans",
    ("sans", True, False): "DejaVuSans-Bold",
    ("sans", False, True): "DejaVuSans-Oblique",
    ("sans", True, True): "DejaVuSans-BoldOblique",
    ("mono", False, False): "DejaVuSansMono",
    ("mono", True, False): "DejaVuSansMono-Bold",
    ("mono", False, True): "DejaVuSansMono-Oblique",
    ("mono", True, True): "DejaVuSansMono-BoldOblique",
}

_fonts_registered = False


def register_fonts() -> None:
    """Register all canonical fonts with ReportLab."""
    global _fonts_registered
    if _fonts_registered:
        return

    for family, styles in FONT_PATHS.items():
        for style, path in styles.items():
            name = FONT_NAMES[(family, "bold" in style, "italic" in style)]
            try:
                pdfmetrics.registerFont(TTFont(name, path))
            except Exception:
                pass  # Font may already be registered or path may not exist

    _fonts_registered = True


def classify_font_family(font_name: str) -> FontFamily:
    """Map source font name to canonical family."""
    name = font_name.lower()

    # Mono detection
    if any(m in name for m in ["mono", "courier", "consolas", "menlo", "code"]):
        return "mono"

    # Serif detection
    if any(s in name for s in ["times", "serif", "georgia", "garamond", "cambria", "palatino"]):
        return "serif"

    # Default to sans
    return "sans"


def get_font_name(family: FontFamily, bold: bool, italic: bool) -> str:
    """Get ReportLab font name for family + style."""
    return FONT_NAMES[(family, bold, italic)]


@dataclass
class FontSpec:
    """Resolved font specification."""
    family: FontFamily
    name: str  # ReportLab font name
    size: float
    bold: bool
    italic: bool


def resolve_font(source_font: str, size: float, bold: bool, italic: bool) -> FontSpec:
    """Resolve source font to canonical FontSpec."""
    family = classify_font_family(source_font)
    name = get_font_name(family, bold, italic)
    return FontSpec(family=family, name=name, size=size, bold=bold, italic=italic)
