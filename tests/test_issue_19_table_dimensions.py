"""Regression coverage for ticket #19: dimension glyphs in Table 1 cells."""

from collections import Counter
from pathlib import Path
import re
import subprocess

from pdf_oxide import PdfDocument, PipelineConfig, extract_pdf


SOURCE_PDF = Path(
    "/mnt/storage12tb/extractor_corpus/inbox/arxiv/1512.03385v1.pdf"
)
PAGE_INDEX = 4
EXPECTED_TABLE_1_DIMENSIONS = (
    "112×112",
    "56×56",
    "28×28",
    "14×14",
    "7×7",
)
DIMENSION_GLYPHS = "\x14\x15"


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def test_table_1_preserves_dimension_glyphs_shown_by_pdftotext() -> None:
    """The engine must retain Table 1's complete dimension-glyph stream."""
    assert SOURCE_PDF.is_file(), f"missing ticket fixture: {SOURCE_PDF}"

    oracle = subprocess.run(
        [
            "pdftotext",
            "-f",
            str(PAGE_INDEX + 1),
            "-l",
            str(PAGE_INDEX + 1),
            "-layout",
            str(SOURCE_PDF),
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    extracted = PdfDocument(str(SOURCE_PDF)).extract_text(PAGE_INDEX)

    compact_oracle = _compact(oracle)
    compact_extracted = _compact(extracted)
    for dimension in EXPECTED_TABLE_1_DIMENSIONS:
        assert dimension in compact_oracle, f"pdftotext lost fixture value {dimension}"
        assert dimension in compact_extracted, (
            f"pdf_oxide lost Table 1 dimension value {dimension}"
        )

    expected = Counter(char for char in oracle if char in DIMENSION_GLYPHS)
    actual = Counter(char for char in extracted if char in DIMENSION_GLYPHS)
    missing = expected - actual

    assert expected == Counter({"\x14": 8, "\x15": 8})
    assert not missing, (
        f"Table 1 is missing {sum(missing.values())} dimension glyphs: "
        f"{dict(missing)!r}; expected={dict(expected)!r}, actual={dict(actual)!r}"
    )

    result = extract_pdf(
        str(SOURCE_PDF),
        PipelineConfig(
            features=[],
            sync_to_arango=False,
            render_page_images=False,
        ),
    )
    table_1 = next(
        table
        for table in result.tables
        if table["page"] == PAGE_INDEX
        and table["rows"] == 29
        and table["cols"] == 10
    )
    cell_text = table_1["text"]
    compact_cell_text = _compact(cell_text)
    for dimension in EXPECTED_TABLE_1_DIMENSIONS:
        assert dimension in compact_cell_text, (
            f"pdf_oxide lost Table 1 cell value {dimension}"
        )
    assert Counter(char for char in cell_text if char in DIMENSION_GLYPHS) == expected
