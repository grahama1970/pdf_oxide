from __future__ import annotations

from dataclasses import dataclass, field, asdict, replace
from decimal import Decimal
from html import escape
from typing import Any, Callable, Iterable, Literal, Mapping, NewType, Sequence, Union

from reportlab.lib import colors
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    LongTable,
    Paragraph,
    Table,
    TableStyle,
    KeepTogether,
    Spacer,
    ListFlowable,
    ListItem,
    Flowable,
    Image,
)
from reportlab.platypus.frames import Frame
from reportlab.platypus.doctemplate import PageTemplate, BaseDocTemplate
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader

# ---------------------------------------------------------------------------
# Type Aliases (improved type safety per Codex review)
# ---------------------------------------------------------------------------

ColorLike = Union[Color, HexColor, None]
PresetName = NewType("PresetName", str)
TitleMode = Literal["external", "inline", "inline_continued"]
Alignment = Literal["LEFT", "CENTER", "RIGHT", "MIDDLE", "TOP", "BOTTOM"]
BulletType = Literal["1", "a", "A", "i", "I", "bullet", "-", "square"]

CellValue = Any
Row = Sequence[CellValue]
CellFormatter = Callable[[Any], str]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class TablePreset:
    """Immutable table styling preset.

    Use _clone() to create specialized variants. The parent field tracks
    inheritance chain for debugging.
    """
    name: str
    font_name: str = "Helvetica"
    font_name_bold: str = "Helvetica-Bold"
    font_size: int = 8
    header_font_size: int = 8
    leading: int = 10
    header_leading: int = 10
    cell_padding_x: int = 5
    cell_padding_y: int = 4
    header_background: ColorLike = field(default_factory=lambda: HexColor("#D9E2F3"))
    header_text_color: ColorLike = field(default_factory=lambda: colors.black)
    body_text_color: ColorLike = field(default_factory=lambda: colors.black)
    row_backgrounds: tuple[ColorLike, ColorLike] | None = field(default_factory=lambda: (HexColor("#FAFAFA"), colors.white))
    grid_color: ColorLike = field(default_factory=lambda: HexColor("#6B7280"))
    grid_width: float = 0.3
    outer_box_width: float = 0.8
    header_rule_width: float = 0.9
    valign: Alignment = "MIDDLE"
    header_valign: Alignment = "MIDDLE"
    repeat_rows: int | tuple[int, ...] = 1
    use_long_table: bool = True
    wrap_cells: bool = True
    split_by_row: bool = True
    add_outer_box: bool = True
    show_inner_grid: bool = True
    default_alignment: Alignment = "LEFT"
    column_alignments: tuple[tuple[int, Alignment], ...] = ()  # Immutable mapping
    section_background: ColorLike = field(default_factory=lambda: HexColor("#EDEDED"))
    section_text_color: ColorLike = field(default_factory=lambda: colors.black)
    total_background: ColorLike = field(default_factory=lambda: HexColor("#EAF1FB"))
    total_text_color: ColorLike = field(default_factory=lambda: colors.black)
    line_dash: tuple[int, int] | None = None
    line_cap: int = 1
    line_join: int = 1
    double_rule: bool = False
    title_mode: TitleMode = "external"
    parent: str | None = None  # Tracks inheritance chain for debugging


@dataclass(slots=True)
class TableSpec:
    """Mutable table specification (content, not styling)."""
    headers: Sequence[str]
    rows: Sequence[Row]
    col_widths: Sequence[float] | None = None
    row_heights: Sequence[float] | None = None
    spans: Sequence[tuple[tuple[int, int], tuple[int, int]]] = ()
    style_commands: Sequence[tuple[Any, ...]] = ()
    repeat_rows: int | tuple[int, ...] | None = None
    section_rows: Sequence[int] = ()
    total_rows: Sequence[int] = ()
    title: str | None = None
    continued_title: str | None = None


@dataclass(slots=True, frozen=True)
class PageTemplatePreset:
    """Immutable page template preset."""
    name: str
    pagesize: tuple[float, float] = letter
    left_margin: float = 0.7 * inch
    right_margin: float = 0.7 * inch
    top_margin: float = 0.8 * inch
    bottom_margin: float = 0.7 * inch
    landscape_mode: bool = False
    columns: int = 1
    show_blank_notice: bool = False


@dataclass(slots=True, frozen=True)
class RunningHeaderPreset:
    """Immutable running header preset."""
    name: str
    alignment: Alignment = "CENTER"
    left_text: str | None = None
    center_text: str | None = None
    right_text: str | None = None
    show_rule: bool = True
    font_size: int = 8


@dataclass(slots=True, frozen=True)
class RunningFooterPreset:
    """Immutable running footer preset."""
    name: str
    alignment: Alignment = "CENTER"
    left_text: str | None = None
    center_text: str | None = None
    right_text: str | None = None
    show_rule: bool = True
    font_size: int = 8


@dataclass(slots=True, frozen=True)
class BoxPreset:
    """Immutable callout/box preset."""
    name: str
    background: ColorLike
    border_color: ColorLike
    text_color: ColorLike = field(default_factory=lambda: colors.black)
    border_width: float = 0.75
    padding: int = 8
    title_font: str = "Helvetica-Bold"
    title_size: int = 9
    body_font: str = "Helvetica"
    body_size: int = 8


@dataclass(slots=True, frozen=True)
class CaptionPreset:
    """Immutable caption preset."""
    name: str
    prefix: str
    font_name: str = "Helvetica-Oblique"
    font_size: int = 8
    text_color: ColorLike = field(default_factory=lambda: HexColor("#374151"))
    space_before: float = 3
    space_after: float = 8


@dataclass(slots=True, frozen=True)
class ListPreset:
    """Immutable list preset."""
    name: str
    bullet_type: BulletType
    indent: float = 14
    bullet_indent: float = 0
    font_name: str = "Helvetica"
    font_size: int = 8
    leading: int = 10


@dataclass(slots=True, frozen=True)
class FormElementPreset:
    """Immutable form element preset."""
    name: str
    field_height: float = 16
    border_width: float = 0.75
    border_color: ColorLike = field(default_factory=lambda: HexColor("#6B7280"))
    fill_color: ColorLike = field(default_factory=lambda: colors.white)
    label_font: str = "Helvetica"
    label_size: int = 8


@dataclass(slots=True, frozen=True)
class DocumentBlockPreset:
    """Immutable document block preset."""
    name: str
    title: str
    box: str = "note_box"


@dataclass(slots=True, frozen=True)
class CompliancePreset:
    """Immutable compliance block preset."""
    name: str
    title: str
    box: str = "definition_box"


@dataclass(slots=True, frozen=True)
class VisualElementPreset:
    """Immutable visual element preset."""
    name: str
    border_color: ColorLike = field(default_factory=lambda: HexColor("#9CA3AF"))
    border_width: float = 0.8
    fill_color: ColorLike = field(default_factory=lambda: HexColor("#F9FAFB"))


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

ALIGNMENT_TO_REPORTLAB = {"LEFT": TA_LEFT, "CENTER": TA_CENTER, "RIGHT": TA_RIGHT}
DEFAULT_TYPE_ALIGNMENTS: dict[type, str] = {int: "RIGHT", float: "RIGHT", Decimal: "RIGHT"}

# Valid values for Literal types (used for validation)
VALID_TITLE_MODES = frozenset(("external", "inline", "inline_continued"))
VALID_ALIGNMENTS = frozenset(("LEFT", "CENTER", "RIGHT", "MIDDLE", "TOP", "BOTTOM"))
VALID_BULLET_TYPES = frozenset(("1", "a", "A", "i", "I", "bullet", "-", "square"))


class TablePresetError(ValueError):
    """Raised for invalid preset configurations."""
    pass


class PresetValidationError(ValueError):
    """Raised when preset validation fails."""
    pass


def _clone(obj, **updates):
    """Clone a frozen preset with updates, tracking parent lineage.

    Uses dataclasses.replace() for frozen dataclass compatibility.
    Automatically sets parent to the original object's name.
    """
    # Track parent lineage
    if hasattr(obj, "parent") and "parent" not in updates:
        updates["parent"] = obj.name
    return replace(obj, **updates)


