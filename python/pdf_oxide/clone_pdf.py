"""PDF Cloner: Preset discovery and validation via synthetic fixtures."""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Dict, List, Optional

import typer
from pydantic import BaseModel, Field, ValidationError

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
    """Profile and assign family — placeholder for task 2."""
    result = profile_for_cloning(pdf_path)
    result["family_id"] = "general_prose"
    result["confidence"] = 0.5
    result["rules_matched"] = ["default"]
    if output_json:
        print(json.dumps(result))
    else:
        typer.echo(f"family: {result['family_id']} (confidence={result['confidence']})")


if __name__ == "__main__":
    app()
