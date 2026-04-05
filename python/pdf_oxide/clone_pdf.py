"""PDF Cloner: Preset discovery and validation via synthetic fixtures."""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Dict, List, Optional

import typer
from pydantic import BaseModel, Field, ValidationError
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Table, TableStyle
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

import pdf_oxide
from pdf_oxide.survey import survey_document

app = typer.Typer(name="clone_pdf", help="PDF Cloner — profile, sample, clone, score")


class ElementType(str, Enum):
    header = "header"
    body = "body"
    table = "table"
    figure = "figure"
    caption = "caption"
    list_item = "list"
    footnote = "footnote"
    equation = "equation"
    page_number = "page_number"
    running_header = "running_header"
    running_footer = "running_footer"


class IRElement(BaseModel):
    id: str
    type: ElementType
    bbox: list[float]
    text: str
    header_level: int = 0
    page: int
    reading_order: int
    font_size: float = 12.0
    is_bold: bool = False
    numbering: Optional[str] = None


class TableCell(BaseModel):
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    text: str
    role: str = "data"


class TableContinuation(BaseModel):
    is_continued: bool = False
    continued_from: Optional[str] = None


class IRTable(BaseModel):
    table_id: str
    page_start: int
    page_end: int
    bbox_per_page: dict[int, list[float]] = Field(default_factory=dict)
    caption: Optional[str] = None
    n_header_rows: int = 1
    n_rows: int
    n_cols: int
    cells: list[TableCell]
    continuation: TableContinuation = Field(default_factory=TableContinuation)
    style: str = "ruled"


class IRRelationship(BaseModel):
    type: str
    source: str
    target: str


class WindowIR(BaseModel):
    window_id: str
    source_pages: list[int]
    source_pdf: str
    family_id: str
    elements: list[IRElement]
    tables: list[IRTable]
    relationships: list[IRRelationship] = Field(default_factory=list)
    reading_order: list[str] = Field(default_factory=list)