def _validate_table_preset(preset: TablePreset) -> None:
    """Validate TablePreset field values at registration time."""
    if preset.title_mode not in VALID_TITLE_MODES:
        raise PresetValidationError(
            f"Invalid title_mode '{preset.title_mode}' in preset '{preset.name}'. "
            f"Valid values: {VALID_TITLE_MODES}"
        )
    if preset.default_alignment not in VALID_ALIGNMENTS:
        raise PresetValidationError(
            f"Invalid default_alignment '{preset.default_alignment}' in preset '{preset.name}'. "
            f"Valid values: {VALID_ALIGNMENTS}"
        )
    if preset.valign not in VALID_ALIGNMENTS:
        raise PresetValidationError(
            f"Invalid valign '{preset.valign}' in preset '{preset.name}'. "
            f"Valid values: {VALID_ALIGNMENTS}"
        )


def _validate_list_preset(preset: ListPreset) -> None:
    """Validate ListPreset field values at registration time."""
    if preset.bullet_type not in VALID_BULLET_TYPES:
        raise PresetValidationError(
            f"Invalid bullet_type '{preset.bullet_type}' in preset '{preset.name}'. "
            f"Valid values: {VALID_BULLET_TYPES}"
        )


def _register_presets(presets: dict, validator=None) -> dict:
    """Register presets with optional validation."""
    if validator:
        for preset in presets.values():
            validator(preset)
    return presets


# ---------------------------------------------------------------------------
# Base presets (parents for inheritance)
# ---------------------------------------------------------------------------

BASE_TABLE = TablePreset(name="base_table")
GRIDLESS_BASE = _clone(
    BASE_TABLE,
    name="gridless_base",
    add_outer_box=False,
    show_inner_grid=False,
    row_backgrounds=None,
    header_background=HexColor("#EEF2FF"),
)
LEDGER_BASE = _clone(
    BASE_TABLE,
    name="ledger_base",
    row_backgrounds=None,
    header_background=HexColor("#E5E7EB"),
    grid_width=0.2,
    outer_box_width=0.9,
)
MATRIX_BASE = _clone(
    BASE_TABLE,
    name="matrix_base",
    font_size=7,
    header_font_size=7,
    leading=9,
    header_leading=9,
    cell_padding_x=4,
    cell_padding_y=3,
)

# ---------------------------------------------------------------------------
# Table Presets Registry (36 presets)
# ---------------------------------------------------------------------------

TABLE_PRESETS: dict[str, TablePreset] = _register_presets({
    # Core
    "data_grid": _clone(BASE_TABLE, name="data_grid"),
    "requirements_matrix": _clone(MATRIX_BASE, name="requirements_matrix", title_mode="inline_continued"),
    "comparison_matrix": _clone(MATRIX_BASE, name="comparison_matrix", default_alignment="CENTER", row_backgrounds=None),
    "ledger": _clone(LEDGER_BASE, name="ledger"),
    "sectioned_grid": _clone(BASE_TABLE, name="sectioned_grid"),
    "gridless_report": _clone(GRIDLESS_BASE, name="gridless_report", title_mode="external"),
    # Compliance/Governance
    "control_matrix": _clone(MATRIX_BASE, name="control_matrix", title_mode="inline_continued"),
    "risk_register": _clone(BASE_TABLE, name="risk_register", column_alignments=((3, "CENTER"), (4, "CENTER"), (5, "RIGHT"))),
    "traceability_matrix": _clone(MATRIX_BASE, name="traceability_matrix", title_mode="inline_continued"),
    "gap_analysis": _clone(BASE_TABLE, name="gap_analysis"),
    "poam": _clone(BASE_TABLE, name="poam"),
    "assessment_results": _clone(BASE_TABLE, name="assessment_results"),
    "audit_findings": _clone(BASE_TABLE, name="audit_findings"),
    "evidence_summary": _clone(BASE_TABLE, name="evidence_summary"),
    "compliance_status": _clone(BASE_TABLE, name="compliance_status"),
    # Engineering/Technical
    "test_results": _clone(BASE_TABLE, name="test_results"),
    "parameter_table": _clone(LEDGER_BASE, name="parameter_table"),
    "specification_table": _clone(BASE_TABLE, name="specification_table"),
    "interface_table": _clone(BASE_TABLE, name="interface_table"),
    "hazard_analysis": _clone(BASE_TABLE, name="hazard_analysis"),
    # Project Management
    "action_items": _clone(BASE_TABLE, name="action_items"),
    "decision_log": _clone(BASE_TABLE, name="decision_log"),
    "change_requests": _clone(BASE_TABLE, name="change_requests"),
    "schedule_table": _clone(BASE_TABLE, name="schedule_table"),
    "budget_table": _clone(LEDGER_BASE, name="budget_table"),
    "raci_matrix": _clone(MATRIX_BASE, name="raci_matrix", default_alignment="CENTER"),
    "meeting_minutes": _clone(BASE_TABLE, name="meeting_minutes"),
    # Reference/Metadata
    "revision_history": _clone(LEDGER_BASE, name="revision_history"),
    "glossary": _clone(GRIDLESS_BASE, name="glossary"),
    "acronyms": _clone(GRIDLESS_BASE, name="acronyms"),
    "references": _clone(GRIDLESS_BASE, name="references"),
    "personnel_table": _clone(BASE_TABLE, name="personnel_table"),
    "distribution_list": _clone(BASE_TABLE, name="distribution_list"),
    # Form-like
    "signature_block": _clone(GRIDLESS_BASE, name="signature_block", row_backgrounds=None),
    "approval_block": _clone(GRIDLESS_BASE, name="approval_block", row_backgrounds=None),
    "checklist": _clone(BASE_TABLE, name="checklist"),
}, _validate_table_preset)

PAGE_TEMPLATE_PRESETS: dict[str, PageTemplatePreset] = {
    "standard_page": PageTemplatePreset("standard_page"),
    "cover_page": PageTemplatePreset("cover_page", top_margin=1.2 * inch),
    "toc_page": PageTemplatePreset("toc_page"),
    "lof_page": PageTemplatePreset("lof_page"),
    "lot_page": PageTemplatePreset("lot_page"),
    "appendix_page": PageTemplatePreset("appendix_page"),
    "landscape_page": PageTemplatePreset("landscape_page", pagesize=landscape(letter), landscape_mode=True),
    "two_column_page": PageTemplatePreset("two_column_page", columns=2),
    "form_page": PageTemplatePreset("form_page", left_margin=0.6 * inch, right_margin=0.6 * inch),
    "title_page": PageTemplatePreset("title_page", top_margin=1.4 * inch),
    "back_cover": PageTemplatePreset("back_cover"),
    "blank_page": PageTemplatePreset("blank_page", show_blank_notice=True),
}

RUNNING_HEADER_PRESETS: dict[str, RunningHeaderPreset] = {
    "doc_title_header": RunningHeaderPreset("doc_title_header", left_text="Document Title", right_text="Page %(page)d"),
    "section_header": RunningHeaderPreset("section_header", center_text="Section Name"),
    "classified_header": RunningHeaderPreset("classified_header", left_text="CONTROLLED", center_text="Document Title", right_text="CONTROLLED"),
    "versioned_header": RunningHeaderPreset("versioned_header", left_text="Document Title", center_text="v1.0", right_text="2026-04-12"),
    "chapter_header": RunningHeaderPreset("chapter_header", center_text="Chapter 1 - Overview"),
    "minimal_header": RunningHeaderPreset("minimal_header", right_text="%(page)d", show_rule=False),
    "dual_logo_header": RunningHeaderPreset("dual_logo_header", center_text="Document Title"),
    "nist_header": RunningHeaderPreset("nist_header", center_text="NIST Special Publication"),
}

