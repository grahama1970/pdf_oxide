from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").split())


def _bbox_area(bbox: list[float]) -> float:
    if len(bbox) != 4:
        return 0.0
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_coverage(inner: list[float], outer: list[float]) -> float:
    inner_area = _bbox_area(inner)
    if inner_area <= 0.0 or len(inner) != 4 or len(outer) != 4:
        return 0.0
    x0 = max(float(inner[0]), float(outer[0]))
    y0 = max(float(inner[1]), float(outer[1]))
    x1 = min(float(inner[2]), float(outer[2]))
    y1 = min(float(inner[3]), float(outer[3]))
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return intersection / inner_area


def _bbox_axis_coverage(inner: list[float], outer: list[float], start: int, end: int) -> float:
    if len(inner) != 4 or len(outer) != 4:
        return 0.0
    inner_span = max(0.0, float(inner[end]) - float(inner[start]))
    if inner_span <= 0.0:
        return 0.0
    overlap = max(
        0.0,
        min(float(inner[end]), float(outer[end])) - max(float(inner[start]), float(outer[start])),
    )
    return overlap / inner_span


def _norm_bbox_block(bbox: Any, page_w: float, page_h: float) -> list[float]:
    if not bbox or len(bbox) != 4 or page_w <= 0 or page_h <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    x, y, width, height = [float(v) for v in bbox]
    x0 = x
    x1 = x + width
    y0 = page_h - y - height
    y1 = page_h - y
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    return [
        max(0.0, min(1.0, x0 / page_w)),
        max(0.0, min(1.0, y0 / page_h)),
        max(0.0, min(1.0, x1 / page_w)),
        max(0.0, min(1.0, y1 / page_h)),
    ]


def _table_metrics(table: dict[str, Any]) -> dict[str, Any]:
    data = table.get("data")
    if isinstance(data, list):
        return {
            "row_count": len(data),
            "column_count": max(
                (len(row) for row in data if isinstance(row, (list, tuple))),
                default=0,
            ),
        }

    rows = table.get("rows")
    if isinstance(rows, list):
        row_count = len(rows)
        column_count = max(
            (len(row) for row in rows if isinstance(row, (list, tuple))),
            default=0,
        )
    else:
        row_count = int(table.get("row_count") or table.get("rows_count") or 0)
        column_count = int(table.get("column_count") or table.get("columns_count") or 0)
    return {
        "row_count": row_count,
        "column_count": column_count,
    }


def _norm_bbox_corners(bbox: Any, page_w: float, page_h: float) -> list[float]:
    if not bbox or len(bbox) != 4 or page_w <= 0 or page_h <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    x0, y0, x1, y1 = [float(v) for v in bbox]
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    return [
        max(0.0, min(1.0, x0 / page_w)),
        max(0.0, min(1.0, y0 / page_h)),
        max(0.0, min(1.0, x1 / page_w)),
        max(0.0, min(1.0, y1 / page_h)),
    ]


def _norm_bbox_corners_unclamped(bbox: Any, page_w: float, page_h: float) -> list[float]:
    if not bbox or len(bbox) != 4 or page_w <= 0 or page_h <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    x0, y0, x1, y1 = [float(v) for v in bbox]
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    return [x0 / page_w, y0 / page_h, x1 / page_w, y1 / page_h]


def _off_page_extent(full_bbox: list[float]) -> dict[str, float]:
    if len(full_bbox) != 4:
        return {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}
    return {
        "left": round(max(0.0, -float(full_bbox[0])), 6),
        "top": round(max(0.0, -float(full_bbox[1])), 6),
        "right": round(max(0.0, float(full_bbox[2]) - 1.0), 6),
        "bottom": round(max(0.0, float(full_bbox[3]) - 1.0), 6),
    }


def _table_geometry_metadata(raw_bbox: Any, visible_bbox: list[float], page_w: float, page_h: float) -> dict[str, Any]:
    raw_values = _bbox_values(raw_bbox)
    full_bbox = _norm_bbox_corners_unclamped(raw_bbox, page_w, page_h)
    off_page_extent = _off_page_extent(full_bbox)
    return {
        "raw_bbox": raw_values,
        "visible_bbox": visible_bbox,
        "full_normalized_bbox": full_bbox,
        "bbox_clipped_to_page": any(value > 0.0 for value in off_page_extent.values()),
        "off_page_extent": off_page_extent,
    }


