#!/usr/bin/env python3
"""Deterministic validator for UX roundtable before-main artifacts."""

from __future__ import annotations

import copy
import json
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", category=DeprecationWarning)

from jsonschema import Draft202012Validator, FormatChecker, RefResolver
from jsonschema.exceptions import ValidationError


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
ARTIFACTS = ROOT / "artifacts" / "pdf-lab"
ROUNDTRIP = ROOT / "artifacts" / "ux_roundtable"
SCHEMA_STORE: dict[str, Any] = {}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def validator(schema_name: str) -> Draft202012Validator:
    path = CONTRACTS / schema_name
    schema = load_json(path)
    Draft202012Validator.check_schema(schema)
    if not SCHEMA_STORE:
        for schema_path in CONTRACTS.glob("*.schema.json"):
            candidate = load_json(schema_path)
            SCHEMA_STORE[schema_path.as_uri()] = candidate
            if isinstance(candidate.get("$id"), str):
                SCHEMA_STORE[candidate["$id"]] = candidate
    return Draft202012Validator(
        schema,
        resolver=RefResolver(path.as_uri(), schema, store=SCHEMA_STORE),
        format_checker=FormatChecker(),
    )


def assert_invalid(checker: Draft202012Validator, value: Any, label: str) -> None:
    try:
        checker.validate(value)
    except ValidationError:
        return
    raise AssertionError(f"{label} unexpectedly passed schema validation")


def main() -> None:
    schema_names = [
        "calibration_label_event_v1.schema.json",
        "annotation_decision_event_v1.schema.json",
        "annotation_queue_manifest_v1.schema.json",
        "ux_timing_event_v1.schema.json",
        "bbox_space_v1.schema.json",
        "document_mount_manifest_v1.schema.json",
        "page_image_manifest_v1.schema.json",
        "retrieval_answer_v1.schema.json",
    ]
    checkers = {name: validator(name) for name in schema_names}

    queue = load_json(ARTIFACTS / "annotation_queue_manifest_v1.json")
    checkers["annotation_queue_manifest_v1.schema.json"].validate(queue)
    assert queue["priority_order"] == [
        "char_parity_deficit",
        "reviewer_flagged",
        "low_confidence",
    ]
    assert queue["counts"] == {
        "total": 2161,
        "char_parity_deficit": 54,
        "reviewer_flagged": 5,
        "low_confidence": 2102,
    }
    assert len(queue["items"]) == 2161
    reasons = [item["reason"] for item in queue["items"]]
    assert reasons[:54] == ["char_parity_deficit"] * 54
    assert reasons[54:59] == ["reviewer_flagged"] * 5
    assert reasons[59:] == ["low_confidence"] * 2102

    mounts = load_json(ARTIFACTS / "document_mount_manifest_v1.json")
    checkers["document_mount_manifest_v1.schema.json"].validate(mounts)

    calibration_events = load_jsonl(ARTIFACTS / "calibration" / "events_v1.jsonl")
    for event in calibration_events:
        checkers["calibration_label_event_v1.schema.json"].validate(event)
    labels = load_jsonl(ARTIFACTS / "calibration" / "labels_v1.jsonl")
    assert len({row["item_sha"] for row in labels}) == len(labels)
    assert all(row["event_id"] for row in labels)

    annotation_events_path = ARTIFACTS / "annotation_decisions_v1.jsonl"
    for event in load_jsonl(annotation_events_path):
        checkers["annotation_decision_event_v1.schema.json"].validate(event)
    assert annotation_events_path.resolve() != (
        ARTIFACTS / "calibration" / "events_v1.jsonl"
    ).resolve()

    page_manifest_path = (
        ARTIFACTS / "page-image-cache" / "page_image_manifest_v1.jsonl"
    )
    for row in load_jsonl(page_manifest_path):
        checkers["page_image_manifest_v1.schema.json"].validate(row)

    retrieval = load_json(ARTIFACTS / "round4_retrieval_result.json")
    retrieval_checker = checkers["retrieval_answer_v1.schema.json"]
    retrieval_checker.validate(retrieval)
    assert retrieval["vector_provenance"] is None
    assert len(retrieval["evidence_groups"]) == 1
    assert len(retrieval["evidence_groups"][0]["evidence"]) == 2
    assert retrieval["evidence_groups"][0]["page_image"]["verified"] is True
    ranked = copy.deepcopy(retrieval)
    ranked["ranked_answers"] = ["first", "second"]
    assert_invalid(retrieval_checker, ranked, "ranked answer array")
    unverified = copy.deepcopy(retrieval)
    unverified["evidence_groups"][0]["page_image"]["verified"] = False
    assert_invalid(retrieval_checker, unverified, "unverified evidence image")

    timing_events = load_jsonl(ROUNDTRIP / "ux_timing_event_v1.jsonl")
    for event in timing_events:
        checkers["ux_timing_event_v1.schema.json"].validate(event)
    assert len(timing_events) == 100
    assert len({event["event_id"] for event in timing_events}) == 100
    receipt = load_json(ROUNDTRIP / "BM06_THROUGHPUT_RECEIPT.json")
    assert receipt["pass"] is True
    assert receipt["totals"] == {
        "decision_writes": 100,
        "timing_writes": 100,
        "duplicate_event_ids": 0,
        "dropped_writes": 0,
    }
    tiers = {row["kind"]: row for row in receipt["workloads"]}
    assert tiers["accept-defer"]["items_per_hour"] >= 120
    assert tiers["mixed"]["items_per_hour"] >= 60
    assert tiers["mixed"]["correction_count"] == 10

    print(
        json.dumps(
            {
                "schemas_checked": len(schema_names),
                "calibration_events": len(calibration_events),
                "annotation_decision_events": len(
                    load_jsonl(annotation_events_path)
                ),
                "queue_items": len(queue["items"]),
                "priority_counts": queue["counts"],
                "timing_events": len(timing_events),
                "retrieval_evidence_groups": len(
                    retrieval["evidence_groups"]
                ),
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