RUNNING_FOOTER_PRESETS: dict[str, RunningFooterPreset] = {
    "page_number_footer": RunningFooterPreset("page_number_footer", center_text="Page %(page)d of %(total)d"),
    "classified_footer": RunningFooterPreset("classified_footer", center_text="CONTROLLED - HANDLE PER POLICY"),
    "revision_footer": RunningFooterPreset("revision_footer", left_text="DOC-001", center_text="Rev A", right_text="Page %(page)d"),
    "copyright_footer": RunningFooterPreset("copyright_footer", center_text="Copyright 2026 Example Org"),
    "doc_control_footer": RunningFooterPreset("doc_control_footer", left_text="DCN 24-0001", right_text="%(page)d"),
    "draft_footer": RunningFooterPreset("draft_footer", center_text="DRAFT - 2026-04-12 - Page %(page)d"),
    "distribution_footer": RunningFooterPreset("distribution_footer", center_text="Distribution A - Approved for Public Release"),
    "minimal_footer": RunningFooterPreset("minimal_footer", center_text="%(page)d", show_rule=False),
}

CALLOUT_PRESETS: dict[str, BoxPreset] = {
    "note_box": BoxPreset("note_box", background=colors.HexColor("#EFF6FF"), border_color=colors.HexColor("#60A5FA")),
    "warning_box": BoxPreset("warning_box", background=colors.HexColor("#FFF7ED"), border_color=colors.HexColor("#F59E0B")),
    "danger_box": BoxPreset("danger_box", background=colors.HexColor("#FEF2F2"), border_color=colors.HexColor("#EF4444")),
    "tip_box": BoxPreset("tip_box", background=colors.HexColor("#F0FDF4"), border_color=colors.HexColor("#22C55E")),
    "example_box": BoxPreset("example_box", background=colors.HexColor("#F9FAFB"), border_color=colors.HexColor("#9CA3AF")),
    "definition_box": BoxPreset("definition_box", background=colors.HexColor("#F5F3FF"), border_color=colors.HexColor("#8B5CF6")),
    "quote_block": BoxPreset("quote_block", background=colors.white, border_color=colors.HexColor("#D1D5DB")),
    "code_block": BoxPreset("code_block", background=colors.HexColor("#F3F4F6"), border_color=colors.HexColor("#6B7280"), body_font="Courier", body_size=7),
    "requirement_box": BoxPreset("requirement_box", background=colors.HexColor("#EFF6FF"), border_color=colors.HexColor("#2563EB")),
    "finding_box": BoxPreset("finding_box", background=colors.HexColor("#FEF2F2"), border_color=colors.HexColor("#DC2626")),
    "recommendation_box": BoxPreset("recommendation_box", background=colors.HexColor("#F0FDF4"), border_color=colors.HexColor("#16A34A")),
    "reference_box": BoxPreset("reference_box", background=colors.HexColor("#FAFAFA"), border_color=colors.HexColor("#9CA3AF")),
}

CAPTION_PRESETS: dict[str, CaptionPreset] = {
    "figure_caption": CaptionPreset("figure_caption", "Figure"),
    "table_caption": CaptionPreset("table_caption", "Table"),
    "equation_caption": CaptionPreset("equation_caption", "Equation"),
    "listing_caption": CaptionPreset("listing_caption", "Listing"),
    "appendix_caption": CaptionPreset("appendix_caption", "Appendix"),
    "exhibit_caption": CaptionPreset("exhibit_caption", "Exhibit"),
}

LIST_PRESETS: dict[str, ListPreset] = {
    "numbered_list": ListPreset("numbered_list", "1", indent=14),
    "alpha_list": ListPreset("alpha_list", "a", indent=14),
    "roman_list": ListPreset("roman_list", "i", indent=18),
    "bullet_list": ListPreset("bullet_list", "bullet", indent=12),
    "dash_list": ListPreset("dash_list", "-", indent=12),
    "checkbox_list": ListPreset("checkbox_list", "square", indent=16),
    "procedure_steps": ListPreset("procedure_steps", "1", indent=20, font_name="Helvetica-Bold", font_size=9),
    "definition_list": ListPreset("definition_list", "bullet", indent=0, bullet_indent=0, leading=12),
    "nested_numbered": ListPreset("nested_numbered", "1", indent=18, leading=11),
    "requirement_list": ListPreset("requirement_list", "1", indent=22, font_name="Courier", font_size=8),
}

FORM_ELEMENT_PRESETS: dict[str, FormElementPreset] = {
    "labeled_field": FormElementPreset("labeled_field", field_height=18, border_width=0.75),
    "inline_field": FormElementPreset("inline_field", field_height=14, border_width=0.5, border_color=colors.HexColor("#9CA3AF")),
    "checkbox_item": FormElementPreset("checkbox_item", field_height=12, border_width=1.0),
    "radio_group": FormElementPreset("radio_group", field_height=12, border_width=1.0, fill_color=colors.HexColor("#F9FAFB")),
    "signature_line": FormElementPreset("signature_line", field_height=20, border_width=0.75, fill_color=colors.transparent),
    "text_area": FormElementPreset("text_area", field_height=48, border_width=0.75, fill_color=colors.HexColor("#FAFAFA")),
    "date_field": FormElementPreset("date_field", field_height=16, border_width=0.5, border_color=colors.HexColor("#6B7280")),
    "dropdown_placeholder": FormElementPreset("dropdown_placeholder", field_height=18, border_width=0.75, fill_color=colors.HexColor("#F3F4F6")),
    "file_reference": FormElementPreset("file_reference", field_height=14, border_width=0.5, border_color=colors.HexColor("#9CA3AF"), label_font="Courier"),
    "initials_field": FormElementPreset("initials_field", field_height=24, border_width=1.0, label_size=10),
}

DOCUMENT_BLOCK_PRESETS: dict[str, DocumentBlockPreset] = {
    "doc_info_block": DocumentBlockPreset("doc_info_block", "Document Information", "note_box"),
    "distribution_block": DocumentBlockPreset("distribution_block", "Distribution", "definition_box"),
    "supersession_notice": DocumentBlockPreset("supersession_notice", "Supersession Notice", "warning_box"),
    "effective_date_block": DocumentBlockPreset("effective_date_block", "Effective Dates", "example_box"),
    "review_block": DocumentBlockPreset("review_block", "Review", "example_box"),
    "poc_block": DocumentBlockPreset("poc_block", "Point of Contact", "note_box"),
    "authority_block": DocumentBlockPreset("authority_block", "Issuing Authority", "definition_box"),
    "classification_block": DocumentBlockPreset("classification_block", "Classification", "warning_box"),
    "caveat_block": DocumentBlockPreset("caveat_block", "Caveats", "warning_box"),
    "releasability_block": DocumentBlockPreset("releasability_block", "Releasability", "note_box"),
    "abstract_block": DocumentBlockPreset("abstract_block", "Abstract", "example_box"),
    "executive_summary": DocumentBlockPreset("executive_summary", "Executive Summary", "note_box"),
    "scope_block": DocumentBlockPreset("scope_block", "Scope", "example_box"),
    "applicability_block": DocumentBlockPreset("applicability_block", "Applicability", "example_box"),
}

COMPLIANCE_PRESETS: dict[str, CompliancePreset] = {
    "control_description": CompliancePreset("control_description", "Control Description", "definition_box"),
    "implementation_statement": CompliancePreset("implementation_statement", "Implementation Statement", "note_box"),
    "evidence_citation": CompliancePreset("evidence_citation", "Evidence Citation", "reference_box"),
    "assessment_finding": CompliancePreset("assessment_finding", "Assessment Finding", "finding_box"),
    "risk_statement": CompliancePreset("risk_statement", "Risk Statement", "warning_box"),
    "mitigation_description": CompliancePreset("mitigation_description", "Mitigation Description", "tip_box"),
    "rationale_block": CompliancePreset("rationale_block", "Rationale", "example_box"),
    "assumption_block": CompliancePreset("assumption_block", "Assumption", "example_box"),
    "constraint_block": CompliancePreset("constraint_block", "Constraint", "warning_box"),
    "inheritance_statement": CompliancePreset("inheritance_statement", "Inheritance Statement", "definition_box"),
    "responsibility_statement": CompliancePreset("responsibility_statement", "Responsibility Statement", "note_box"),
    "continuous_monitoring": CompliancePreset("continuous_monitoring", "Continuous Monitoring", "tip_box"),
}