def _bbox_union(boxes: list[list[float]]) -> list[float]:
    if not boxes:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _extract_fitz_text_lines(pdf_path: Path, page_index: int, page_w: float, page_h: float) -> list[dict[str, Any]]:
    try:
        import fitz  # noqa: PLC0415
    except Exception:
        return []

    try:
        with fitz.open(pdf_path) as doc:
            page = doc[page_index]
            raw = page.get_text("rawdict")
    except Exception:
        return []

    lines: list[dict[str, Any]] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            chars: list[dict[str, Any]] = []
            fonts: list[str] = []
            sizes: list[float] = []
            for span in line.get("spans", []):
                if span.get("font"):
                    fonts.append(str(span.get("font")))
                if span.get("size") is not None:
                    sizes.append(float(span.get("size")))
                chars.extend(char for char in span.get("chars", []) if isinstance(char, dict))
            text = "".join(str(char.get("c") or "") for char in chars)
            if not _normalize_text(text):
                continue
            nonspace = [char for char in chars if str(char.get("c") or "").strip() and char.get("bbox")]
            if not nonspace:
                continue
            x0 = min(float(char["bbox"][0]) for char in nonspace)
            y0 = min(float(char["bbox"][1]) for char in nonspace)
            x1 = max(float(char["bbox"][2]) for char in nonspace)
            y1 = max(float(char["bbox"][3]) for char in nonspace)
            lines.append(
                {
                    "text": _normalize_text(text),
                    "bbox": _norm_bbox_corners([x0, y0, x1, y1], page_w, page_h),
                    "raw_bbox": [x0, y0, x1, y1],
                    "dir": list(line.get("dir") or []),
                    "font_name": fonts[0] if fonts else None,
                    "font_size": sizes[0] if sizes else None,
                    "is_bold": any("bold" in font.lower() for font in fonts),
                }
            )
    return lines


