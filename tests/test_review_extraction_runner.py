"""Tests for the /review-extraction runner (R3 contract).

Five deterministic tests pinning the WebGPT 2026-05-13 R3 plan invariants:

  1. Manifest split flags: optional_code_present=true does NOT imply
     extraction_code_mutated=true or preset_or_ledger_mutated=true. It only
     implies review_tooling_code_mutated=true.
  2. review_bundle wording: when review tooling changed, the section text
     does NOT say "No code changes" — it says "Review tooling code is
     included under optional_code/".
  3. best_match_id guard: best_match_id is null unless
     match_status == "matched".
  4. ambiguous_multiple is NOT counted as matched in the summary totals.
  5. text_hint narrows paragraph rows better than family-only matching:
     three distinct expected paragraphs with different text_hints produce
     three distinct match results (one each), not three identical
     ambiguous-multiple lists of every paragraph on the page.

These run against the installed skill at
`~/.claude/skills/review-extraction/scripts/build_golden_slice_bundle.py`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


RUNNER_PATH = Path.home() / ".claude" / "skills" / "review-extraction" / "scripts" / "build_golden_slice_bundle.py"


@pytest.fixture(scope="module")
def runner():
    """Import the runner module by path so we can call its helpers directly."""
    if not RUNNER_PATH.exists():
        pytest.skip(f"runner not installed at {RUNNER_PATH}")
    spec = importlib.util.spec_from_file_location("build_golden_slice_bundle_under_test", RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_golden_slice_bundle_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


# --- 1. Manifest split flags -------------------------------------------------


def test_manifest_split_flags_when_optional_code_present(runner):
    manifest = runner._manifest(
        slice_id="t1",
        pdf_path=Path("/dev/null"),
        page_index=0,
        expected={"human_page_claim": "x", "printed_page_label": "x"},
        artifact_paths={},
        page_dims=[100, 100],
        crop_count=0,
        has_human_label=False,
        ledger_path=None,
        optional_code_info={"runner_sha256": "deadbeef"},
    )
    assert manifest["schema_version"] == "review_extraction.golden_slice.v2"
    assert manifest["optional_code_present"] is True
    assert manifest["review_tooling_code_mutated"] is True
    assert manifest["extraction_code_mutated"] is False
    assert manifest["preset_or_ledger_mutated"] is False
    assert manifest["matrix_mutated"] is False
    assert "code_or_preset_mutated" not in manifest, (
        "deprecated single flag must NOT be emitted in v3"
    )


def test_manifest_split_flags_when_no_optional_code(runner):
    manifest = runner._manifest(
        slice_id="t2",
        pdf_path=Path("/dev/null"),
        page_index=0,
        expected={"human_page_claim": "x", "printed_page_label": "x"},
        artifact_paths={},
        page_dims=[100, 100],
        crop_count=0,
        has_human_label=False,
        ledger_path=None,
        optional_code_info=None,
    )
    assert manifest["optional_code_present"] is False
    assert manifest["review_tooling_code_mutated"] is False
    assert manifest["extraction_code_mutated"] is False
    assert manifest["preset_or_ledger_mutated"] is False
    assert manifest["matrix_mutated"] is False


# --- 2. review_bundle wording ----------------------------------------------


def _fake_manifest(runner, optional_code: bool):
    return runner._manifest(
        slice_id="t",
        pdf_path=Path("/dev/null"),
        page_index=0,
        expected={"human_page_claim": "x", "printed_page_label": "x"},
        artifact_paths={"manifest": "/tmp/x/manifest.json"},
        page_dims=[100, 100],
        crop_count=0,
        has_human_label=True,
        ledger_path=None,
        optional_code_info={"runner_sha256": "x"} if optional_code else None,
    )


def test_review_bundle_wording_when_optional_code_present(runner):
    """Per R3 directive: do not say 'No code changes' when optional_code is
    present. Use the precise wording that distinguishes review tooling from
    extraction/preset/ledger/matrix/LLM."""
    manifest = _fake_manifest(runner, optional_code=True)
    md = runner._review_bundle_md(
        manifest=manifest,
        expected={"expected_elements": []},
        release_payload={"elements": []},
        comparison={
            "summary": {
                "expected_element_count": 0, "matched_count": 0,
                "ambiguous_multiple_count": 0, "missing_count": 0,
                "extra_extractor_element_count": 0,
                "reviewer_action_required_count": 0,
            }
        },
    )
    assert "No pdf_oxide core, preset, ledger, matrix, or LLM changes." in md
    assert "Review tooling code is included under `optional_code/`" in md
    # Negative assertion: the contradictory R2-era phrase must NOT appear
    assert "No code changes" not in md, (
        "wording must not say 'No code changes' when optional_code/ is present"
    )


def test_review_bundle_wording_lists_four_split_flags(runner):
    manifest = _fake_manifest(runner, optional_code=True)
    md = runner._review_bundle_md(
        manifest=manifest, expected={"expected_elements": []},
        release_payload={"elements": []},
        comparison={
            "summary": {
                "expected_element_count": 0, "matched_count": 0,
                "ambiguous_multiple_count": 0, "missing_count": 0,
                "extra_extractor_element_count": 0,
                "reviewer_action_required_count": 0,
            }
        },
    )
    for flag in (
        "extraction_code_mutated", "preset_or_ledger_mutated",
        "matrix_mutated", "review_tooling_code_mutated",
    ):
        assert flag in md, f"{flag} must be surfaced in review_bundle.md"
    assert "code_or_preset_mutated" not in md, (
        "deprecated single flag must NOT appear in review_bundle.md"
    )


# --- 3. best_match_id guard --------------------------------------------------


def _comparison_from(runner, expected, release_elements):
    """Run _build_comparison against a minimal in-memory expected + release."""
    return runner._build_comparison(
        expected={"slice_id": "t", "expected_elements": expected},
        release_payload={"elements": release_elements},
        raw_payload={"elements": release_elements},
    )


def test_best_match_id_null_unless_matched(runner):
    """best_match_id MUST be null for missing and for ambiguous_multiple."""
    expected = [
        # missing — no candidates
        {"family": "footnote_block", "label": "fn1",
         "text_hint": "zzz_nonexistent_text", "allowed_types": ["footnote"]},
        # ambiguous_multiple — two candidates by allowed_types
        {"family": "paragraph_block", "label": "p1",
         "allowed_types": ["paragraph_block"]},
        # matched — single candidate by exact text_hint
        {"family": "section_heading", "label": "intro",
         "text_hint": "UniqueHeadingABC", "allowed_types": ["section_heading"]},
    ]
    release = [
        {"id": "e:0", "type": "paragraph_block", "text": "alpha"},
        {"id": "e:1", "type": "paragraph_block", "text": "beta"},
        {"id": "e:2", "type": "section_heading", "text": "UniqueHeadingABC body"},
    ]
    comp = _comparison_from(runner, expected, release)
    rows = comp["rows"]
    by_family = {r["expected_family"] + ":" + r["human_label"]: r for r in rows}

    miss = by_family["footnote_block:fn1"]
    ambig = by_family["paragraph_block:p1"]
    matched = by_family["section_heading:intro"]

    assert miss["match_status"] == "missing"
    assert miss["best_match_id"] is None

    assert ambig["match_status"] == "ambiguous_multiple"
    assert ambig["best_match_id"] is None, (
        "best_match_id must be null for ambiguous_multiple"
    )

    assert matched["match_status"] == "matched"
    assert matched["best_match_id"] == "e:2"


# --- 4. ambiguous_multiple is not counted as matched ------------------------


def test_ambiguous_multiple_not_counted_as_matched(runner):
    expected = [
        {"family": "paragraph_block", "label": "p", "allowed_types": ["paragraph_block"]},
    ]
    release = [
        {"id": "e:0", "type": "paragraph_block", "text": "a"},
        {"id": "e:1", "type": "paragraph_block", "text": "b"},
        {"id": "e:2", "type": "paragraph_block", "text": "c"},
    ]
    comp = _comparison_from(runner, expected, release)
    summary = comp["summary"]
    assert summary["matched_count"] == 0
    assert summary["ambiguous_multiple_count"] == 1
    # Sanity: matched_count + ambiguous + missing == expected_element_count
    assert (summary["matched_count"]
            + summary["ambiguous_multiple_count"]
            + summary["missing_count"]) == summary["expected_element_count"]


# --- 5. text_hint narrows paragraph rows better than family-only matching --


def test_text_hint_narrows_paragraph_rows(runner):
    """Three expected paragraphs, three distinct text_hints, four release
    elements. With text_hint precedence, each expected row narrows to
    exactly ONE distinct match — not three rows all ambiguous over all four
    paragraph_block candidates."""
    expected = [
        {"family": "paragraph_block", "label": "p1",
         "text_hint": "Modern information systems",
         "allowed_types": ["paragraph_block"], "match_strategy": "text_contains"},
        {"family": "paragraph_block", "label": "p2",
         "text_hint": "Security controls are the safeguards",
         "allowed_types": ["paragraph_block"], "match_strategy": "text_contains"},
        {"family": "paragraph_block", "label": "p3",
         "text_hint": "The selection, design, and implementation",
         "allowed_types": ["paragraph_block"], "match_strategy": "text_contains"},
    ]
    release = [
        {"id": "e:10", "type": "paragraph_block",
         "text": "Modern information systems can include a variety..."},
        {"id": "e:11", "type": "paragraph_block",
         "text": "Security controls are the safeguards or countermeasures..."},
        {"id": "e:12", "type": "paragraph_block",
         "text": "The selection, design, and implementation of security..."},
        {"id": "e:99", "type": "paragraph_block",
         "text": "Some unrelated trailing paragraph."},
    ]
    comp = _comparison_from(runner, expected, release)
    rows = {r["human_label"]: r for r in comp["rows"]}

    assert rows["p1"]["match_status"] == "matched"
    assert rows["p1"]["best_match_id"] == "e:10"
    assert rows["p1"]["match_basis"] == "text_hint"

    assert rows["p2"]["match_status"] == "matched"
    assert rows["p2"]["best_match_id"] == "e:11"

    assert rows["p3"]["match_status"] == "matched"
    assert rows["p3"]["best_match_id"] == "e:12"

    # Summary: 3 matched, 0 ambiguous, 0 missing; e:99 is extra
    summary = comp["summary"]
    assert summary["matched_count"] == 3
    assert summary["ambiguous_multiple_count"] == 0
    assert summary["missing_count"] == 0
    extras = comp["extra_pdf_oxide_elements_not_in_human_label"]
    assert any(e["emitted_id"] == "e:99" for e in extras)


# --- 6/7/8. blocked_on payload contract ------------------------------------


def _valid_blocked_on() -> dict:
    return {
        "schema_version": "review_extraction.blocked_on.v1",
        "stop_condition_id": "core_vs_preset_ownership_lacks_reproducer",
        "where_blocked": [
            {"expected_family": "section_heading",
             "detail": "block:9 has INTRODUCTION text but typed paragraph_block"},
        ],
        "specific_question": "For row 3 (INTRODUCTION), is the owner route pdf_oxide_core or nist_preset_ledger?",
        "artifacts_to_consult": ["tables/golden_slice_comparison.md"],
        "what_i_will_NOT_do_without_decision": ["do not patch src/extractors/*"],
        "what_decision_unblocks": ["implement the named route and regenerate"],
    }


def test_blocked_on_validator_accepts_valid_payload(runner):
    runner._validate_blocked_on(_valid_blocked_on())  # must not raise


def test_blocked_on_validator_rejects_missing_fields(runner):
    bad = _valid_blocked_on()
    del bad["specific_question"]
    with pytest.raises(runner.BlockedOnValidationError, match="missing required fields"):
        runner._validate_blocked_on(bad)


def test_blocked_on_validator_rejects_unknown_stop_condition(runner):
    bad = _valid_blocked_on()
    bad["stop_condition_id"] = "extractor_is_bad"
    with pytest.raises(runner.BlockedOnValidationError, match="not one of the six allowed"):
        runner._validate_blocked_on(bad)


def test_blocked_on_validator_rejects_prose_where_blocked(runner):
    bad = _valid_blocked_on()
    bad["where_blocked"] = ["extraction is bad"]  # prose list, not dicts with detail
    with pytest.raises(runner.BlockedOnValidationError, match="dict with a 'detail' field"):
        runner._validate_blocked_on(bad)


def test_blocked_on_validator_rejects_vague_specific_question(runner):
    bad = _valid_blocked_on()
    bad["specific_question"] = "what should I do"
    with pytest.raises(runner.BlockedOnValidationError, match="≥30 chars"):
        runner._validate_blocked_on(bad)


def test_blocked_on_markdown_render_includes_all_required_sections(runner):
    md = runner._render_blocked_on_md(_valid_blocked_on())
    assert "Asking for — bundle is BLOCKED" in md
    assert "Stop condition" in md
    assert "Specific question" in md
    assert "Where I am blocked" in md
    assert "Artifacts to consult" in md
    assert "What I will NOT do without your decision" in md
    assert "What your decision unblocks" in md


def test_manifest_carries_blocked_on_payload(runner):
    payload = _valid_blocked_on()
    m = runner._manifest(
        slice_id="t",
        pdf_path=Path("/dev/null"),
        page_index=0,
        expected={"human_page_claim": "x", "printed_page_label": "x"},
        artifact_paths={},
        page_dims=[100, 100],
        crop_count=0,
        has_human_label=True,
        ledger_path=None,
        optional_code_info=None,
        blocked_on_payload=payload,
    )
    assert m["blocked_on"] == payload


def test_manifest_blocked_on_null_when_not_supplied(runner):
    m = runner._manifest(
        slice_id="t",
        pdf_path=Path("/dev/null"),
        page_index=0,
        expected={"human_page_claim": "x", "printed_page_label": "x"},
        artifact_paths={},
        page_dims=[100, 100],
        crop_count=0,
        has_human_label=True,
        ledger_path=None,
        optional_code_info=None,
        blocked_on_payload=None,
    )
    assert m["blocked_on"] is None


def test_review_bundle_prepends_section_0_when_blocked_on_set(runner):
    payload = _valid_blocked_on()
    m = runner._manifest(
        slice_id="t",
        pdf_path=Path("/dev/null"),
        page_index=0,
        expected={"human_page_claim": "x", "printed_page_label": "x"},
        artifact_paths={"manifest": "/tmp/x/manifest.json"},
        page_dims=[100, 100],
        crop_count=0,
        has_human_label=True,
        ledger_path=None,
        optional_code_info=None,
        blocked_on_payload=payload,
    )
    md = runner._review_bundle_md(
        manifest=m,
        expected={"expected_elements": []},
        release_payload={"elements": []},
        comparison={"summary": {
            "expected_element_count": 0, "matched_count": 0,
            "ambiguous_multiple_count": 0, "missing_count": 0,
            "extra_extractor_element_count": 0,
            "reviewer_action_required_count": 0,
        }},
    )
    assert "## 0. Asking for — bundle is BLOCKED" in md
    assert payload["specific_question"] in md


def test_review_bundle_omits_section_0_when_blocked_on_null(runner):
    m = runner._manifest(
        slice_id="t",
        pdf_path=Path("/dev/null"),
        page_index=0,
        expected={"human_page_claim": "x", "printed_page_label": "x"},
        artifact_paths={"manifest": "/tmp/x/manifest.json"},
        page_dims=[100, 100],
        crop_count=0,
        has_human_label=True,
        ledger_path=None,
        optional_code_info=None,
        blocked_on_payload=None,
    )
    md = runner._review_bundle_md(
        manifest=m,
        expected={"expected_elements": []},
        release_payload={"elements": []},
        comparison={"summary": {
            "expected_element_count": 0, "matched_count": 0,
            "ambiguous_multiple_count": 0, "missing_count": 0,
            "extra_extractor_element_count": 0,
            "reviewer_action_required_count": 0,
        }},
    )
    assert "## 0. Asking for" not in md