VISUAL_ELEMENT_PRESETS: dict[str, VisualElementPreset] = {
    "figure_frame": VisualElementPreset("figure_frame", border_color=colors.HexColor("#9CA3AF"), border_width=0.8, fill_color=colors.HexColor("#F9FAFB")),
    "diagram_container": VisualElementPreset("diagram_container", border_color=colors.HexColor("#6B7280"), border_width=1.0, fill_color=colors.white),
    "chart_frame": VisualElementPreset("chart_frame", border_color=colors.HexColor("#D1D5DB"), border_width=0.5, fill_color=colors.HexColor("#FAFAFA")),
    "logo_placement": VisualElementPreset("logo_placement", border_color=colors.transparent, border_width=0, fill_color=colors.transparent),
    "icon_badge": VisualElementPreset("icon_badge", border_color=colors.HexColor("#E5E7EB"), border_width=0.75, fill_color=colors.HexColor("#F3F4F6")),
    "severity_indicator": VisualElementPreset("severity_indicator", border_color=colors.HexColor("#DC2626"), border_width=1.5, fill_color=colors.HexColor("#FEE2E2")),
    "status_pill": VisualElementPreset("status_pill", border_color=colors.transparent, border_width=0, fill_color=colors.HexColor("#E5E7EB")),
    "progress_bar": VisualElementPreset("progress_bar", border_color=colors.HexColor("#D1D5DB"), border_width=0.5, fill_color=colors.HexColor("#E5E7EB")),
}

PRESET_CATEGORIES = {
    "tables": TABLE_PRESETS,
    "page_templates": PAGE_TEMPLATE_PRESETS,
    "running_headers": RUNNING_HEADER_PRESETS,
    "running_footers": RUNNING_FOOTER_PRESETS,
    "callout_blocks": CALLOUT_PRESETS,
    "caption_styles": CAPTION_PRESETS,
    "list_styles": LIST_PRESETS,
    "form_elements": FORM_ELEMENT_PRESETS,
    "document_blocks": DOCUMENT_BLOCK_PRESETS,
    "compliance_specific": COMPLIANCE_PRESETS,
    "visual_elements": VISUAL_ELEMENT_PRESETS,
}


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def html_text(value: Any) -> str:
    return escape("" if value is None else str(value)).replace("\n", "<br/>")


def default_formatter(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)


def _column_alignments_to_dict(
    alignments: Mapping[int, str] | Sequence[tuple[int, str]] | None
) -> dict[int, str]:
    """Convert column_alignments to dict format.

    Accepts both dict (legacy) and tuple of (col_idx, alignment) pairs (frozen dataclass).
    """
    if alignments is None:
        return {}
    if isinstance(alignments, dict):
        return dict(alignments)
    # Tuple of (col_idx, alignment) pairs
    return dict(alignments)


def infer_column_alignments(
    rows: Sequence[Row],
    overrides: Mapping[int, str] | Sequence[tuple[int, str]] | None = None,
) -> dict[int, str]:
    """Infer column alignments from row data with optional overrides.

    Args:
        rows: Table row data
        overrides: Manual alignment overrides (dict or tuple pairs)

    Returns:
        Dict mapping column index to alignment string
    """
    alignments = _column_alignments_to_dict(overrides)
    if not rows:
        return alignments
    max_cols = max(len(row) for row in rows)
    for col_idx in range(max_cols):
        if col_idx in alignments:
            continue
        sample = next((row[col_idx] for row in rows if col_idx < len(row) and row[col_idx] not in (None, "")), None)
        if sample is None:
            continue
        for value_type, alignment in DEFAULT_TYPE_ALIGNMENTS.items():
            if isinstance(sample, value_type):
                alignments[col_idx] = alignment
                break
    return alignments


def make_paragraph_styles(preset: TablePreset) -> tuple[ParagraphStyle, ParagraphStyle, ParagraphStyle]:
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        name=f"{preset.name}_body",
        parent=styles["BodyText"],
        fontName=preset.font_name,
        fontSize=preset.font_size,
        leading=preset.leading,
        textColor=preset.body_text_color,
        spaceBefore=0,
        spaceAfter=0,
    )
    header = ParagraphStyle(
        name=f"{preset.name}_header",
        parent=body,
        fontName=preset.font_name_bold,
        fontSize=preset.header_font_size,
        leading=preset.header_leading,
        textColor=preset.header_text_color,
    )
    title = ParagraphStyle(
        name=f"{preset.name}_title",
        parent=header,
        fontSize=max(preset.header_font_size + 1, 9),
        leading=max(preset.header_leading + 1, 11),
    )
    return body, header, title


def _p(value: Any, style: ParagraphStyle, formatter: CellFormatter = default_formatter) -> Paragraph:
    return Paragraph(html_text(formatter(value)), style)


def build_table_data(spec: TableSpec, preset: TablePreset, formatter: CellFormatter = default_formatter) -> list[list[Any]]:
    body_style, header_style, title_style = make_paragraph_styles(preset)
    data: list[list[Any]] = []
    if spec.title and preset.title_mode in {"inline", "inline_continued"}:
        data.append([_p(spec.title, title_style, lambda x: str(x))] + [""] * (len(spec.headers) - 1))
    data.append([_p(h, header_style, lambda x: str(x)) for h in spec.headers])
    for row in spec.rows:
        if preset.wrap_cells:
            data.append([_p(v, body_style, formatter) for v in row])
        else:
            data.append([formatter(v) for v in row])
    return data


def _line_cmd(name: str, start: tuple[int, int], stop: tuple[int, int], preset: TablePreset):
    cmd: tuple[Any, ...] = (name, start, stop, preset.grid_width, preset.grid_color, preset.line_cap, preset.line_dash, preset.line_join)
    return cmd


