#!/usr/bin/env python3
"""Validate executable PDF Lab creator-reviewer defect checks."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "pdf_oxide.pdf_lab.creator_reviewer_defects.v1"
RESULT_SCHEMA = "pdf_oxide.pdf_lab.creator_reviewer_defect_validation.v1"
SUPPORTED_DEFECT_CLASSES = {
    "REGION_LABEL_MISMATCH",
    "REGION_BBOX_MISMATCH",
    "TEXT_CONTENT_MISMATCH",
    "TABLE_FALSE_POSITIVE",
    "TABLE_CELL_TOP_LEVEL_LEAK",
}
SUPPORTED_EXPECTED_STATES = {"absent_top_level", "present"}
OWNER_VALUES = {"pdf_oxide_core", "nist_preset", "export_schema", "ui", "external_harness"}
IGNORED_TOP_LEVEL_TYPES = {"table"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(path: Path | str, *, bundle_path: Path | None = None) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate

    candidates = [Path.cwd() / candidate]
    if bundle_path is not None:
        candidates.append(bundle_path.parent / candidate)
    for resolved in candidates:
        if resolved.exists():
            return resolved.resolve()
    return candidates[0].resolve()


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def is_bbox(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(item, (int, float)) for item in value)
        and value[0] <= value[2]
        and value[1] <= value[3]
    )


def bbox_contains(outer: list[float], inner: list[float], *, tolerance: float = 0.003) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def bbox_area(bbox: list[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def bbox_iou(first: list[float], second: list[float]) -> float:
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[2], second[2])
    y1 = min(first[3], second[3])
    intersection = bbox_area([x0, y0, x1, y1])
    union = bbox_area(first) + bbox_area(second) - intersection
    if union <= 0:
        return 0.0
    return intersection / union


def bbox_matches_region(block_bbox: list[float], region_bbox: list[float]) -> bool:
    return (
        bbox_contains(region_bbox, block_bbox, tolerance=0.005)
        or bbox_contains(block_bbox, region_bbox, tolerance=0.005)
        or bbox_iou(block_bbox, region_bbox) >= 0.5
    )


def bbox_edges_close(
    block_bbox: list[float], region_bbox: list[float], *, tolerance: float = 0.012
) -> bool:
    return all(
        abs(float(actual) - float(expected)) <= tolerance
        for actual, expected in zip(block_bbox, region_bbox)
    )


def validate_bundle_shape(bundle: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if bundle.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if not isinstance(bundle.get("page"), int) or bundle["page"] < 1:
        errors.append("page must be a positive integer")
    evidence = bundle.get("evidence")
    if not isinstance(evidence, dict):
        errors.append("evidence must be an object")
    else:
        for key in ("page_image", "overlay_image", "extraction_json"):
            if not isinstance(evidence.get(key), str) or not evidence[key].strip():
                errors.append(f"evidence.{key} must be a non-empty string")
    checks = bundle.get("checks")
    if not isinstance(checks, list) or not checks:
        errors.append("checks must be a non-empty list")
        return errors
    for index, check in enumerate(checks):
        prefix = f"checks[{index}]"
        if not isinstance(check, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for key in (
            "id",
            "defect_class",
            "page",
            "region_bbox",
            "actual_label",
            "expected_label",
            "expected_state",
            "text",
            "owner",
            "proof_command",
        ):
            if key not in check:
                errors.append(f"{prefix}.{key} is required")
        if check.get("defect_class") not in SUPPORTED_DEFECT_CLASSES:
            errors.append(f"{prefix}.defect_class is unsupported")
        if check.get("expected_state") not in SUPPORTED_EXPECTED_STATES:
            errors.append(f"{prefix}.expected_state is unsupported")
        if check.get("owner") not in OWNER_VALUES:
            errors.append(f"{prefix}.owner is unsupported")
        if check.get("page") != bundle.get("page"):
            errors.append(f"{prefix}.page must match bundle page")
        if not is_bbox(check.get("region_bbox")):
            errors.append(f"{prefix}.region_bbox must be [x0, y0, x1, y1]")
        for key in ("id", "actual_label", "expected_label", "text", "proof_command"):
            if not isinstance(check.get(key), str) or not check[key].strip():
                errors.append(f"{prefix}.{key} must be a non-empty string")
        for key in ("expected_text", "forbidden_text"):
            if key in check and (not isinstance(check.get(key), str) or not check[key].strip()):
                errors.append(f"{prefix}.{key} must be a non-empty string")
        block_id = check.get("block_id")
        if block_id is not None and not isinstance(block_id, str):
            errors.append(f"{prefix}.block_id must be a string or null")
        if "bbox_tolerance" in check and not isinstance(check.get("bbox_tolerance"), (int, float)):
            errors.append(f"{prefix}.bbox_tolerance must be a number")
    return errors


def block_candidates_for_table_cell_leak(
    extraction: dict[str, Any],
    check: dict[str, Any],
) -> list[dict[str, Any]]:
    blocks = extraction.get("blocks") or extraction.get("elements") or []
    if not isinstance(blocks, list):
        return []

    text = normalize_text(check.get("text"))
    block_id = check.get("block_id")
    region_bbox = check["region_bbox"]
    tables = [
        block for block in blocks if block.get("type") == "table" and is_bbox(block.get("bbox"))
    ]

    candidates: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("type") in IGNORED_TOP_LEVEL_TYPES:
            continue
        block_bbox = block.get("bbox")
        if not is_bbox(block_bbox):
            continue
        block_text = normalize_text(block.get("text"))
        id_match = block_id is not None and block.get("id") == block_id
        text_match = bool(text) and block_text == text
        if not id_match and not text_match:
            continue
        if not bbox_contains(region_bbox, block_bbox) and not bbox_contains(
            block_bbox, region_bbox
        ):
            continue
        contained_by_table = any(bbox_contains(table["bbox"], block_bbox) for table in tables)
        table_has_text = any(text and text in normalize_text(table.get("text")) for table in tables)
        candidates.append(
            {
                "id": block.get("id"),
                "type": block.get("type"),
                "source_type": block.get("source_type"),
                "text": block_text,
                "bbox": block_bbox,
                "contained_by_table": contained_by_table,
                "table_has_text": table_has_text,
            }
        )
    return candidates


def block_candidates_for_region_label_mismatch(
    extraction: dict[str, Any],
    check: dict[str, Any],
) -> list[dict[str, Any]]:
    blocks = extraction.get("blocks") or extraction.get("elements") or []
    if not isinstance(blocks, list):
        return []

    text = normalize_text(check.get("text"))
    block_id = check.get("block_id")
    region_bbox = check["region_bbox"]
    expected_semantic_role = check.get("expected_semantic_role")

    candidates: list[dict[str, Any]] = []
    for block in blocks:
        block_bbox = block.get("bbox")
        if not is_bbox(block_bbox):
            continue
        block_text = normalize_text(block.get("text"))
        id_match = block_id is not None and block.get("id") == block_id
        text_match = bool(text) and block_text == text
        if not id_match and not text_match:
            continue
        if not bbox_matches_region(block_bbox, region_bbox):
            continue
        candidates.append(
            {
                "id": block.get("id"),
                "type": block.get("type"),
                "source_type": block.get("source_type"),
                "semantic_role": block.get("semantic_role"),
                "text": block_text,
                "bbox": block_bbox,
                "iou": bbox_iou(block_bbox, region_bbox),
                "matches_expected_label": block.get("type") == check["expected_label"],
                "matches_expected_semantic_role": (
                    expected_semantic_role is None
                    or block.get("semantic_role") == expected_semantic_role
                ),
            }
        )
    return candidates


def block_candidates_for_region_bbox_mismatch(
    extraction: dict[str, Any],
    check: dict[str, Any],
) -> list[dict[str, Any]]:
    blocks = extraction.get("blocks") or extraction.get("elements") or []
    if not isinstance(blocks, list):
        return []

    text = normalize_text(check.get("text"))
    block_id = check.get("block_id")
    region_bbox = check["region_bbox"]
    tolerance = float(check.get("bbox_tolerance") or 0.012)

    candidates: list[dict[str, Any]] = []
    for block in blocks:
        block_bbox = block.get("bbox")
        if not is_bbox(block_bbox):
            continue
        block_text = normalize_text(block.get("text"))
        id_match = block_id is not None and block.get("id") == block_id
        text_match = bool(text) and block_text == text
        if not id_match and not text_match:
            continue
        candidates.append(
            {
                "id": block.get("id"),
                "type": block.get("type"),
                "source_type": block.get("source_type"),
                "text": block_text,
                "bbox": block_bbox,
                "iou": bbox_iou(block_bbox, region_bbox),
                "matches_expected_label": block.get("type") == check["expected_label"],
                "matches_expected_bbox": bbox_edges_close(
                    block_bbox, region_bbox, tolerance=tolerance
                ),
            }
        )
    return candidates


def block_candidates_for_text_content_mismatch(
    extraction: dict[str, Any],
    check: dict[str, Any],
) -> list[dict[str, Any]]:
    blocks = extraction.get("blocks") or extraction.get("elements") or []
    if not isinstance(blocks, list):
        return []

    text = normalize_text(check.get("text"))
    expected_text = normalize_text(check.get("expected_text") or text)
    forbidden_text = normalize_text(check.get("forbidden_text"))
    block_id = check.get("block_id")
    region_bbox = check["region_bbox"]

    candidates: list[dict[str, Any]] = []
    for block in blocks:
        block_bbox = block.get("bbox")
        if not is_bbox(block_bbox):
            continue
        block_text = normalize_text(block.get("text"))
        id_match = block_id is not None and block.get("id") == block_id
        text_match = bool(text) and (text in block_text or block_text in text)
        if not id_match and not text_match:
            continue
        if not bbox_matches_region(block_bbox, region_bbox):
            continue
        candidates.append(
            {
                "id": block.get("id"),
                "type": block.get("type"),
                "source_type": block.get("source_type"),
                "text": block_text,
                "bbox": block_bbox,
                "iou": bbox_iou(block_bbox, region_bbox),
                "matches_expected_label": block.get("type") == check["expected_label"],
                "contains_expected_text": bool(expected_text) and expected_text in block_text,
                "contains_forbidden_text": bool(forbidden_text) and forbidden_text in block_text,
            }
        )
    return candidates


def block_candidates_for_table_false_positive(
    extraction: dict[str, Any],
    check: dict[str, Any],
) -> list[dict[str, Any]]:
    blocks = extraction.get("blocks") or extraction.get("elements") or []
    if not isinstance(blocks, list):
        return []

    text = normalize_text(check.get("text"))
    block_id = check.get("block_id")
    region_bbox = check["region_bbox"]

    candidates: list[dict[str, Any]] = []
    for block in blocks:
        if block.get("type") != "table":
            continue
        block_bbox = block.get("bbox")
        if not is_bbox(block_bbox):
            continue
        block_text = normalize_text(block.get("text"))
        id_match = block_id is not None and block.get("id") == block_id
        text_match = bool(text) and text in block_text
        if not id_match and not text_match:
            continue
        if not bbox_matches_region(block_bbox, region_bbox):
            continue
        raw = block.get("raw") if isinstance(block.get("raw"), dict) else {}
        candidates.append(
            {
                "id": block.get("id"),
                "type": block.get("type"),
                "source_type": block.get("source_type"),
                "text": block_text,
                "bbox": block_bbox,
                "iou": bbox_iou(block_bbox, region_bbox),
                "row_count": raw.get("row_count"),
                "column_count": raw.get("column_count"),
            }
        )
    return candidates


def evaluate_check(extraction: dict[str, Any], check: dict[str, Any]) -> dict[str, Any]:
    if check["defect_class"] == "REGION_LABEL_MISMATCH":
        candidates = block_candidates_for_region_label_mismatch(extraction, check)
        expected_state = check["expected_state"]
        label_matches = [
            candidate
            for candidate in candidates
            if candidate["matches_expected_label"] and candidate["matches_expected_semantic_role"]
        ]
        passed = bool(label_matches) if expected_state == "present" else not label_matches
        return {
            "id": check["id"],
            "defect_class": check["defect_class"],
            "status": "PASS" if passed else "FAIL",
            "expected_state": expected_state,
            "actual_label": check["actual_label"],
            "expected_label": check["expected_label"],
            "actual_semantic_role": check.get("actual_semantic_role"),
            "expected_semantic_role": check.get("expected_semantic_role"),
            "text": check["text"],
            "candidate_count": len(candidates),
            "matching_label_count": len(label_matches),
            "candidates": candidates,
        }

    if check["defect_class"] == "REGION_BBOX_MISMATCH":
        candidates = block_candidates_for_region_bbox_mismatch(extraction, check)
        expected_state = check["expected_state"]
        bbox_matches = [
            candidate
            for candidate in candidates
            if candidate["matches_expected_label"] and candidate["matches_expected_bbox"]
        ]
        passed = bool(bbox_matches) if expected_state == "present" else not bbox_matches
        return {
            "id": check["id"],
            "defect_class": check["defect_class"],
            "status": "PASS" if passed else "FAIL",
            "expected_state": expected_state,
            "actual_label": check["actual_label"],
            "expected_label": check["expected_label"],
            "text": check["text"],
            "candidate_count": len(candidates),
            "matching_bbox_count": len(bbox_matches),
            "candidates": candidates,
        }

    if check["defect_class"] == "TEXT_CONTENT_MISMATCH":
        candidates = block_candidates_for_text_content_mismatch(extraction, check)
        expected_state = check["expected_state"]
        text_matches = [
            candidate
            for candidate in candidates
            if candidate["matches_expected_label"]
            and candidate["contains_expected_text"]
            and not candidate["contains_forbidden_text"]
        ]
        passed = bool(text_matches) if expected_state == "present" else not text_matches
        return {
            "id": check["id"],
            "defect_class": check["defect_class"],
            "status": "PASS" if passed else "FAIL",
            "expected_state": expected_state,
            "actual_label": check["actual_label"],
            "expected_label": check["expected_label"],
            "text": check["text"],
            "expected_text": check.get("expected_text"),
            "forbidden_text": check.get("forbidden_text"),
            "candidate_count": len(candidates),
            "matching_text_count": len(text_matches),
            "candidates": candidates,
        }

    if check["defect_class"] == "TABLE_FALSE_POSITIVE":
        candidates = block_candidates_for_table_false_positive(extraction, check)
        expected_state = check["expected_state"]
        passed = not candidates if expected_state == "absent_top_level" else bool(candidates)
        return {
            "id": check["id"],
            "defect_class": check["defect_class"],
            "status": "PASS" if passed else "FAIL",
            "expected_state": expected_state,
            "actual_label": check["actual_label"],
            "expected_label": check["expected_label"],
            "text": check["text"],
            "candidate_count": len(candidates),
            "spurious_table_count": len(candidates),
            "candidates": candidates,
        }

    candidates = block_candidates_for_table_cell_leak(extraction, check)
    leaking_candidates = [
        candidate
        for candidate in candidates
        if candidate["contained_by_table"] and candidate["table_has_text"]
    ]
    expected_state = check["expected_state"]
    if expected_state == "absent_top_level":
        passed = not leaking_candidates
    else:
        passed = bool(leaking_candidates)
    return {
        "id": check["id"],
        "defect_class": check["defect_class"],
        "status": "PASS" if passed else "FAIL",
        "expected_state": expected_state,
        "actual_label": check["actual_label"],
        "expected_label": check["expected_label"],
        "text": check["text"],
        "candidate_count": len(candidates),
        "leaking_candidate_count": len(leaking_candidates),
        "candidates": candidates,
    }


def validate(bundle_path: Path, extraction_path: Path | None = None) -> dict[str, Any]:
    bundle = load_json(bundle_path)
    shape_errors = validate_bundle_shape(bundle)
    if shape_errors:
        return {
            "schema": RESULT_SCHEMA,
            "created_at": utc_now(),
            "status": "INVALID",
            "bundle": str(bundle_path),
            "extraction_json": str(extraction_path) if extraction_path else None,
            "errors": shape_errors,
            "checks": [],
        }

    evidence = bundle["evidence"]
    resolved_extraction_path = resolve_path(
        extraction_path or evidence["extraction_json"], bundle_path=bundle_path
    )
    if not resolved_extraction_path.exists():
        return {
            "schema": RESULT_SCHEMA,
            "created_at": utc_now(),
            "status": "INVALID",
            "bundle": str(bundle_path),
            "extraction_json": str(resolved_extraction_path),
            "errors": [f"extraction_json does not exist: {resolved_extraction_path}"],
            "checks": [],
        }

    extraction = load_json(resolved_extraction_path)
    check_results = [evaluate_check(extraction, check) for check in bundle["checks"]]
    failed = [check for check in check_results if check["status"] != "PASS"]
    return {
        "schema": RESULT_SCHEMA,
        "created_at": utc_now(),
        "status": "PASS" if not failed else "FAIL",
        "bundle": str(bundle_path),
        "extraction_json": str(resolved_extraction_path),
        "page": bundle["page"],
        "errors": [],
        "summary": {
            "check_count": len(check_results),
            "passed": len(check_results) - len(failed),
            "failed": len(failed),
        },
        "checks": check_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--extraction-json", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    result = validate(args.bundle, args.extraction_json)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
