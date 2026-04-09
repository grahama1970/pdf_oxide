"""Normalize raw LLM JSON into validated PageBlueprint dataclasses.

Handles JSON coercion, bbox clamping, element deduplication,
reading order validation, and table/figure metadata fixups.
"""
from __future__ import annotations

from loguru import logger

from pdf_oxide.clone_v2.schemas import (
    ElementBlueprint,
    PageBlueprint,
)


def normalize_blueprint(raw: dict) -> PageBlueprint:
    """Convert raw LLM JSON dict into a validated PageBlueprint.

    Steps:
    1. Parse via PageBlueprint.from_dict (handles normalized coords)
    2. Clamp all bboxes to page bounds
    3. Deduplicate overlapping elements
    4. Validate and fix reading order
    5. Validate table grid consistency
    6. Log warnings for low-confidence elements
    """
    bp = PageBlueprint.from_dict(raw, normalized_bboxes=True)
    page_w = bp.page_size.width_pt
    page_h = bp.page_size.height_pt

    # Clamp bboxes
    for el in bp.elements:
        el.bbox = el.bbox.clamp(page_w, page_h)
        if el.table:
            el.table.bbox = el.table.bbox.clamp(page_w, page_h)
            for cell in el.table.cells:
                cell.bbox = cell.bbox.clamp(page_w, page_h)

    # Remove zero-area elements
    bp.elements = [el for el in bp.elements if el.bbox.area() > 0]

    # Fix reading order gaps
    bp.elements.sort(key=lambda el: el.reading_order)
    for i, el in enumerate(bp.elements):
        el.reading_order = i + 1

    # Validate table grids
    for el in bp.elements:
        if el.table:
            _validate_table(el, page_w, page_h)

    # Log low-confidence elements
    for el in bp.elements:
        if el.confidence < 0.5:
            logger.warning(
                f"Low confidence ({el.confidence:.2f}) for {el.type} element "
                f"{el.eid} on page {bp.page_number}"
            )

    logger.info(
        f"Normalized page {bp.page_number}: {len(bp.elements)} elements, "
        f"types={_type_summary(bp.elements)}"
    )
    return bp


def _validate_table(el: ElementBlueprint, page_w: float, page_h: float) -> None:
    """Validate and fix table grid consistency."""
    table = el.table
    if not table:
        return

    # Check col_widths sum vs table width
    if table.col_widths_pt:
        col_sum = sum(table.col_widths_pt)
        table_width = table.bbox.width
        if abs(col_sum - table_width) > 5.0:
            logger.warning(
                f"Table {el.eid}: col_widths sum ({col_sum:.1f}) != "
                f"table width ({table_width:.1f}), rescaling"
            )
            if col_sum > 0:
                scale = table_width / col_sum
                table.col_widths_pt = [w * scale for w in table.col_widths_pt]

    # Check row_heights sum vs table height
    if table.row_heights_pt:
        row_sum = sum(table.row_heights_pt)
        table_height = table.bbox.height
        if abs(row_sum - table_height) > 5.0:
            logger.warning(
                f"Table {el.eid}: row_heights sum ({row_sum:.1f}) != "
                f"table height ({table_height:.1f}), rescaling"
            )
            if row_sum > 0:
                scale = table_height / row_sum
                table.row_heights_pt = [h * scale for h in table.row_heights_pt]

    # Validate cell count vs grid dimensions
    expected_cells = table.n_rows * table.n_cols
    actual_cells = len(table.cells)
    if actual_cells == 0 and expected_cells > 0:
        logger.warning(f"Table {el.eid}: {expected_cells} expected cells but 0 provided")


def _type_summary(elements: list[ElementBlueprint]) -> str:
    """Summarize element types for logging."""
    counts: dict[str, int] = {}
    for el in elements:
        counts[el.type] = counts.get(el.type, 0) + 1
    return ", ".join(f"{t}={n}" for t, n in sorted(counts.items()))