def build_base_style(spec: TableSpec, preset: TablePreset) -> TableStyle:
    if not spec.headers:
        raise TablePresetError("headers may not be empty")
    title_rows = 1 if spec.title and preset.title_mode in {"inline", "inline_continued"} else 0
    header_row_index = title_rows
    first_body_row = header_row_index + 1
    last_row = title_rows + len(spec.rows)
    last_col = len(spec.headers) - 1
    repeat_rows = spec.repeat_rows if spec.repeat_rows is not None else preset.repeat_rows
    alignments = infer_column_alignments(spec.rows, preset.column_alignments)

    cmds: list[tuple[Any, ...]] = [
        ("VALIGN", (0, 0), (-1, -1), preset.valign),
        ("VALIGN", (0, header_row_index), (-1, header_row_index), preset.header_valign),
        ("LEFTPADDING", (0, 0), (-1, -1), preset.cell_padding_x),
        ("RIGHTPADDING", (0, 0), (-1, -1), preset.cell_padding_x),
        ("TOPPADDING", (0, 0), (-1, -1), preset.cell_padding_y),
        ("BOTTOMPADDING", (0, 0), (-1, -1), preset.cell_padding_y),
        ("TEXTCOLOR", (0, header_row_index), (-1, header_row_index), preset.header_text_color),
        ("BACKGROUND", (0, header_row_index), (-1, header_row_index), preset.header_background),
        ("LINEBELOW", (0, header_row_index), (-1, header_row_index), preset.header_rule_width, preset.grid_color),
    ]

    if title_rows:
        cmds.extend([
            ("SPAN", (0, 0), (-1, 0)),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5EEF9")),
            ("LINEBELOW", (0, 0), (-1, 0), 1.0, preset.grid_color),
        ])

    if preset.add_outer_box:
        cmds.append(("BOX", (0, 0), (-1, -1), preset.outer_box_width, preset.grid_color))
    if preset.show_inner_grid:
        cmds.append(("INNERGRID", (0, header_row_index), (-1, -1), preset.grid_width, preset.grid_color))
    if preset.row_backgrounds and last_row >= first_body_row:
        cmds.append(("ROWBACKGROUNDS", (0, first_body_row), (-1, last_row), list(preset.row_backgrounds)))

    # Native line variations using ReportLab tuple extensions when appropriate.
    if preset.line_dash is not None:
        cmds.append(_line_cmd("LINEBELOW", (0, header_row_index), (-1, header_row_index), preset))
        if preset.add_outer_box:
            cmds.append(("BOX", (0, 0), (-1, -1), preset.outer_box_width, preset.grid_color, preset.line_cap, preset.line_dash, preset.line_join))
        if preset.show_inner_grid:
            cmds.append(("INNERGRID", (0, header_row_index), (-1, -1), preset.grid_width, preset.grid_color))

    if preset.double_rule:
        y = header_row_index
        cmds.append(("LINEBELOW", (0, y), (-1, y), preset.header_rule_width, preset.grid_color))
        cmds.append(("LINEBELOW", (0, y), (-1, y), max(preset.header_rule_width - 0.35, 0.35), preset.grid_color, 1, None, 1, 2, 2))

    for col_idx, alignment in alignments.items():
        cmds.append(("ALIGN", (col_idx, first_body_row), (col_idx, -1), alignment))
    cmds.append(("ALIGN", (0, header_row_index), (-1, header_row_index), preset.default_alignment))

    for row_idx in spec.section_rows:
        rr = row_idx + first_body_row
        cmds.extend([
            ("BACKGROUND", (0, rr), (-1, rr), preset.section_background),
            ("TEXTCOLOR", (0, rr), (-1, rr), preset.section_text_color),
            ("FONTNAME", (0, rr), (-1, rr), preset.font_name_bold),
            ("LINEABOVE", (0, rr), (-1, rr), 0.9, preset.grid_color),
        ])

    for row_idx in spec.total_rows:
        rr = row_idx + first_body_row
        cmds.extend([
            ("BACKGROUND", (0, rr), (-1, rr), preset.total_background),
            ("TEXTCOLOR", (0, rr), (-1, rr), preset.total_text_color),
            ("FONTNAME", (0, rr), (-1, rr), preset.font_name_bold),
            ("LINEABOVE", (0, rr), (-1, rr), 1.0, preset.grid_color),
        ])

    for start, stop in spec.spans:
        sx, sy = start
        ex, ey = stop
        cmds.append(("SPAN", (sx, sy + first_body_row), (ex, ey + first_body_row)))

    cmds.extend(spec.style_commands)
    return TableStyle(cmds)


def build_table(spec: TableSpec, preset: str | TablePreset = "data_grid", formatter: CellFormatter = default_formatter):
    preset_obj = TABLE_PRESETS[preset] if isinstance(preset, str) else preset
    data = build_table_data(spec, preset_obj, formatter=formatter)
    repeat_rows = spec.repeat_rows if spec.repeat_rows is not None else preset_obj.repeat_rows
    table_cls = LongTable if preset_obj.use_long_table else Table
    tbl = table_cls(
        data,
        colWidths=spec.col_widths,
        rowHeights=spec.row_heights,
        repeatRows=repeat_rows,
        splitByRow=preset_obj.split_by_row,
    )
    tbl.setStyle(build_base_style(spec, preset_obj))
    return tbl


def build_table_from_dataframe(df: Any, preset: str | TablePreset = "data_grid", *, col_widths: Sequence[float] | None = None, **kwargs):
    spec = TableSpec(headers=[str(c) for c in df.columns.tolist()], rows=df.values.tolist(), col_widths=col_widths, **kwargs)
    return build_table(spec, preset=preset)


# ---------------------------------------------------------------------------
# Simple higher-level blocks
# ---------------------------------------------------------------------------


def _box_styles(preset: BoxPreset) -> tuple[ParagraphStyle, ParagraphStyle]:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        name=f"{preset.name}_title",
        parent=styles["BodyText"],
        fontName=preset.title_font,
        fontSize=preset.title_size,
        leading=preset.title_size + 2,
        textColor=preset.text_color,
        spaceAfter=2,
    )
    body_style = ParagraphStyle(
        name=f"{preset.name}_body",
        parent=styles["BodyText"],
        fontName=preset.body_font,
        fontSize=preset.body_size,
        leading=preset.body_size + 2,
        textColor=preset.text_color,
    )
    return title_style, body_style


def build_callout(title: str, body: str, preset: str = "note_box"):
    p = CALLOUT_PRESETS[preset]
    title_style, body_style = _box_styles(p)
    table = Table(
        [[Paragraph(html_text(title), title_style)], [Paragraph(html_text(body), body_style)]],
        colWidths=[6.8 * inch],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), p.background),
        ("BOX", (0, 0), (-1, -1), p.border_width, p.border_color),
        ("LEFTPADDING", (0, 0), (-1, -1), p.padding),
        ("RIGHTPADDING", (0, 0), (-1, -1), p.padding),
        ("TOPPADDING", (0, 0), (-1, -1), p.padding - 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), p.padding - 2),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, p.border_color),
    ]))
    return table


def build_document_block(preset: str, body: str):
    block = DOCUMENT_BLOCK_PRESETS[preset]
    return build_callout(block.title, body, preset=block.box)


def build_compliance_block(preset: str, body: str):
    block = COMPLIANCE_PRESETS[preset]
    return build_callout(block.title, body, preset=block.box)


def build_caption(text: str, number: str, preset: str = "table_caption"):
    cp = CAPTION_PRESETS[preset]
    styles = getSampleStyleSheet()
    style = ParagraphStyle(
        name=f"{cp.name}_style",
        parent=styles["BodyText"],
        fontName=cp.font_name,
        fontSize=cp.font_size,
        leading=cp.font_size + 2,
        textColor=cp.text_color,
        spaceBefore=cp.space_before,
        spaceAfter=cp.space_after,
    )
    return Paragraph(html_text(f"{cp.prefix} {number}: {text}"), style)


def registry_summary() -> list[tuple[str, int]]:
    return [(name, len(reg)) for name, reg in PRESET_CATEGORIES.items()]


def list_preset_names(category: str) -> list[str]:
    return list(PRESET_CATEGORIES[category].keys())


def add_section_row(rows: list[list[Any]], label: str, width: int) -> tuple[int, tuple[tuple[int, int], tuple[int, int]]]:
    index = len(rows)
    rows.append([label] + [""] * (width - 1))
    return index, ((0, index), (width - 1, index))


def add_total_row(rows: list[list[Any]], label: str, values: Sequence[Any]) -> int:
    index = len(rows)
    rows.append([label] + list(values))
    return index


# ---------------------------------------------------------------------------
# List rendering
# ---------------------------------------------------------------------------


BULLET_TYPE_MAP = {
    "1": "1",           # numbered 1, 2, 3...
    "a": "a",           # alpha a, b, c...
    "A": "A",           # Alpha A, B, C...
    "i": "i",           # roman i, ii, iii...
    "I": "I",           # Roman I, II, III...
    "bullet": "bullet",
    "-": "-",           # dash
    "square": "square", # checkbox style
}


def build_list(
    items: Sequence[str | Flowable],
    preset: str | ListPreset = "bullet_list",
    start: int | str | None = None,
) -> ListFlowable:
    """Build a ListFlowable from a preset and sequence of items.

    Args:
        items: Sequence of strings or Flowable objects (like Paragraph)
        preset: Preset name or ListPreset object
        start: Optional starting value (number for numbered, 'circle'/'square' for bullets)

    Returns:
        ListFlowable ready to add to a story
    """
    p = LIST_PRESETS[preset] if isinstance(preset, str) else preset
    styles = getSampleStyleSheet()
    item_style = ParagraphStyle(
        name=f"{p.name}_item",
        parent=styles["BodyText"],
        fontName=p.font_name,
        fontSize=p.font_size,
        leading=p.leading,
    )

    # Convert strings to Paragraphs
    flowable_items = []
    for item in items:
        if isinstance(item, str):
            flowable_items.append(Paragraph(html_text(item), item_style))
        else:
            flowable_items.append(item)

    bullet_type = BULLET_TYPE_MAP.get(p.bullet_type, "bullet")

    return ListFlowable(
        flowable_items,
        bulletType=bullet_type,
        start=start,
        leftIndent=p.indent,
        bulletOffsetY=0,
    )


