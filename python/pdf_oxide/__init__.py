"""
PDF Oxide - The Complete PDF Toolkit

Extract, create, and edit PDFs with one library.
Rust core with Python bindings. Fast, safe, dependency-free.

# Extract. Create. Edit.

## Extract
- Text with reading order and layout analysis
- Images (JPEG, PNG, TIFF)
- Forms and annotations
- Convert to Markdown, HTML, PlainText

## Create
- Fluent API: `Pdf.create()`
- Tables, images, graphics
- Colors, gradients, patterns

## Edit
- Annotations (highlights, notes, stamps)
- Form fields (text, checkbox, radio)
- Round-trip: modify existing PDFs

# Quick Start

```python
from pdf_oxide import PdfDocument, Pdf

# Extract
doc = PdfDocument("input.pdf")
text = doc.to_plain_text(0)

# Create
pdf = Pdf.create()
pdf.add_page().text("Hello!", x=72, y=720, size=24)
pdf.save("output.pdf")
```

# License

Dual-licensed under MIT OR Apache-2.0.
"""

from .pdf_oxide import (
    VERSION,
    BlendMode,
    # Advanced Graphics
    Color,
    ExtGState,
    LinearGradient,
    LineCap,
    LineJoin,
    PatternPresets,
    # PDF Creation
    Pdf,
    PdfDocument,
    RadialGradient,
    # Extraction
    TextSpan,
    # Geometry (Phase 1.7)
    Rect,
    Point,
    # OCR (always available as stub if feature is off)
    OcrConfig,
    OcrEngine,
    # Office (always available as stub if feature is off)
    OfficeConverter,
    # Standalone functions
    map_framework_controls,
    merge_tables,
)

from .survey import survey_document


__all__ = [
    "PdfDocument",
    "VERSION",
    # PDF Creation
    "Pdf",
    # Advanced Graphics
    "Color",
    "BlendMode",
    "ExtGState",
    "LinearGradient",
    "RadialGradient",
    "LineCap",
    "LineJoin",
    "PatternPresets",
    # Extraction
    "TextSpan",
    # Geometry
    "Rect",
    "Point",
    # OCR
    "OcrEngine",
    "OcrConfig",
    # Office
    "OfficeConverter",
    # Standalone functions
    "map_framework_controls",
    "merge_tables",
    # Survey
    "survey_document",
]
__version__ = VERSION
