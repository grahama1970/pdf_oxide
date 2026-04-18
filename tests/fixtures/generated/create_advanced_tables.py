"""Generate advanced table test fixtures using the TablePreset system.

Uses the reportlab_table_presets library for proper table generation.
ONE TABLE PER PAGE for clean extraction testing.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "python" / "pdf_oxide"))

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, PageBreak, Paragraph, TableStyle
from reportlab.lib.styles import getSampleStyleSheet

from reportlab_table_presets.table_presets import (
    TablePreset,
    TableSpec,
    build_table,
    add_section_row,
    add_total_row,
    DEFAULT_PRESETS,
)


def create_advanced_table_fixtures(output_path: str):
    """Create PDF with advanced table test cases using presets."""
    doc = SimpleDocTemplate(output_path, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    # =========================================================================
    # TEST 1: Data Grid - basic report table with zebra striping
    # =========================================================================
    elements.append(Paragraph("Test 1: Data Grid Preset", styles["Heading2"]))
    elements.append(Spacer(1, 0.2*inch))

    spec = TableSpec(
        headers=["ID", "Product", "Category", "Price", "Stock"],
        rows=[
            ["001", "Widget A", "Hardware", 19.99, 150],
            ["002", "Widget B", "Hardware", 24.99, 75],
            ["003", "Gadget X", "Electronics", 149.99, 30],
            ["004", "Gadget Y", "Electronics", 199.99, 12],
            ["005", "Service Z", "Services", 99.00, None],
        ],
        col_widths=[0.6*inch, 1.2*inch, 1.0*inch, 0.8*inch, 0.8*inch],
    )
    elements.append(build_table(spec, preset="data_grid"))
    elements.append(PageBreak())

    # =========================================================================
    # TEST 2: Requirements Matrix - dense wrapped text
    # =========================================================================
    elements.append(Paragraph("Test 2: Requirements Matrix Preset", styles["Heading2"]))
    elements.append(Spacer(1, 0.2*inch))

    spec = TableSpec(
        headers=["ID", "Subsystem", "Requirement", "Status", "Priority"],
        rows=[
            ["REQ-001", "Authentication", "The system shall enforce MFA for all privileged users and log unsuccessful attempts to the security audit trail.", "Implemented", "High"],
            ["REQ-002", "Audit Pipeline", "The ingestion pipeline shall preserve source line provenance and support delta analysis across document revisions.", "In Progress", "High"],
            ["REQ-003", "Evidence Parser", "Vendor-submitted PDFs shall be parsed into traceable requirement records with table-aware extraction.", "Planned", "Medium"],
            ["REQ-004", "Compliance Export", "The system shall export OSCAL-formatted assessment results with full traceability to source artifacts.", "Planned", "Medium"],
        ],
        col_widths=[0.8*inch, 1.0*inch, 3.2*inch, 0.9*inch, 0.7*inch],
    )
    elements.append(build_table(spec, preset="requirements_matrix"))
    elements.append(PageBreak())

    # =========================================================================
    # TEST 3: Comparison Matrix - center aligned, multi-row header concept
    # =========================================================================
    elements.append(Paragraph("Test 3: Comparison Matrix Preset", styles["Heading2"]))
    elements.append(Spacer(1, 0.2*inch))

    spec = TableSpec(
        headers=["Feature", "Tool A", "Tool B", "Tool C", "Our Solution"],
        rows=[
            ["Table Extraction", "Basic", "Good", "Limited", "Advanced"],
            ["PDF Parsing", "Yes", "Yes", "No", "Yes"],
            ["Stream Mode", "No", "Yes", "No", "Yes"],
            ["Lattice Mode", "Yes", "Yes", "Yes", "Yes"],
            ["Span Detection", "No", "Partial", "No", "Full"],
            ["Performance", "Slow", "Medium", "Fast", "Fast"],
        ],
        col_widths=[1.2*inch, 0.9*inch, 0.9*inch, 0.9*inch, 1.0*inch],
    )
    elements.append(build_table(spec, preset="comparison_matrix"))
    elements.append(PageBreak())

    # =========================================================================
    # TEST 4: Ledger - with section rows and total
    # =========================================================================
    elements.append(Paragraph("Test 4: Ledger Preset (Sections + Totals)", styles["Heading2"]))
    elements.append(Spacer(1, 0.2*inch))

    ledger_rows: list[list] = []
    section_rows: list[int] = []
    spans: list[tuple[tuple[int, int], tuple[int, int]]] = []

    # Section 1
    idx, span = add_section_row(ledger_rows, "Phase 1 - Infrastructure", 4)
    section_rows.append(idx)
    spans.append(span)
    ledger_rows.extend([
        ["Server setup", 40, 150, 6000],
        ["Network config", 24, 150, 3600],
        ["Security hardening", 32, 175, 5600],
    ])

    # Section 2
    idx, span = add_section_row(ledger_rows, "Phase 2 - Development", 4)
    section_rows.append(idx)
    spans.append(span)
    ledger_rows.extend([
        ["API development", 120, 165, 19800],
        ["Frontend build", 80, 155, 12400],
        ["Testing", 60, 145, 8700],
    ])

    # Total
    total_idx = add_total_row(ledger_rows, "Project Total", [356, "", 56100])

    spec = TableSpec(
        headers=["Task", "Hours", "Rate", "Cost"],
        rows=ledger_rows,
        col_widths=[2.8*inch, 0.9*inch, 0.9*inch, 1.0*inch],
        spans=spans,
        section_rows=section_rows,
        total_rows=[total_idx],
    )

    def money_fmt(v):
        if v in (None, ""):
            return ""
        if isinstance(v, (int, float)):
            return f"${v:,.0f}"
        return str(v)

    elements.append(build_table(spec, preset="ledger", formatter=money_fmt))
    elements.append(PageBreak())

    # =========================================================================
    # TEST 5: Sectioned Grid - grouped data
    # =========================================================================
    elements.append(Paragraph("Test 5: Sectioned Grid Preset", styles["Heading2"]))
    elements.append(Spacer(1, 0.2*inch))

    grid_rows: list[list] = []
    section_rows2: list[int] = []
    spans2: list[tuple[tuple[int, int], tuple[int, int]]] = []

    idx, span = add_section_row(grid_rows, "Engineering Department", 4)
    section_rows2.append(idx)
    spans2.append(span)
    grid_rows.extend([
        ["Alice", "Senior Dev", "alice@co.com", "$120,000"],
        ["Bob", "Developer", "bob@co.com", "$95,000"],
        ["Carol", "Junior Dev", "carol@co.com", "$75,000"],
    ])

    idx, span = add_section_row(grid_rows, "Sales Department", 4)
    section_rows2.append(idx)
    spans2.append(span)
    grid_rows.extend([
        ["Dave", "Sales Manager", "dave@co.com", "$110,000"],
        ["Eve", "Sales Rep", "eve@co.com", "$65,000"],
    ])

    spec = TableSpec(
        headers=["Name", "Role", "Email", "Salary"],
        rows=grid_rows,
        col_widths=[1.2*inch, 1.2*inch, 1.8*inch, 1.0*inch],
        spans=spans2,
        section_rows=section_rows2,
    )
    elements.append(build_table(spec, preset="sectioned_grid"))
    elements.append(PageBreak())

    # =========================================================================
    # TEST 6: Key-Value Block - form-like layout
    # =========================================================================
    elements.append(Paragraph("Test 6: Key-Value Block Preset", styles["Heading2"]))
    elements.append(Spacer(1, 0.2*inch))

    spec = TableSpec(
        headers=["Field", "Value"],
        rows=[
            ["Document ID", "DOC-2026-0412-001"],
            ["Title", "Advanced Table Extraction Test Fixtures"],
            ["Author", "pdf_oxide Development Team"],
            ["Created", "2026-04-12"],
            ["Version", "1.0.0"],
            ["Classification", "Internal"],
        ],
        col_widths=[1.5*inch, 3.5*inch],
    )
    elements.append(build_table(spec, preset="key_value_block"))
    elements.append(PageBreak())

    # =========================================================================
    # TEST 7: Dense Financial Table (custom preset)
    # =========================================================================
    elements.append(Paragraph("Test 7: Dense Financial Table", styles["Heading2"]))
    elements.append(Spacer(1, 0.2*inch))

    financial_preset = TablePreset(
        name="financial_dense",
        font_size=7,
        header_font_size=7,
        leading=9,
        header_leading=9,
        cell_padding_x=3,
        cell_padding_y=2,
        header_background=colors.HexColor("#1B4D3E"),
        header_text_color=colors.white,
        row_backgrounds=(colors.HexColor("#F5F5F5"), colors.white),
        column_alignments={1: "RIGHT", 2: "RIGHT", 3: "RIGHT", 4: "RIGHT", 5: "RIGHT", 6: "RIGHT"},
    )

    spec = TableSpec(
        headers=["Metric", "2020", "2021", "2022", "2023", "2024", "2025"],
        rows=[
            ["Revenue", 1200, 1450, 1680, 1920, 2150, 2400],
            ["COGS", 720, 870, 1008, 1152, 1290, 1440],
            ["Gross Profit", 480, 580, 672, 768, 860, 960],
            ["OpEx", 240, 290, 336, 384, 430, 480],
            ["EBITDA", 240, 290, 336, 384, 430, 480],
            ["D&A", 48, 58, 67, 77, 86, 96],
            ["EBIT", 192, 232, 269, 307, 344, 384],
            ["Interest", 24, 29, 34, 38, 43, 48],
            ["EBT", 168, 203, 235, 269, 301, 336],
            ["Tax", 42, 51, 59, 67, 75, 84],
            ["Net Income", 126, 152, 176, 202, 226, 252],
        ],
        col_widths=[1.1*inch] + [0.7*inch]*6,
    )

    def thousands(v):
        if v in (None, ""):
            return ""
        if isinstance(v, (int, float)):
            return f"${v:,.0f}k"
        return str(v)

    elements.append(build_table(spec, preset=financial_preset, formatter=thousands))
    elements.append(PageBreak())

    # =========================================================================
    # TEST 8: Wide Table (12 columns)
    # =========================================================================
    elements.append(Paragraph("Test 8: Wide Table (12 Months)", styles["Heading2"]))
    elements.append(Spacer(1, 0.2*inch))

    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    spec = TableSpec(
        headers=["Metric"] + months,
        rows=[
            ["Sales"] + [100 + i*10 for i in range(12)],
            ["Costs"] + [60 + i*6 for i in range(12)],
            ["Profit"] + [40 + i*4 for i in range(12)],
        ],
        col_widths=[0.7*inch] + [0.42*inch]*12,
    )

    wide_preset = TablePreset(
        name="wide_monthly",
        font_size=7,
        header_font_size=7,
        leading=9,
        header_leading=9,
        cell_padding_x=2,
        cell_padding_y=2,
        header_background=colors.HexColor("#8B0000"),
        header_text_color=colors.white,
        default_alignment="CENTER",
    )
    elements.append(build_table(spec, preset=wide_preset))
    elements.append(PageBreak())

    # =========================================================================
    # TEST 9: Empty Cells and Sparse Data
    # =========================================================================
    elements.append(Paragraph("Test 9: Empty Cells / Sparse Data", styles["Heading2"]))
    elements.append(Spacer(1, 0.2*inch))

    spec = TableSpec(
        headers=["ID", "Name", "Value A", "Value B", "Notes"],
        rows=[
            ["001", "Item 1", 10, None, None],
            ["002", None, 20, 30, "Has both"],
            ["003", "Item 3", None, 40, "Only B"],
            [None, "Orphan", None, None, "No ID"],
            ["005", "Item 5", 50, 60, None],
        ],
        col_widths=[0.6*inch, 1.0*inch, 0.8*inch, 0.8*inch, 1.2*inch],
    )
    elements.append(build_table(spec, preset="data_grid"))
    elements.append(PageBreak())

    # =========================================================================
    # TEST 10: Special Characters
    # =========================================================================
    elements.append(Paragraph("Test 10: Special Characters", styles["Heading2"]))
    elements.append(Spacer(1, 0.2*inch))

    spec = TableSpec(
        headers=["Symbol", "Name", "Example"],
        rows=[
            ["$", "Dollar", "$1,234.56"],
            ["%", "Percent", "45.67%"],
            ["&", "Ampersand", "A & B"],
            ["<>", "Angle Brackets", "<value>"],
            ['"', "Quote", '"quoted text"'],
            ["'", "Apostrophe", "it's working"],
        ],
        col_widths=[0.8*inch, 1.2*inch, 1.5*inch],
    )
    elements.append(build_table(spec, preset="data_grid"))
    elements.append(PageBreak())

    # =========================================================================
    # TEST 11: Multi-Page Table (LongTable with repeatRows)
    # =========================================================================
    elements.append(Paragraph("Test 11: Multi-Page Table (50 rows)", styles["Heading2"]))
    elements.append(Spacer(1, 0.2*inch))

    rows = [[i, f"Item {i}", f"Description of item number {i}", i*10] for i in range(1, 51)]
    spec = TableSpec(
        headers=["ID", "Name", "Description", "Value"],
        rows=rows,
        col_widths=[0.5*inch, 1.0*inch, 3.0*inch, 0.8*inch],
        repeat_rows=1,
    )
    elements.append(build_table(spec, preset="data_grid"))

    # Build PDF
    doc.build(elements)
    print(f"Created: {output_path}")
    return output_path


if __name__ == "__main__":
    output = Path(__file__).parent / "advanced_tables.pdf"
    create_advanced_table_fixtures(str(output))