def build_nested_list(
    items: Sequence[str | Flowable | tuple[str, Sequence[str]]],
    preset: str | ListPreset = "numbered_list",
    sub_preset: str | ListPreset = "bullet_list",
) -> ListFlowable:
    """Build a nested ListFlowable with sub-lists.

    Args:
        items: Sequence where each item is either:
               - A string (regular item)
               - A Flowable
               - A tuple of (string, list of sub-items)
        preset: Preset for the outer list
        sub_preset: Preset for nested sub-lists

    Returns:
        ListFlowable with nested structure
    """
    p = LIST_PRESETS[preset] if isinstance(preset, str) else preset
    sp = LIST_PRESETS[sub_preset] if isinstance(sub_preset, str) else sub_preset

    styles = getSampleStyleSheet()
    item_style = ParagraphStyle(
        name=f"{p.name}_item",
        parent=styles["BodyText"],
        fontName=p.font_name,
        fontSize=p.font_size,
        leading=p.leading,
    )

    flowable_items = []
    for item in items:
        if isinstance(item, tuple) and len(item) == 2:
            text, sub_items = item
            flowable_items.append(Paragraph(html_text(text), item_style))
            flowable_items.append(build_list(sub_items, preset=sp))
        elif isinstance(item, str):
            flowable_items.append(Paragraph(html_text(item), item_style))
        else:
            flowable_items.append(item)

    bullet_type = BULLET_TYPE_MAP.get(p.bullet_type, "bullet")
    return ListFlowable(
        flowable_items,
        bulletType=bullet_type,
        leftIndent=p.indent,
    )


# ---------------------------------------------------------------------------
# Page template rendering
# ---------------------------------------------------------------------------


def build_page_template(
    template_id: str,
    preset: str | PageTemplatePreset = "standard_page",
    on_page: Callable | None = None,
    on_page_end: Callable | None = None,
) -> PageTemplate:
    """Build a PageTemplate from a preset.

    Args:
        template_id: Unique identifier for the template
        preset: Preset name or PageTemplatePreset object
        on_page: Optional callback called at start of each page: fn(canvas, doc)
        on_page_end: Optional callback called at end of each page: fn(canvas, doc)

    Returns:
        PageTemplate ready to add to a BaseDocTemplate
    """
    p = PAGE_TEMPLATE_PRESETS[preset] if isinstance(preset, str) else preset

    pagesize = p.pagesize
    if p.landscape_mode and pagesize[0] < pagesize[1]:
        pagesize = landscape(pagesize)

    # Calculate frame dimensions
    frame_x = p.left_margin
    frame_y = p.bottom_margin
    frame_width = pagesize[0] - p.left_margin - p.right_margin
    frame_height = pagesize[1] - p.top_margin - p.bottom_margin

    if p.columns == 1:
        frames = [
            Frame(
                frame_x, frame_y, frame_width, frame_height,
                id=f"{template_id}_frame",
                leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
            )
        ]
    else:
        # Multi-column layout
        gutter = 0.25 * inch
        col_width = (frame_width - gutter * (p.columns - 1)) / p.columns
        frames = []
        for i in range(p.columns):
            col_x = frame_x + i * (col_width + gutter)
            frames.append(
                Frame(
                    col_x, frame_y, col_width, frame_height,
                    id=f"{template_id}_col{i+1}",
                    leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
                )
            )

    return PageTemplate(
        id=template_id,
        frames=frames,
        onPage=on_page,
        onPageEnd=on_page_end,
        pagesize=pagesize,
    )


def _safe_attr(obj: Any, name: str, default: Any = "") -> Any:
    """Safely get an attribute from an object with a default."""
    return getattr(obj, name, default)


def _format_running_text(text: str | None, canvas: Canvas, doc: BaseDocTemplate, extra_ctx: dict | None = None) -> str:
    """Format running text with substitution variables.

    Supports both % and .format() syntax with fallback.
    Variables: page, total, title, author, date, section, version
    """
    if not text:
        return ""
    page = canvas.getPageNumber()
    total = getattr(canvas, "_page_count", None) or getattr(doc, "page_count", None) or page
    values = {
        "page": page,
        "total": total,
        "title": _safe_attr(doc, "title", ""),
        "author": _safe_attr(doc, "author", ""),
        "date": _safe_attr(doc, "date_str", ""),
        "section": _safe_attr(doc, "current_section", ""),
        "version": _safe_attr(doc, "version", ""),
    }
    if extra_ctx:
        values.update(extra_ctx)
    try:
        return text % values
    except Exception:
        try:
            return text.format(**values)
        except Exception:
            return text


def _draw_running_band(
    canvas: Canvas,
    page_width: float,
    left_margin: float,
    right_margin: float,
    y: float,
    left: str | None,
    center: str | None,
    right: str | None,
    font_name: str = "Helvetica",
    font_size: int = 8,
    rule_y: float | None = None,
    rule_color: Any = colors.HexColor("#D1D5DB"),
):
    """Draw a running header or footer band with left/center/right text."""
    left_x = left_margin
    right_x = page_width - right_margin
    center_x = page_width / 2.0
    canvas.setFont(font_name, font_size)
    if left:
        canvas.drawString(left_x, y, left)
    if center:
        canvas.drawCentredString(center_x, y, center)
    if right:
        canvas.drawRightString(right_x, y, right)
    if rule_y is not None:
        canvas.setStrokeColor(rule_color)
        canvas.setLineWidth(0.5)
        canvas.line(left_x, rule_y, right_x, rule_y)


def build_header_callback(
    preset: str | RunningHeaderPreset = "doc_title_header",
    doc_title: str | None = None,
    section_name: str | None = None,
) -> Callable:
    """Build an onPage callback for headers.

    Args:
        preset: Preset name or RunningHeaderPreset object
        doc_title: Optional title to substitute for %(title)s
        section_name: Optional section name for substitution

    Returns:
        Callback function: fn(canvas, doc)

    Special presets:
        - dual_logo_header: Draws logo placeholder boxes on left and right
        - nist_header: Dark blue styling for NIST documents
    """
    p = RUNNING_HEADER_PRESETS[preset] if isinstance(preset, str) else preset
    extra_ctx = {"title": doc_title} if doc_title else {}
    if section_name:
        extra_ctx["section"] = section_name

    def header_callback(canvas, doc):
        canvas.saveState()
        page_width, page_height = canvas._pagesize
        left_margin = getattr(doc, "leftMargin", 0.7 * inch)
        right_margin = getattr(doc, "rightMargin", 0.7 * inch)
        top_margin = getattr(doc, "topMargin", 0.8 * inch)
        y = page_height - max(top_margin * 0.45, 0.35 * inch)
        rule_y = y - 4 if p.show_rule else None

        # Special case: dual_logo_header
        if p.name == "dual_logo_header":
            box_w = 0.55 * inch
            box_h = 0.24 * inch
            left_x = left_margin
            right_x = page_width - right_margin - box_w
            top_y = y + 2
            canvas.setStrokeColor(colors.HexColor("#6B7280"))
            canvas.rect(left_x, top_y - box_h, box_w, box_h, stroke=1, fill=0)
            canvas.rect(right_x, top_y - box_h, box_w, box_h, stroke=1, fill=0)
            canvas.setFont("Helvetica", 6)
            canvas.drawCentredString(left_x + box_w / 2.0, top_y - box_h + 6, "LOGO")
            canvas.drawCentredString(right_x + box_w / 2.0, top_y - box_h + 6, "LOGO")
            center_text = _format_running_text(p.center_text, canvas, doc, extra_ctx) or doc_title or ""
            _draw_running_band(
                canvas, page_width, left_margin, right_margin,
                y=y, left=None, center=center_text, right=None,
                font_name="Helvetica-Bold", font_size=p.font_size, rule_y=rule_y,
            )
        # Special case: nist_header
        elif p.name == "nist_header":
            canvas.setFillColor(colors.HexColor("#1F3A5F"))
            canvas.setFont("Helvetica-Bold", p.font_size)
            center_text = _format_running_text(p.center_text, canvas, doc, extra_ctx) or "NIST Special Publication"
            canvas.drawCentredString(page_width / 2.0, y, center_text)
            if p.show_rule:
                canvas.setStrokeColor(colors.HexColor("#1F3A5F"))
                canvas.setLineWidth(1)
                canvas.line(left_margin, y - 5, page_width - right_margin, y - 5)
        # Standard header
        else:
            left_text = _format_running_text(p.left_text, canvas, doc, extra_ctx)
            center_text = _format_running_text(p.center_text, canvas, doc, extra_ctx)
            right_text = _format_running_text(p.right_text, canvas, doc, extra_ctx)
            _draw_running_band(
                canvas, page_width, left_margin, right_margin,
                y=y, left=left_text, center=center_text, right=right_text,
                font_name="Helvetica", font_size=p.font_size, rule_y=rule_y,
            )
        canvas.restoreState()

    return header_callback


