"""ReportLab preset system for PDF generation.

This module provides 136 presets across 11 categories for generating
consistent, professional PDF documents. Used by the clone pipeline to
regenerate extracted content.

Categories:
    - tables: 36 presets (data_grid, requirements_matrix, ledger, etc.)
    - page_templates: 12 presets (standard_page, cover_page, landscape, etc.)
    - running_headers: 8 presets (doc_title_header, nist_header, etc.)
    - running_footers: 8 presets (page_number_footer, draft_footer, etc.)
    - callout_blocks: 12 presets (note_box, warning_box, danger_box, etc.)
    - caption_styles: 6 presets (figure_caption, table_caption, etc.)
    - list_styles: 10 presets (numbered_list, bullet_list, etc.)
    - form_elements: 10 presets (signature_line, checkbox_item, etc.)
    - document_blocks: 14 presets (doc_info_block, executive_summary, etc.)
    - compliance_specific: 12 presets (control_description, assessment_finding, etc.)
    - visual_elements: 8 presets (figure_frame, severity_indicator, etc.)

Usage:
    from pdf_oxide.presets import build_table, TableSpec, SeverityBadge

    spec = TableSpec(
        headers=["ID", "Requirement", "Status"],
        rows=[["REQ-001", "Preserve provenance", "Implemented"]],
    )
    table = build_table(spec, preset="requirements_matrix")

Type aliases (for static type checking):
    ColorLike: Union[Color, HexColor, None]
    TitleMode: Literal["external", "inline", "inline_continued"]
    Alignment: Literal["LEFT", "CENTER", "RIGHT", "MIDDLE", "TOP", "BOTTOM"]
    BulletType: Literal["1", "a", "A", "i", "I", "bullet", "-", "square"]
"""

from pdf_oxide.presets.tables import (
    # Type aliases
    ColorLike,
    TitleMode,
    Alignment,
    BulletType,
    PresetName,
    # Dataclasses (frozen/immutable)
    TablePreset,
    TableSpec,
    PageTemplatePreset,
    RunningHeaderPreset,
    RunningFooterPreset,
    BoxPreset,
    CaptionPreset,
    ListPreset,
    FormElementPreset,
    DocumentBlockPreset,
    CompliancePreset,
    VisualElementPreset,
    # Registries
    TABLE_PRESETS,
    PAGE_TEMPLATE_PRESETS,
    RUNNING_HEADER_PRESETS,
    RUNNING_FOOTER_PRESETS,
    CALLOUT_PRESETS,
    CAPTION_PRESETS,
    LIST_PRESETS,
    FORM_ELEMENT_PRESETS,
    DOCUMENT_BLOCK_PRESETS,
    COMPLIANCE_PRESETS,
    VISUAL_ELEMENT_PRESETS,
    PRESET_CATEGORIES,
    # Validation
    VALID_TITLE_MODES,
    VALID_ALIGNMENTS,
    VALID_BULLET_TYPES,
    # Table rendering
    build_table,
    build_table_data,
    build_table_from_dataframe,
    build_base_style,
    add_section_row,
    add_total_row,
    # List rendering
    build_list,
    build_nested_list,
    BULLET_TYPE_MAP,
    # Page templates and callbacks
    build_page_template,
    build_header_callback,
    build_footer_callback,
    build_combined_callback,
    # Callouts and blocks
    build_callout,
    build_document_block,
    build_compliance_block,
    build_caption,
    # Form elements
    SignatureLine,
    CheckboxField,
    TextField,
    build_signature_block,
    # Visual elements
    SeverityBadge,
    StatusPill,
    ProgressBar,
    FigureFrame,
    build_image_figure,
    # Utilities
    registry_summary,
    list_preset_names,
    html_text,
    default_formatter,
    # Errors
    TablePresetError,
    PresetValidationError,
)

__all__ = [
    # Type aliases
    "ColorLike",
    "TitleMode",
    "Alignment",
    "BulletType",
    "PresetName",
    # Dataclasses (frozen/immutable)
    "TablePreset",
    "TableSpec",
    "PageTemplatePreset",
    "RunningHeaderPreset",
    "RunningFooterPreset",
    "BoxPreset",
    "CaptionPreset",
    "ListPreset",
    "FormElementPreset",
    "DocumentBlockPreset",
    "CompliancePreset",
    "VisualElementPreset",
    # Registries
    "TABLE_PRESETS",
    "PAGE_TEMPLATE_PRESETS",
    "RUNNING_HEADER_PRESETS",
    "RUNNING_FOOTER_PRESETS",
    "CALLOUT_PRESETS",
    "CAPTION_PRESETS",
    "LIST_PRESETS",
    "FORM_ELEMENT_PRESETS",
    "DOCUMENT_BLOCK_PRESETS",
    "COMPLIANCE_PRESETS",
    "VISUAL_ELEMENT_PRESETS",
    "PRESET_CATEGORIES",
    # Validation constants
    "VALID_TITLE_MODES",
    "VALID_ALIGNMENTS",
    "VALID_BULLET_TYPES",
    # Table rendering
    "build_table",
    "build_table_data",
    "build_table_from_dataframe",
    "build_base_style",
    "add_section_row",
    "add_total_row",
    # List rendering
    "build_list",
    "build_nested_list",
    "BULLET_TYPE_MAP",
    # Page templates and callbacks
    "build_page_template",
    "build_header_callback",
    "build_footer_callback",
    "build_combined_callback",
    # Callouts and blocks
    "build_callout",
    "build_document_block",
    "build_compliance_block",
    "build_caption",
    # Form elements
    "SignatureLine",
    "CheckboxField",
    "TextField",
    "build_signature_block",
    # Visual elements
    "SeverityBadge",
    "StatusPill",
    "ProgressBar",
    "FigureFrame",
    "build_image_figure",
    # Utilities
    "registry_summary",
    "list_preset_names",
    "html_text",
    "default_formatter",
    # Errors
    "TablePresetError",
    "PresetValidationError",
]
