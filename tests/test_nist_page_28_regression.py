"""GS001 R4 — failing regression for NIST 800-53r5 page 28 (printed PAGE 1).

Added BEFORE the row 3 core fix per WebGPT 2026-05-13 plan. The four
assertions are the deterministic gates: when these pass, the GS001 page
28 extraction matches the human-annotated contract.

Acceptance gates (all four MUST pass before GS001 R4 can converge):

  1. No release-mode element text contains the extraction artifact "derMon"
     (boundary-bleed from the section_subtitle into the first body paragraph).
  2. The release-mode JSON has a `section_heading` element whose normalized
     text is EXACTLY "INTRODUCTION" (not merged with the all-caps subtitle).
  3. The first body paragraph_block text starts with "Modern information
     systems" (the merge-bleed is gone).
  4. The side DOI chrome ("This publication is available free of charge…")
     is NOT classified as paragraph_block or list — it must route to
     header_footer_noise.

These tests deliberately FAIL on the current (R3) extraction. They are the
failing-test-first contract WebGPT required before the row 3 core fix is
implemented. Once row 3 lands, run pytest to verify the failing
assertions flip to pass.

Run with:

    PDF_OXIDE_REPO=$PWD uv run pytest tests/test_nist_page_28_regression.py -v

The test invokes the /review-extraction runner to do the actual extraction
because that's the canonical release-mode path for the slice.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


PDF_PATH = Path("/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/NIST_SP_800-53r5.pdf")
LEDGER_PATH = Path(__file__).resolve().parent.parent / "python" / "pdf_oxide" / "presets" / "document_families" / "nist_sp_800_53r5_promotion_ledger.json"
RUNNER = Path.home() / ".claude" / "skills" / "review-extraction" / "scripts" / "build_golden_slice_bundle.py"
EXPECTED = Path("/tmp/pdf-lab-golden-slices/nist_page_28_printed_page_1/expected_elements_v2.json")
HUMAN_LABEL = Path("/tmp/pdf-lab-golden-slices/nist_page_28_printed_page_1/human_labeled_page.png")
PAGE_INDEX = 27


@pytest.fixture(scope="module")
def release_json():
    """Run the /review-extraction runner once; reuse the release JSON."""
    if not PDF_PATH.exists():
        pytest.fail(f"source PDF not present: {PDF_PATH}")
    if not RUNNER.exists():
        pytest.fail(f"runner not installed at {RUNNER}")
    if not LEDGER_PATH.exists():
        pytest.fail(f"ledger not present: {LEDGER_PATH}")
    if not EXPECTED.exists():
        pytest.fail(f"expected_elements not present: {EXPECTED}")

    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "nist_page_28_regression"
        cmd = [
            "uv", "--project", str(Path(__file__).resolve().parent.parent), "run", "python",
            str(RUNNER),
            "--pdf", str(PDF_PATH),
            "--page-index", str(PAGE_INDEX),
            "--expected-elements", str(EXPECTED),
            "--ledger", str(LEDGER_PATH),
            "--slice-id", "nist_page_28_regression_under_test",
            "--out", str(out_dir),
        ]
        if HUMAN_LABEL.exists():
            cmd += ["--human-labeled-page", str(HUMAN_LABEL)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            pytest.fail(
                f"runner failed (returncode={result.returncode})\n"
                f"stdout: {result.stdout[-2000:]}\n"
                f"stderr: {result.stderr[-2000:]}"
            )
        release_path = out_dir / "json" / "pdf_oxide_release_page.json"
        if not release_path.exists():
            pytest.fail(f"release JSON not produced: {release_path}")
        return json.loads(release_path.read_text(encoding="utf-8"))


def _normalize(s: str) -> str:
    return " ".join((s or "").lower().split())


# --- Gate 1: no element text contains "derMon" -------------------------------


def test_no_element_text_contains_derMon_artifact(release_json):
    """The merge-bleed extraction artifact 'derMon information systems' must
    not appear in any release-mode element text. R3 failed this gate."""
    offenders = []
    for el in release_json["elements"]:
        text = el.get("text") or ""
        if "derMon" in text:
            offenders.append({"id": el.get("id"), "text_preview": text[:120]})
    assert not offenders, (
        f"release JSON has {len(offenders)} element(s) with 'derMon' artifact: {offenders}"
    )


# --- Gate 2: section_heading element with text exactly "INTRODUCTION" ---


def test_section_heading_INTRODUCTION_is_exact(release_json):
    """Row 3 contract: an emitted element typed `section_heading` whose
    normalized text equals exactly 'INTRODUCTION' (not merged with
    subtitle). R3 has no such element — block:8 is 'CHAPTER ONE' and
    block:9 is paragraph_block containing both INTRODUCTION and subtitle."""
    matches = [
        el for el in release_json["elements"]
        if el.get("type") == "section_heading"
        and _normalize(el.get("text") or "") == "introduction"
    ]
    assert len(matches) >= 1, (
        "no section_heading element with normalized text == 'introduction' found; "
        f"section_heading candidates emitted: "
        f"{[(e.get('id'), e.get('text')) for e in release_json['elements'] if e.get('type') == 'section_heading']}"
    )


# --- Gate 3: first body paragraph starts with "Modern information systems" ---


def test_first_body_paragraph_starts_with_Modern_information_systems(release_json):
    """Row 4 contract: the first body paragraph_block on the page must
    start with 'Modern information systems'. R3 fails because block:10
    starts with 'derMon information systems' (merge bleed)."""
    paragraphs = [
        el for el in release_json["elements"]
        if el.get("type") == "paragraph_block"
    ]
    if not paragraphs:
        pytest.fail("no paragraph_block elements emitted for page 27")
    # Find the first paragraph_block whose text is non-trivial (not just chrome)
    # and check whether ANY of the first 3 paragraph_blocks starts with the
    # expected text. Row order on the rendered page is what matters.
    starters = [
        (el["id"], (el.get("text") or "").strip()[:60])
        for el in paragraphs
        if (el.get("text") or "").strip()
    ]
    expected = "modern information systems"
    matches = [
        (eid, preview) for eid, preview in starters
        if _normalize(preview).startswith(expected)
    ]
    assert matches, (
        f"no paragraph_block starts with 'Modern information systems'; "
        f"all paragraph_block starters: {starters}"
    )


# --- Gate 4: side DOI chrome is NOT body/list ---


def test_side_doi_chrome_routed_to_header_footer_noise(release_json):
    """Row 2 contract: any release-mode element whose text contains
    'This publication is available free of charge' MUST be typed
    `header_footer_noise`, not `paragraph_block` or `list`. R3 fails
    because the rotated DOI watermark text leaks as blocks 3/4/5."""
    doi_anchor = "this publication is available free of charge"
    leaks = []
    for el in release_json["elements"]:
        text_norm = _normalize(el.get("text") or "")
        if doi_anchor in text_norm and el.get("type") in {"paragraph_block", "list"}:
            leaks.append({
                "id": el.get("id"),
                "type": el.get("type"),
                "text_preview": (el.get("text") or "")[:100],
            })
    assert not leaks, (
        f"DOI watermark leaked into body/list content (expected header_footer_noise): {leaks}"
    )