def validate_ir(ir_dict: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []

    try:
        ir = WindowIR(**ir_dict)
    except ValidationError as exc:
        return False, [str(err) for err in exc.errors()]

    element_ids = [el.id for el in ir.elements]
    table_ids = [tbl.table_id for tbl in ir.tables]

    if len(set(element_ids)) != len(element_ids):
        errors.append("duplicate element IDs found")

    valid_ids = set(element_ids) | set(table_ids)

    for ref_id in ir.reading_order:
        if ref_id not in set(element_ids):
            errors.append(f"reading_order references unknown element id: {ref_id}")

    for rel in ir.relationships:
        if rel.source not in valid_ids:
            errors.append(f"relationship source references unknown id: {rel.source}")
        if rel.target not in valid_ids:
            errors.append(f"relationship target references unknown id: {rel.target}")

    source_page_set = set(ir.source_pages)
    for el in ir.elements:
        if el.page not in source_page_set:
            errors.append(f"element {el.id} has page {el.page} outside source_pages")

    for tbl in ir.tables:
        if tbl.page_start not in source_page_set:
            errors.append(f"table {tbl.table_id} page_start {tbl.page_start} outside source_pages")
        if tbl.page_end not in source_page_set:
            errors.append(f"table {tbl.table_id} page_end {tbl.page_end} outside source_pages")

    return len(errors) == 0, errors


def _build_page_signatures(doc, survey: Dict[str, Any]) -> List[Dict[str, Any]]:
    page_details = survey.get("page_details", []) or []
    table_pages = set(survey.get("table_pages", []) or [])
    figure_pages = set(survey.get("figure_pages", []) or [])
    equation_pages = set(survey.get("equation_pages", []) or [])

    signatures: List[Dict[str, Any]] = []
    for idx, detail in enumerate(page_details):
        page_num = int(detail.get("page", idx))
        signatures.append(
            {
                "page_num": page_num,
                "char_count": int(detail.get("char_count", 0) or 0),
                "has_images": bool(detail.get("has_images", False)),
                "is_blank": bool(detail.get("is_blank", False)),
                "table_candidate": page_num in table_pages,
                "figure_candidate": page_num in figure_pages,
                "equation_candidate": page_num in equation_pages,
            }
        )
    return signatures


def profile_for_cloning(pdf_path: str) -> Dict[str, Any]:
    doc = pdf_oxide.PdfDocument(pdf_path)
    survey = survey_document(doc, enrich_profile=True)
    toc = doc.get_toc() or []
    _ = doc.get_section_map()

    page_count = int(survey.get("page_count", 0) or 0)

    return {
        "doc_id": hashlib.md5(pdf_path.encode("utf-8")).hexdigest(),
        "path": pdf_path,
        "page_count": page_count,
        "domain": survey.get("domain", "general"),
        "complexity_score": survey.get("complexity_score", 1),
        "layout_mode": "multi_column" if int(survey.get("columns", 1) or 1) > 1 else "single_column",
        "has_toc": bool(survey.get("has_toc", False)),
        "toc_entry_count": int(survey.get("toc_entry_count", 0) or 0),
        "toc_pages": [e.get("page") for e in toc if toc] or [],
        "lof_entries": [e for e in toc if isinstance(e, dict) and e.get("entry_type") == "Figure"],
        "lot_entries": [e for e in toc if isinstance(e, dict) and e.get("entry_type") == "Table"],
        "has_tables": bool(survey.get("has_tables", False)),
        "table_density": len(survey.get("table_pages", []) or []) / max(page_count, 1),
        "has_figures": bool(survey.get("has_figures", False)),
        "figure_density": len(survey.get("figure_pages", []) or []) / max(page_count, 1),
        "has_equations": bool(survey.get("has_equations", False)),
        "has_engineering": survey.get("domain") in ("engineering", "defense"),
        "section_count": int(survey.get("section_count", 0) or 0),
        "section_style": survey.get("section_style"),
        "is_scanned": bool(survey.get("is_scanned", False)),
        "page_signatures": _build_page_signatures(doc, survey),
    }


def assign_family(signature: dict) -> dict:
    domain = signature.get("domain")
    table_density = float(signature.get("table_density", 0.0) or 0.0)
    section_style = signature.get("section_style")
    has_engineering = bool(signature.get("has_engineering", False))
    layout_mode = signature.get("layout_mode")
    has_equations = bool(signature.get("has_equations", False))
    has_toc = bool(signature.get("has_toc", False))
    section_count = int(signature.get("section_count", 0) or 0)
    is_scanned = bool(signature.get("is_scanned", False))
    figure_density = float(signature.get("figure_density", 0.0) or 0.0)

    rules_matched: list[str] = []

    if domain == "defense" and table_density > 0.3 and section_style == "decimal":
        family_id = "defense_spec_requirements_tables"
        confidence = 0.9
        rules_matched.append("rule_1_defense_table_decimal")
    elif domain == "defense" and has_engineering:
        family_id = "defense_engineering_drawings"
        confidence = 0.9
        rules_matched.append("rule_2_defense_engineering")
    elif domain == "engineering" and table_density > 0.2:
        family_id = "engineering_spec_tables"
        confidence = 0.9
        rules_matched.append("rule_3_engineering_tables")
    elif domain == "academic" and layout_mode == "multi_column" and has_equations:
        family_id = "academic_twocol_math"
        confidence = 0.9
        rules_matched.append("rule_4_academic_twocol_math")
    elif domain == "academic" and layout_mode == "multi_column":
        family_id = "academic_twocol_prose"
        confidence = 0.9
        rules_matched.append("rule_5_academic_twocol_prose")
    elif domain == "academic":
        family_id = "academic_singlecol"
        confidence = 0.9
        rules_matched.append("rule_6_academic_singlecol")
    elif has_toc and section_count > 20 and table_density > 0.1:
        family_id = "technical_manual_mixed"
        confidence = 0.7
        rules_matched.append("rule_7_toc_sections_tables")
    elif is_scanned:
        family_id = "scanned_mixed"
        confidence = 0.7
        rules_matched.append("rule_8_scanned")
    else:
        family_id = "general_prose"
        confidence = 0.5
        rules_matched.append("rule_9_default")

    subfamily_id: Optional[str] = None
    if table_density > 0.35 and has_toc:
        subfamily_id = "appendix_matrix_tables"
        rules_matched.append("subfamily_table_heavy_appendices")
    elif figure_density > 0.3:
        subfamily_id = "figure_heavy_sections"
        rules_matched.append("subfamily_figure_heavy_sections")

    return {
        "family_id": family_id,
        "subfamily_id": subfamily_id,
        "confidence": confidence,
        "rules_matched": rules_matched,
    }


def profile_and_assign(pdf_path: str) -> dict:
    signature = profile_for_cloning(pdf_path)
    assigned = assign_family(signature)
    return {**signature, **assigned}



def render_ir_to_pdf(ir: dict, output_path: str) -> str:
    if not REPORTLAB_AVAILABLE:
        raise ImportError("reportlab is required for PDF rendering but not installed")
    
    valid, errors = validate_ir(ir)
    valid, errors = validate_ir(ir)
    if not valid:
        raise ValueError(f"Invalid IR: {errors}")

    parsed = WindowIR(**ir)
    c = canvas.Canvas(output_path, pagesize=letter)
    page_width, page_height = letter

    for page_num in parsed.source_pages:
        page_elements = sorted([el for el in parsed.elements if el.page == page_num], key=lambda el: el.reading_order)
        page_tables = [tbl for tbl in parsed.tables if tbl.page_start <= page_num <= tbl.page_end]

        for el in page_elements:
            x0, y0, x1, y1 = el.bbox
            x = float(x0)
            y_top = float(y1)

            if el.type == ElementType.header:
                c.setFont("Helvetica-Bold", max(8, float(el.font_size)))
                c.drawString(x, y_top, el.text)
            elif el.type in (ElementType.body, ElementType.list_item, ElementType.footnote):
                c.setFont("Helvetica", max(8, float(el.font_size)))
                c.drawString(x, y_top, el.text)
            elif el.type == ElementType.caption:
                c.setFont("Helvetica-Oblique", max(6, float(el.font_size) - 1))
                c.drawString(x, y_top, el.text)
            elif el.type == ElementType.running_header:
                c.setFont("Helvetica", max(8, float(el.font_size)))
                c.drawString(x, page_height - 24, el.text)
            elif el.type == ElementType.running_footer:
                c.setFont("Helvetica", max(8, float(el.font_size)))
                c.drawString(x, 18, el.text)
            elif el.type == ElementType.equation:
                c.setFont("Courier", max(8, float(el.font_size)))
                c.drawString(x, y_top, el.text or "[equation]")
            elif el.type == ElementType.page_number:
                c.setFont("Helvetica", max(8, float(el.font_size)))
                text = el.text if el.text else str(page_num)
                text_w = c.stringWidth(text, "Helvetica", max(8, float(el.font_size)))
                c.drawString((page_width - text_w) / 2.0, 18, text)
            elif el.type == ElementType.figure:
                width = max(1.0, float(x1 - x0))
                height = max(1.0, float(y1 - y0))
                c.rect(x0, y0, width, height)
                c.setFont("Helvetica-Oblique", 9)
                c.drawString(x0 + 4, y1 - 12, el.text or "Figure")

        for tbl in page_tables:
            bbox = tbl.bbox_per_page.get(page_num)
            if not bbox:
                continue

            max_row = max((cell.row for cell in tbl.cells), default=-1)
            max_col = max((cell.col for cell in tbl.cells), default=-1)
            n_rows = max(tbl.n_rows, max_row + 1)
            n_cols = max(tbl.n_cols, max_col + 1)
            data = [["" for _ in range(n_cols)] for _ in range(n_rows)]

            for cell in tbl.cells:
                if 0 <= cell.row < n_rows and 0 <= cell.col < n_cols:
                    data[cell.row][cell.col] = cell.text

            table_obj = Table(data)
            styles = []
            if tbl.style == "ruled":
                styles.append(("GRID", (0, 0), (-1, -1), 0.5, colors.black))
            elif tbl.style == "light_ruled":
                styles.append(("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.grey))

            if tbl.n_header_rows > 0:
                header_end = min(tbl.n_header_rows - 1, n_rows - 1)
                styles.extend(
                    [
                        ("FONTNAME", (0, 0), (-1, header_end), "Helvetica-Bold"),
                        ("BACKGROUND", (0, 0), (-1, header_end), colors.lightgrey),
                    ]
                )

            if styles:
                table_obj.setStyle(TableStyle(styles))

            x0, y0, x1, y1 = bbox
            avail_w = max(1.0, float(x1 - x0))
            avail_h = max(1.0, float(y1 - y0))
            _, th = table_obj.wrapOn(c, avail_w, avail_h)
            table_obj.drawOn(c, float(x0), float(y1) - th)

        c.showPage()

    c.save()
    return output_path


@app.command("profile")
def profile(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    output_json: bool = typer.Option(False, "--json", is_flag=True, help="Output as JSON"),
) -> None:
    """Profile a PDF for cloning — wraps survey_document + profile into DocumentSignature."""
    result = profile_for_cloning(pdf_path)
    if output_json:
        print(json.dumps(result))
    else:
        typer.echo(f"doc_id:     {result['doc_id']}")
        typer.echo(f"domain:     {result['domain']}")
        typer.echo(f"pages:      {result['page_count']}")
        typer.echo(f"layout:     {result['layout_mode']}")
        typer.echo(f"has_toc:    {result['has_toc']}")
        typer.echo(f"tables:     {result['has_tables']} (density={result['table_density']:.2f})")
        typer.echo(f"figures:    {result['has_figures']} (density={result['figure_density']:.2f})")
        typer.echo(f"sections:   {result['section_count']} ({result['section_style']})")
        typer.echo(f"complexity: {result['complexity_score']}")


@app.command("family")
def family(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    output_json: bool = typer.Option(False, "--json", is_flag=True, help="Output as JSON"),
) -> None:
    """Profile and assign family based on rule-based signature matching."""
    result = profile_and_assign(pdf_path)
    if output_json:
        print(json.dumps(result))
    else:
        typer.echo(f"family: {result['family_id']} (confidence={result['confidence']})")


@app.command("render-ir")
def render_ir(
    ir_json: str = typer.Argument(..., help="Path to IR JSON file"),
    output_path: str = typer.Option("synthetic.pdf", "-o", help="Output PDF path"),
) -> None:
    with open(ir_json, "r", encoding="utf-8") as f:
        ir = json.load(f)
    typer.echo(render_ir_to_pdf(ir, output_path))


if __name__ == "__main__":
    app()