def _match_text_lines(block_text: str, text_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target = _normalize_text(block_text)
    if not target:
        return []
    for start in range(len(text_lines)):
        parts: list[str] = []
        matched: list[dict[str, Any]] = []
        for line in text_lines[start:]:
            parts.append(line["text"])
            matched.append(line)
            joined = _normalize_text(" ".join(parts))
            if joined == target:
                return matched
            if len(joined) > len(target) + 8 and not target.startswith(joined):
                break
    return []


def _should_split_block(block_bbox: list[float], matched_lines: list[dict[str, Any]]) -> bool:
    if len(matched_lines) <= 1:
        return False
    line_boxes = [line["bbox"] for line in matched_lines]
    union = _bbox_union(line_boxes)
    if not _bbox_area(union):
        return False
    vertical_gaps = [
        max(0.0, float(next_line["bbox"][1]) - float(prev_line["bbox"][3]))
        for prev_line, next_line in zip(matched_lines, matched_lines[1:])
    ]
    return _bbox_area(block_bbox) > _bbox_area(union) * 1.35 or any(gap > 0.018 for gap in vertical_gaps)


def _block_elements(
    *,
    block: dict[str, Any],
    block_index: int,
    page_index: int,
    page_w: float,
    page_h: float,
    text_lines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    text = str(block.get("text") or "").strip()
    source_type = str(block.get("block_type") or "unknown")
    original_block_bbox = _norm_bbox_block(block.get("bbox"), page_w, page_h)
    block_bbox = original_block_bbox
    matched_lines = _match_text_lines(text, text_lines)
    if matched_lines:
        block_bbox = _bbox_union([line["bbox"] for line in matched_lines])

    base = {
        "page": page_index + 1,
        "pdf_page_index": page_index,
        "type": "unknown_region",
        "source_type": source_type,
        "font_size": block.get("font_size"),
        "font_name": block.get("font_name"),
        "is_bold": block.get("is_bold"),
        "raw": block,
    }
    if not _should_split_block(original_block_bbox, matched_lines):
        return [
            {
                **base,
                "id": f"actual:p{page_index + 1}:block:{block_index}",
                "bbox": block_bbox,
                "text": text,
            }
        ]

    elements: list[dict[str, Any]] = []
    for line_index, line in enumerate(matched_lines):
        elements.append(
            {
                **base,
                "id": f"actual:p{page_index + 1}:block:{block_index}:line:{line_index}",
                "bbox": line["bbox"],
                "text": line["text"],
                "font_size": line.get("font_size", block.get("font_size")),
                "font_name": line.get("font_name", block.get("font_name")),
                "is_bold": line.get("is_bold", block.get("is_bold")),
                "raw": {
                    **block,
                    "parent_bbox": block.get("bbox"),
                    "line_bbox": line.get("raw_bbox"),
                    "line_text": line["text"],
                },
            }
        )
    return elements


def _is_vertical_margin_line(line: dict[str, Any]) -> bool:
    bbox = line.get("bbox")
    direction = line.get("dir")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    if not isinstance(direction, list) or len(direction) != 2:
        return False
    try:
        dx, dy = [float(value) for value in direction]
    except (TypeError, ValueError):
        return False
    return abs(dy) > abs(dx) and (float(bbox[2]) <= 0.12 or float(bbox[0]) >= 0.88)


def _consolidate_rotated_side_chrome_fragments(
    raw_elements: list[dict[str, Any]],
    text_lines: list[dict[str, Any]],
    page_index: int,
) -> list[dict[str, Any]]:
    """Collapse classifier fragments that PyMuPDF exposes as one rotated margin line."""
    replacements: dict[int, dict[str, Any]] = {}
    consumed: set[int] = set()
    replacement_index = 0

    for line in text_lines:
        if not _is_vertical_margin_line(line):
            continue
        line_text = _normalize_text(line.get("text") or "")
        if not line_text:
            continue
        matching_indexes: list[int] = []
        for index, element in enumerate(raw_elements):
            if index in consumed:
                continue
            text = _normalize_text(element.get("text") or "")
            if not text or text not in line_text:
                continue
            bbox = element.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            if not (float(bbox[0]) <= 0.12 or float(bbox[2]) >= 0.88):
                continue
            matching_indexes.append(index)

        if not matching_indexes:
            continue
        first_index = matching_indexes[0]
        fragments = [raw_elements[index] for index in matching_indexes]
        consumed.update(matching_indexes)
        replacement_index += 1
        replacements[first_index] = {
            "id": f"actual:p{page_index + 1}:rotated_side_chrome:{replacement_index}",
            "page": page_index + 1,
            "pdf_page_index": page_index,
            "type": "header_footer_noise",
            "source_type": "RotatedSideChrome",
            "bbox": line["bbox"],
            "text": line_text,
            "font_size": line.get("font_size"),
            "font_name": line.get("font_name"),
            "is_bold": line.get("is_bold"),
            "raw": {
                "source": "fitz_rawdict_rotated_margin_line",
                "line_bbox": line.get("raw_bbox"),
                "line_dir": line.get("dir"),
                "fragment_ids": [fragment.get("id") for fragment in fragments],
                "fragments": fragments,
            },
        }

    if not replacements:
        return raw_elements

    consolidated: list[dict[str, Any]] = []
    for index, element in enumerate(raw_elements):
        if index in replacements:
            consolidated.append(replacements[index])
        elif index not in consumed:
            consolidated.append(element)
    return consolidated


def _table_bbox(table: dict[str, Any], page_w: float, page_h: float) -> list[float]:
    bbox = table.get("bbox") or table.get("bounding_box") or table.get("rect")
    if isinstance(bbox, dict):
        bbox = [
            bbox.get("x0", bbox.get("left", 0.0)),
            bbox.get("y0", bbox.get("top", 0.0)),
            bbox.get("x1", bbox.get("right", 0.0)),
            bbox.get("y1", bbox.get("bottom", 0.0)),
        ]
    if not bbox or len(bbox) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    if "data" in table or "flavor" in table:
        return _norm_bbox_corners(bbox, page_w, page_h)
    return _norm_bbox_block(bbox, page_w, page_h)


def _table_raw_bbox(table: dict[str, Any]) -> list[float]:
    bbox = table.get("bbox") or table.get("bounding_box") or table.get("rect")
    if isinstance(bbox, dict):
        bbox = [
            bbox.get("x0", bbox.get("left", 0.0)),
            bbox.get("y0", bbox.get("top", 0.0)),
            bbox.get("x1", bbox.get("right", 0.0)),
            bbox.get("y1", bbox.get("bottom", 0.0)),
        ]
    return _bbox_values(bbox)


def _bbox_values(bbox: Any) -> list[float]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return []
    try:
        return [float(v) for v in bbox]
    except (TypeError, ValueError):
        return []


def _clean_table_cell(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


_QID_MARKER_RE = re.compile(r"\[QID_[^\]]+\]", re.IGNORECASE)


def _qid_marker_count(text: str) -> int:
    return len(_QID_MARKER_RE.findall(text or ""))


def _qid_cells_from_row_text(text: str, expected_columns: int) -> list[str]:
    if expected_columns <= 0:
        return []
    matches = list(_QID_MARKER_RE.finditer(text or ""))
    if len(matches) != expected_columns:
        return []
    cells: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        cell = _clean_table_cell(text[match.start():end])
        if not cell:
            return []
        cells.append(cell)
    return cells


def _row_blocks_inside_table(
    raw_elements: list[dict[str, Any]],
    table_bbox: list[float],
    page_number: int,
    expected_columns: int,
) -> list[tuple[float, list[str]]]:
    rows: list[tuple[float, list[str]]] = []
    for element in raw_elements:
        if element.get("type") == "table" or element.get("page") != page_number:
            continue
        bbox = element.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        text = str(element.get("text") or "")
        cells = _qid_cells_from_row_text(text, expected_columns)
        if not cells:
            continue
        covered = _bbox_coverage(bbox, table_bbox) >= 0.9 or (
            _bbox_axis_coverage(bbox, table_bbox, 1, 3) >= 0.9
            and _bbox_axis_coverage(table_bbox, bbox, 0, 2) >= 0.9
        )
        if covered:
            rows.append(((float(bbox[1]) + float(bbox[3])) / 2.0, cells))
    rows.sort(key=lambda row: row[0])
    return rows


def _repair_table_with_qid_rows(
    table: dict[str, Any],
    raw_elements: list[dict[str, Any]],
    table_bbox: list[float],
    page_number: int,
) -> dict[str, Any]:
    metrics = _table_metrics(table)
    expected_columns = int(metrics.get("column_count") or 0)
    expected_rows = int(metrics.get("row_count") or 0)
    qid_rows = _row_blocks_inside_table(raw_elements, table_bbox, page_number, expected_columns)
    if expected_columns <= 0 or not qid_rows:
        return table
    if expected_rows and len(qid_rows) != expected_rows:
        return table
    repaired = dict(table)
    repaired["data"] = [cells for _, cells in qid_rows]
    return repaired


def _suppress_qid_table_row_duplicates(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tables = [
        element
        for element in elements
        if element.get("type") == "table" and isinstance(element.get("bbox"), list)
    ]
    if not tables:
        return elements

    out: list[dict[str, Any]] = []
    for element in elements:
        if element.get("type") == "table":
            out.append(element)
            continue
        bbox = element.get("bbox")
        text = str(element.get("text") or "")
        if not isinstance(bbox, list) or len(bbox) != 4 or _qid_marker_count(text) < 2:
            out.append(element)
            continue
        covered = any(
            table.get("page") == element.get("page")
            and (
                _bbox_coverage(bbox, table.get("bbox") or []) >= 0.9
                or (
                    _bbox_axis_coverage(bbox, table.get("bbox") or [], 1, 3) >= 0.9
                    and _bbox_axis_coverage(table.get("bbox") or [], bbox, 0, 2) >= 0.9
                )
            )
            for table in tables
        )
        if not covered:
            out.append(element)
    return out


def _table_data(table: dict[str, Any]) -> list[list[str]]:
    data = table.get("data")
    if isinstance(data, list):
        rows: list[list[str]] = []
        for row in data:
            if isinstance(row, (list, tuple)):
                rows.append([_clean_table_cell(cell) for cell in row])
        return rows

    rows = table.get("rows")
    if not isinstance(rows, list):
        return []
    out: list[list[str]] = []
    for row in rows:
        cells = row.get("cells") if isinstance(row, dict) else row
        if not isinstance(cells, list):
            continue
        values: list[str] = []
        for cell in cells:
            text = cell.get("text") if isinstance(cell, dict) else cell
            values.append(_clean_table_cell(text))
        out.append(values)
    return out


def _table_text(table: dict[str, Any]) -> str:
    rows = _table_data(table)
    if rows:
        lines = [
            " | ".join(cell for cell in row).strip()
            for row in rows
        ]
        return "\n".join(line for line in lines if line)
    explicit = table.get("text") or table.get("markdown")
    if explicit:
        return str(explicit)
    return ""


def _raw_table_payload(table: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    raw = {**table, **metrics}
    data = _table_data(table)
    if data:
        raw["rows"] = [
            {"cells": [{"text": cell} for cell in row]}
            for row in data
        ]
    return raw


def _is_tiny_empty_table_false_positive(table: dict[str, Any], metrics: dict[str, Any], bbox: list[float]) -> bool:
    row_count = int(metrics.get("row_count") or 0)
    column_count = int(metrics.get("column_count") or 0)
    whitespace = float(table.get("whitespace") or 0.0)
    if row_count > 3 or column_count > 3 or whitespace < 95.0:
        return False
    if _bbox_area(bbox) > 0.0025:
        return False
    text = _table_text(table)
    return not text.replace("|", "").strip()


def _extract_tables_for_snapshot(doc: Any, page_index: int) -> list[dict[str, Any]]:
    """Use the shared Camelot-style extractor, falling back to the legacy API."""
    try:
        tables = doc.read_pdf(pages=str(page_index + 1), flavor="auto") or []
        tables = [table for table in tables if isinstance(table, dict)]
        if tables:
            return tables
    except Exception:
        pass

    try:
        return [
            table if isinstance(table, dict) else {"raw_value": table}
            for table in (doc.extract_tables(page_index) or [])
        ]
    except Exception:
        return []


def _extract_page(pdf_path: Path, page_index: int, ledger_path: Path | None, apply_mode: str) -> dict[str, Any]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
    import pdf_oxide  # noqa: PLC0415

    doc = pdf_oxide.open(str(pdf_path))
    page_w, page_h = doc.page_dimensions(page_index)
    text_lines = _extract_fitz_text_lines(pdf_path, page_index, page_w, page_h)
    raw_elements: list[dict[str, Any]] = []

    for index, block in enumerate(doc.classify_blocks(page_index) or []):
        if not isinstance(block, dict):
            continue
        raw_elements.extend(
            _block_elements(
                block=block,
                block_index=index,
                page_index=page_index,
                page_w=page_w,
                page_h=page_h,
                text_lines=text_lines,
            )
        )
    raw_elements = _consolidate_rotated_side_chrome_fragments(raw_elements, text_lines, page_index)

    for index, table in enumerate(_extract_tables_for_snapshot(doc, page_index)):
        metrics = _table_metrics(table)
        bbox = _table_bbox(table, page_w, page_h)
        raw_bbox = _table_raw_bbox(table)
        table_geometry = _table_geometry_metadata(raw_bbox, bbox, page_w, page_h)
        if _is_tiny_empty_table_false_positive(table, metrics, bbox):
            continue
        table = _repair_table_with_qid_rows(table, raw_elements, bbox, page_index + 1)
        metrics = _table_metrics(table)
        raw_elements.append(
            {
                "id": f"actual:p{page_index + 1}:table:{index}",
                "page": page_index + 1,
                "pdf_page_index": page_index,
                "type": "table",
                "source_type": "table",
                "bbox": bbox,
                "text": _table_text(table),
                "table_geometry": table_geometry,
                "raw": {**_raw_table_payload(table, metrics), "table_geometry": table_geometry},
            }
        )

    raw_elements = _suppress_qid_table_row_duplicates(raw_elements)

    if ledger_path and ledger_path.exists():
        from pdf_oxide.presets.applier import ApplierConfig, apply_ledger  # noqa: PLC0415

        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        blocks = apply_ledger(raw_elements, ledger, ApplierConfig(mode=apply_mode))
        ledger_used = str(ledger_path)
    else:
        blocks = raw_elements
        ledger_used = None

    return {
        "page": page_index + 1,
        "pdf_page_index": page_index,
        "page_dimensions_pts": [page_w, page_h],
        "ledger_path": ledger_used,
        "apply_mode": apply_mode,
        "blocks": blocks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--apply-mode", default="release")
    parser.add_argument("--max-pages", required=True, type=int)
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args()

    page_index = max(0, args.max_pages - 1)
    payload = {
        "schema": "pdf_lab.current_extraction_snapshot.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pdf_path": str(args.pdf),
        "pages": [_extract_page(args.pdf, page_index, args.ledger, args.apply_mode)],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
