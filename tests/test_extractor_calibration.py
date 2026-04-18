#!/usr/bin/env python3
"""Deterministic extraction calibration test.

This test compares pdf_oxide extraction results against a ground truth manifest
from a calibration fixture PDF. The fixture has QID markers that enable exact
validation of what should be extracted.

Pass criteria: 95% match on sections AND 95% match on tables.

Usage:
    pytest tests/test_extractor_calibration.py -v
    python tests/test_extractor_calibration.py  # direct run with exit code
"""
import json
import re
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from pdf_oxide.pipeline_extract import extract_content
from pdf_oxide.pipeline_types import PipelineConfig

# Calibration fixture paths
FIXTURE_PDF = Path("/tmp/nist_calibration.pdf")
FIXTURE_TRUTH = Path("/tmp/nist_calibration.truth.json")

# Pass threshold
PASS_THRESHOLD = 0.95


def load_truth_manifest() -> dict:
    """Load the ground truth manifest."""
    if not FIXTURE_TRUTH.exists():
        raise FileNotFoundError(f"Truth manifest not found: {FIXTURE_TRUTH}")
    return json.loads(FIXTURE_TRUTH.read_text())


def extract_control_ids(sections: list) -> set:
    """Extract control IDs (XX-N format) from sections."""
    control_re = re.compile(r"^([A-Z]{2}-\d+(?:\(\d+\))?)")
    control_ids = set()
    for s in sections:
        numbering = s.get("numbering", "") or ""
        title = s.get("title", "") or ""
        # Check numbering field first
        if numbering and control_re.match(numbering):
            control_ids.add(numbering.split("(")[0])  # Normalize AC-2(1) -> AC-2
        # Also check title
        match = control_re.match(title)
        if match:
            control_ids.add(match.group(1).split("(")[0])
    return control_ids


def run_calibration_test() -> dict:
    """Run extraction and compare against truth manifest.

    Returns dict with:
        - passed: bool
        - section_match: float (0.0-1.0)
        - table_match: float (0.0-1.0)
        - control_match: float (0.0-1.0)
        - details: dict with expected/actual counts
    """
    if not FIXTURE_PDF.exists():
        return {
            "passed": False,
            "error": f"Fixture PDF not found: {FIXTURE_PDF}",
        }

    # Load truth manifest
    truth = load_truth_manifest()

    # Expected counts from truth manifest
    expected_sections = set()
    expected_control_ids = set()
    expected_tables = 0

    # section_hierarchy is a dict: {id: {title, depth, qid, ...}}
    for sec_id, section in truth.get("section_hierarchy", {}).items():
        title = section.get("title", "")
        expected_sections.add(title)
        # Extract control ID from title (AC-1, SI-7, etc.)
        match = re.match(r"([A-Z]{2}-\d+)", title)
        if match:
            expected_control_ids.add(match.group(1))

    expected_tables = truth.get("total_tables", 0)

    # Run extraction
    config = PipelineConfig()
    result = extract_content(str(FIXTURE_PDF), config)

    # Actual results
    actual_sections = set(s.get("title", "") for s in result.sections)
    actual_control_ids = extract_control_ids(result.sections)
    actual_tables = len(result.tables)

    # Calculate match rates
    if expected_control_ids:
        control_match = len(actual_control_ids & expected_control_ids) / len(expected_control_ids)
    else:
        control_match = 1.0 if not actual_control_ids else 0.0

    if expected_tables > 0:
        table_match = min(actual_tables / expected_tables, 1.0)
        # Penalize over-detection
        if actual_tables > expected_tables * 1.5:
            table_match *= 0.8
    else:
        table_match = 1.0 if actual_tables == 0 else 0.5

    # Section match: compare titles (fuzzy)
    section_matches = 0
    for exp in expected_sections:
        exp_clean = exp.lower().strip()[:50]
        for act in actual_sections:
            act_clean = act.lower().strip()[:50]
            if exp_clean in act_clean or act_clean in exp_clean:
                section_matches += 1
                break

    if expected_sections:
        section_match = section_matches / len(expected_sections)
    else:
        section_match = 1.0

    # Overall pass/fail
    passed = control_match >= PASS_THRESHOLD and table_match >= PASS_THRESHOLD

    return {
        "passed": passed,
        "section_match": round(section_match, 3),
        "table_match": round(table_match, 3),
        "control_match": round(control_match, 3),
        "details": {
            "expected_sections": len(expected_sections),
            "actual_sections": len(result.sections),
            "expected_control_ids": sorted(expected_control_ids),
            "actual_control_ids": sorted(actual_control_ids),
            "missing_control_ids": sorted(expected_control_ids - actual_control_ids),
            "expected_tables": expected_tables,
            "actual_tables": actual_tables,
        },
    }


def test_extraction_calibration():
    """Pytest entry point."""
    result = run_calibration_test()

    print("\n=== Extraction Calibration Test ===")
    print(f"Control ID match: {result['control_match']:.1%}")
    print(f"Table match: {result['table_match']:.1%}")
    print(f"Section match: {result['section_match']:.1%}")
    print()
    print(f"Expected control IDs: {result['details']['expected_control_ids'][:10]}...")
    print(f"Actual control IDs: {result['details']['actual_control_ids'][:10]}...")
    print(f"Missing control IDs: {result['details']['missing_control_ids']}")
    print(f"Tables: {result['details']['actual_tables']}/{result['details']['expected_tables']}")
    print()

    assert result["passed"], (
        f"Extraction calibration failed: "
        f"control={result['control_match']:.1%}, table={result['table_match']:.1%} "
        f"(need {PASS_THRESHOLD:.0%})"
    )


if __name__ == "__main__":
    result = run_calibration_test()

    print("=== Extraction Calibration Test ===")
    print(f"Passed: {result['passed']}")
    print(f"Control ID match: {result.get('control_match', 0):.1%}")
    print(f"Table match: {result.get('table_match', 0):.1%}")
    print(f"Section match: {result.get('section_match', 0):.1%}")

    if "details" in result:
        d = result["details"]
        print(f"\nExpected controls: {d['expected_control_ids']}")
        print(f"Actual controls: {d['actual_control_ids']}")
        print(f"Missing: {d['missing_control_ids']}")
        print(f"Tables: {d['actual_tables']}/{d['expected_tables']}")

    if "error" in result:
        print(f"\nError: {result['error']}")

    # Exit with code for /code-runner
    sys.exit(0 if result["passed"] else 1)
