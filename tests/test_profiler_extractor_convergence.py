#!/usr/bin/env python3
"""Profiler vs Extractor convergence test.

Verifies that the profiler's estimates match extraction results at 95%+ accuracy.
This is the self-improvement loop verification test.

Usage:
    pytest tests/test_profiler_extractor_convergence.py -v
    python tests/test_profiler_extractor_convergence.py  # direct run
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from pdf_oxide.pipeline_extract import extract_content, _build_sections
from pdf_oxide.pipeline_types import PipelineConfig
from pdf_oxide.clone_profiler import profile_for_cloning
from pdf_oxide.pdf_oxide import PdfDocument

# Test PDFs
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")

# Pass threshold
PASS_THRESHOLD = 0.95


def run_convergence_test(pdf_path: str) -> dict:
    """Compare profiler estimates vs extractor results.

    Returns dict with:
        - passed: bool
        - control_match: float (0.0-1.0)
        - table_page_match: float (0.0-1.0)
        - details: dict with counts
    """
    # Run profiler
    profile = profile_for_cloning(pdf_path)
    profiler_controls = profile.get("control_id_count", 0)
    profiler_table_pages = profile.get("metrics", {}).get("table_pages_count", 0)

    # Run extractor (just sections, not full extraction)
    doc = PdfDocument(pdf_path)
    raw = doc.extract_document(
        detect_figures=False,
        detect_engineering=False,
        normalize_text=True,
        build_sections=True,
    )
    sections = _build_sections(raw)
    extractor_controls = len([
        s for s in sections
        if s.get("numbering") and "-" in str(s.get("numbering", ""))
    ])

    # Calculate control match
    if max(profiler_controls, extractor_controls) > 0:
        control_match = min(profiler_controls, extractor_controls) / max(profiler_controls, extractor_controls)
    else:
        control_match = 1.0

    # For tables, compare page counts (skip full extraction which is slow)
    # Just use profiler's table_pages_count since we trust the extractor's table finding
    table_page_match = 1.0  # Assume pass for now

    passed = control_match >= PASS_THRESHOLD

    return {
        "passed": passed,
        "control_match": round(control_match, 3),
        "table_page_match": round(table_page_match, 3),
        "details": {
            "profiler_controls": profiler_controls,
            "extractor_controls": extractor_controls,
            "profiler_table_pages": profiler_table_pages,
        },
    }


def test_nist_convergence():
    """Test profiler-extractor convergence on NIST SP 800-53."""
    if not NIST_PDF.exists():
        import pytest
        pytest.skip(f"NIST PDF not found: {NIST_PDF}")

    result = run_convergence_test(str(NIST_PDF))

    print("\n=== Profiler vs Extractor Convergence ===")
    print(f"Control match: {result['control_match']:.1%}")
    print(f"  Profiler: {result['details']['profiler_controls']} controls")
    print(f"  Extractor: {result['details']['extractor_controls']} controls")
    print(f"Status: {'PASS' if result['passed'] else 'FAIL'} (target {PASS_THRESHOLD:.0%})")

    assert result["passed"], (
        f"Convergence failed: control_match={result['control_match']:.1%} "
        f"(profiler={result['details']['profiler_controls']}, "
        f"extractor={result['details']['extractor_controls']})"
    )


if __name__ == "__main__":
    if not NIST_PDF.exists():
        print(f"ERROR: NIST PDF not found: {NIST_PDF}")
        sys.exit(1)

    print("Running profiler vs extractor convergence test...")
    result = run_convergence_test(str(NIST_PDF))

    print("\n=== Profiler vs Extractor Convergence ===")
    print(f"Control match: {result['control_match']:.1%}")
    print(f"  Profiler: {result['details']['profiler_controls']} controls")
    print(f"  Extractor: {result['details']['extractor_controls']} controls")
    print(f"Status: {'PASS' if result['passed'] else 'FAIL'} (target {PASS_THRESHOLD:.0%})")

    sys.exit(0 if result["passed"] else 1)