def build_footer_callback(
    preset: str | RunningFooterPreset = "page_number_footer",
    total_pages: int | None = None,
) -> Callable:
    """Build an onPage callback for footers.

    Args:
        preset: Preset name or RunningFooterPreset object
        total_pages: Optional total page count for %(total)s substitution

    Returns:
        Callback function: fn(canvas, doc)

    Special presets:
        - draft_footer: Draws large "DRAFT" watermark on page
    """
    p = RUNNING_FOOTER_PRESETS[preset] if isinstance(preset, str) else preset
    extra_ctx = {"total": total_pages} if total_pages else {}

    def footer_callback(canvas, doc):
        canvas.saveState()
        page_width, page_height = canvas._pagesize
        left_margin = getattr(doc, "leftMargin", 0.7 * inch)
        right_margin = getattr(doc, "rightMargin", 0.7 * inch)
        bottom_margin = getattr(doc, "bottomMargin", 0.7 * inch)
        y = max(bottom_margin * 0.45, 0.35 * inch)
        rule_y = y + 8 if p.show_rule else None

        # Special case: draft_footer draws watermark
        if p.name == "draft_footer":
            canvas.setFillColor(colors.Color(0.75, 0.75, 0.75, alpha=0.22))
            canvas.setFont("Helvetica-Bold", 38)
            canvas.drawCentredString(page_width / 2.0, page_height / 2.0, "DRAFT")
            canvas.setFillColor(colors.black)

        left_text = _format_running_text(p.left_text, canvas, doc, extra_ctx)
        center_text = _format_running_text(p.center_text, canvas, doc, extra_ctx)
        right_text = _format_running_text(p.right_text, canvas, doc, extra_ctx)
        _draw_running_band(
            canvas, page_width, left_margin, right_margin,
            y=y, left=left_text, center=center_text, right=right_text,
            font_name="Helvetica", font_size=p.font_size, rule_y=rule_y,
        )
        canvas.restoreState()

    return footer_callback


def build_combined_callback(
    header_preset: str | RunningHeaderPreset | None = None,
    footer_preset: str | RunningFooterPreset | None = None,
    doc_title: str | None = None,
    total_pages: int | None = None,
) -> Callable:
    """Build a combined onPage callback for both header and footer.

    Args:
        header_preset: Header preset name or object (None to skip header)
        footer_preset: Footer preset name or object (None to skip footer)
        doc_title: Document title for header substitution
        total_pages: Total page count for footer substitution

    Returns:
        Callback function: fn(canvas, doc)
    """
    header_fn = build_header_callback(header_preset, doc_title=doc_title) if header_preset else None
    footer_fn = build_footer_callback(footer_preset, total_pages=total_pages) if footer_preset else None

    def combined_callback(canvas, doc):
        if header_fn:
            header_fn(canvas, doc)
        if footer_fn:
            footer_fn(canvas, doc)

    return combined_callback


# ---------------------------------------------------------------------------
# Form elements (visual placeholders - actual AcroForm requires doc.acroForm)
# ---------------------------------------------------------------------------


class SignatureLine(Flowable):
    """A signature line flowable with label and date field."""

    def __init__(
        self,
        label: str = "Signature",
        width: float = 3 * inch,
        line_width: float = 0.75,
        show_date: bool = True,
        preset: str | FormElementPreset = "signature_line",
    ):
        Flowable.__init__(self)
        self.label = label
        self.sig_width = width
        self.line_width = line_width
        self.show_date = show_date
        p = FORM_ELEMENT_PRESETS[preset] if isinstance(preset, str) else preset
        self.preset = p
        self.width = width + (1.5 * inch if show_date else 0)
        self.height = p.field_height + 14

    def draw(self):
        canvas = self.canv
        canvas.setStrokeColor(self.preset.border_color)
        canvas.setLineWidth(self.line_width)

        # Signature line
        y_line = 10
        canvas.line(0, y_line, self.sig_width, y_line)

        # Label below line
        canvas.setFont(self.preset.label_font, self.preset.label_size)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawString(0, 0, self.label)

        # Date field
        if self.show_date:
            date_x = self.sig_width + 0.25 * inch
            canvas.line(date_x, y_line, date_x + 1.2 * inch, y_line)
            canvas.drawString(date_x, 0, "Date")


class CheckboxField(Flowable):
    """A checkbox field flowable with label."""

    def __init__(
        self,
        label: str,
        checked: bool = False,
        size: float = 10,
        preset: str | FormElementPreset = "checkbox_item",
    ):
        Flowable.__init__(self)
        self.label = label
        self.checked = checked
        self.size = size
        p = FORM_ELEMENT_PRESETS[preset] if isinstance(preset, str) else preset
        self.preset = p
        self.width = size + 6 + len(label) * 5  # Approximate width
        self.height = max(size, 12)

    def draw(self):
        canvas = self.canv
        canvas.setStrokeColor(self.preset.border_color)
        canvas.setLineWidth(self.preset.border_width)
        canvas.setFillColor(self.preset.fill_color)

        # Draw checkbox
        y_offset = (self.height - self.size) / 2
        canvas.rect(0, y_offset, self.size, self.size, fill=1)

        # Draw check mark if checked
        if self.checked:
            canvas.setStrokeColor(colors.HexColor("#1F2937"))
            canvas.setLineWidth(1.2)
            # Checkmark path
            canvas.line(2, y_offset + self.size/2, self.size/2 - 1, y_offset + 2)
            canvas.line(self.size/2 - 1, y_offset + 2, self.size - 2, y_offset + self.size - 2)

        # Label
        canvas.setFont(self.preset.label_font, self.preset.label_size)
        canvas.setFillColor(colors.black)
        canvas.drawString(self.size + 6, (self.height - self.preset.label_size) / 2, self.label)


class TextField(Flowable):
    """A text field placeholder flowable with label."""

    def __init__(
        self,
        label: str,
        width: float = 2 * inch,
        multiline: bool = False,
        preset: str | FormElementPreset = "labeled_field",
    ):
        Flowable.__init__(self)
        self.label = label
        self.field_width = width
        self.multiline = multiline
        p = FORM_ELEMENT_PRESETS[preset] if isinstance(preset, str) else preset
        self.preset = p
        self.width = width
        self.height = (p.field_height * 3 if multiline else p.field_height) + 12

    def draw(self):
        canvas = self.canv

        # Label
        canvas.setFont(self.preset.label_font, self.preset.label_size)
        canvas.setFillColor(colors.HexColor("#374151"))
        label_y = self.height - self.preset.label_size
        canvas.drawString(0, label_y, self.label)

        # Field box
        field_height = self.preset.field_height * 3 if self.multiline else self.preset.field_height
        canvas.setStrokeColor(self.preset.border_color)
        canvas.setLineWidth(self.preset.border_width)
        canvas.setFillColor(self.preset.fill_color)
        canvas.rect(0, 0, self.field_width, field_height, fill=1)


def build_signature_block(
    signers: Sequence[tuple[str, str]],
    preset: str | FormElementPreset = "signature_line",
) -> Table:
    """Build a signature block table with multiple signature lines.

    Args:
        signers: List of (role, name) tuples, e.g. [("Author", "John Doe"), ("Reviewer", "")]
        preset: Form element preset to use

    Returns:
        Table flowable with signature lines
    """
    rows = []
    for role, name in signers:
        sig_line = SignatureLine(label=f"{role}: {name}" if name else role, preset=preset)
        rows.append([sig_line])

    table = Table(rows, colWidths=[5 * inch])
    table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


# ---------------------------------------------------------------------------
# Visual elements
# ---------------------------------------------------------------------------


class SeverityBadge(Flowable):
    """A colored badge for severity/priority indicators."""

    SEVERITY_COLORS = {
        "critical": (colors.HexColor("#DC2626"), colors.white),      # Red
        "high": (colors.HexColor("#EA580C"), colors.white),          # Orange
        "medium": (colors.HexColor("#CA8A04"), colors.black),        # Yellow
        "low": (colors.HexColor("#16A34A"), colors.white),           # Green
        "info": (colors.HexColor("#2563EB"), colors.white),          # Blue
    }

    def __init__(
        self,
        text: str,
        severity: str = "medium",
        width: float | None = None,
        height: float = 14,
    ):
        Flowable.__init__(self)
        self.text = text
        self.severity = severity.lower()
        bg, fg = self.SEVERITY_COLORS.get(self.severity, (colors.HexColor("#6B7280"), colors.white))
        self.bg_color = bg
        self.fg_color = fg
        self.badge_height = height
        self.width = width or (len(text) * 5 + 12)
        self.height = height

    def draw(self):
        canvas = self.canv
        canvas.setFillColor(self.bg_color)
        canvas.roundRect(0, 0, self.width, self.height, 3, fill=1, stroke=0)

        canvas.setFont("Helvetica-Bold", 7)
        canvas.setFillColor(self.fg_color)
        text_width = canvas.stringWidth(self.text, "Helvetica-Bold", 7)
        x = (self.width - text_width) / 2
        y = (self.height - 7) / 2 + 1
        canvas.drawString(x, y, self.text)


class StatusPill(Flowable):
    """A pill-shaped status indicator."""

    STATUS_COLORS = {
        "implemented": (colors.HexColor("#DCFCE7"), colors.HexColor("#166534")),
        "partial": (colors.HexColor("#FEF3C7"), colors.HexColor("#92400E")),
        "planned": (colors.HexColor("#DBEAFE"), colors.HexColor("#1E40AF")),
        "not_implemented": (colors.HexColor("#FEE2E2"), colors.HexColor("#991B1B")),
        "open": (colors.HexColor("#F3F4F6"), colors.HexColor("#374151")),
        "closed": (colors.HexColor("#D1FAE5"), colors.HexColor("#065F46")),
        "in_progress": (colors.HexColor("#DBEAFE"), colors.HexColor("#1E40AF")),
        "blocked": (colors.HexColor("#FEE2E2"), colors.HexColor("#991B1B")),
    }

    def __init__(
        self,
        text: str,
        status: str = "open",
        width: float | None = None,
        height: float = 16,
    ):
        Flowable.__init__(self)
        self.text = text
        self.status = status.lower().replace(" ", "_")
        bg, fg = self.STATUS_COLORS.get(self.status, (colors.HexColor("#F3F4F6"), colors.HexColor("#374151")))
        self.bg_color = bg
        self.fg_color = fg
        self.pill_height = height
        self.width = width or (len(text) * 5 + 16)
        self.height = height

    def draw(self):
        canvas = self.canv
        radius = self.height / 2
        canvas.setFillColor(self.bg_color)
        canvas.roundRect(0, 0, self.width, self.height, radius, fill=1, stroke=0)

        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(self.fg_color)
        text_width = canvas.stringWidth(self.text, "Helvetica", 8)
        x = (self.width - text_width) / 2
        y = (self.height - 8) / 2 + 1
        canvas.drawString(x, y, self.text)


class ProgressBar(Flowable):
    """A horizontal progress bar."""

    def __init__(
        self,
        progress: float,
        width: float = 2 * inch,
        height: float = 12,
        show_label: bool = True,
        bar_color: Any = colors.HexColor("#2563EB"),
        track_color: Any = colors.HexColor("#E5E7EB"),
    ):
        Flowable.__init__(self)
        self.progress = max(0, min(1, progress))  # Clamp 0-1
        self.bar_width = width
        self.bar_height = height
        self.show_label = show_label
        self.bar_color = bar_color
        self.track_color = track_color
        self.width = width + (30 if show_label else 0)
        self.height = height

    def draw(self):
        canvas = self.canv
        radius = self.bar_height / 2

        # Track (background)
        canvas.setFillColor(self.track_color)
        canvas.roundRect(0, 0, self.bar_width, self.bar_height, radius, fill=1, stroke=0)

        # Progress fill
        if self.progress > 0:
            fill_width = max(self.bar_height, self.bar_width * self.progress)  # Min width for rounding
            canvas.setFillColor(self.bar_color)
            canvas.roundRect(0, 0, fill_width, self.bar_height, radius, fill=1, stroke=0)

        # Label
        if self.show_label:
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#374151"))
            label = f"{int(self.progress * 100)}%"
            canvas.drawString(self.bar_width + 6, (self.bar_height - 8) / 2 + 1, label)


class FigureFrame(Flowable):
    """A placeholder frame for figures/images with caption."""

    def __init__(
        self,
        width: float = 4 * inch,
        height: float = 3 * inch,
        caption: str | None = None,
        preset: str | VisualElementPreset = "figure_frame",
    ):
        Flowable.__init__(self)
        p = VISUAL_ELEMENT_PRESETS[preset] if isinstance(preset, str) else preset
        self.preset = p
        self.frame_width = width
        self.frame_height = height
        self.caption = caption
        self.width = width
        self.height = height + (16 if caption else 0)

    def draw(self):
        canvas = self.canv

        # Frame
        canvas.setStrokeColor(self.preset.border_color)
        canvas.setLineWidth(self.preset.border_width)
        canvas.setFillColor(self.preset.fill_color)

        frame_y = 16 if self.caption else 0
        canvas.rect(0, frame_y, self.frame_width, self.frame_height, fill=1)

        # Placeholder X
        canvas.setStrokeColor(colors.HexColor("#D1D5DB"))
        canvas.setLineWidth(0.5)
        canvas.line(0, frame_y, self.frame_width, frame_y + self.frame_height)
        canvas.line(0, frame_y + self.frame_height, self.frame_width, frame_y)

        # Caption
        if self.caption:
            canvas.setFont("Helvetica-Oblique", 8)
            canvas.setFillColor(colors.HexColor("#6B7280"))
            canvas.drawCentredString(self.frame_width / 2, 2, self.caption)


def build_image_figure(
    image_path: str,
    caption: str,
    number: str,
    max_width: float = 6.1 * inch,
    max_height: float = 3.5 * inch,
    preset: str | VisualElementPreset = "figure_frame",
) -> Flowable:
    """Build a figure with an actual image, framed and captioned.

    Args:
        image_path: Path to the image file (PNG, JPG, etc.)
        caption: Caption text for the figure
        number: Figure number (e.g., "1", "2.3")
        max_width: Maximum width for the image
        max_height: Maximum height for the image
        preset: Visual element preset for frame styling

    Returns:
        KeepTogether flowable containing framed image and caption
    """
    p = VISUAL_ELEMENT_PRESETS[preset] if isinstance(preset, str) else preset

    # Load image and calculate scaled dimensions
    reader = ImageReader(image_path)
    img_w, img_h = reader.getSize()
    scale = min(max_width / img_w, max_height / img_h, 1.0)  # Don't upscale
    scaled_w = img_w * scale
    scaled_h = img_h * scale

    img = Image(image_path, width=scaled_w, height=scaled_h)
    img.hAlign = "CENTER"

    # Wrap in a framed table
    frame_table = Table([[img]], colWidths=[max_width])
    frame_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), p.border_width, p.border_color),
        ("BACKGROUND", (0, 0), (-1, -1), p.fill_color),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))

    return KeepTogether([frame_table, build_caption(caption, number, preset="figure_caption")])
