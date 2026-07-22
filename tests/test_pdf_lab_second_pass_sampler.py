from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts/pdf_lab/select_stratified_page_cases.py"
    spec = importlib.util.spec_from_file_location("select_stratified_page_cases_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _candidate(page: int, idx: int, preset: str, reasons: list[str] | None = None) -> dict:
    return {
        "candidate_id": f"cand:p{page:04d}:{idx:04d}:{preset}",
        "page_number": page,
        "preset_type": preset,
        "detection_reason": reasons or [f"preset_type:{preset}"],
    }


def _manifest() -> dict:
    return {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "pdf_id": "nist:test",
        "pdf_path": "/tmp/nist.pdf",
        "page_count": 400,
        "candidate_count": 10,
        "candidates": [
            _candidate(1, 0, "toc", ["frontmatter_or_early_page", "hardening_interest"]),
            _candidate(2, 0, "section_heading", ["frontmatter_or_early_page"]),
            _candidate(25, 0, "table", ["hardening_interest", "large_region"]),
            _candidate(25, 1, "unknown_layout", ["hardening_interest"]),
            _candidate(40, 0, "list", ["hardening_interest"]),
            _candidate(120, 0, "reference", ["hardening_interest"]),
            _candidate(300, 0, "footnote", ["hardening_interest", "boundary_geometry"]),
            _candidate(360, 0, "appendix", ["late_document_page"]),
            _candidate(390, 0, "table", ["late_document_page", "hardening_interest"]),
            _candidate(399, 0, "side_chrome", ["late_document_page", "boundary_geometry", "hardening_interest"]),
        ],
    }


def test_sampler_is_deterministic_for_fixed_seed() -> None:
    sampler = _load_module()
    first = sampler.select_page_cases(_manifest(), sample_size=6, seed=1234)
    second = sampler.select_page_cases(_manifest(), sample_size=6, seed=1234)

    assert first["schema"] == "pdf_lab.second_pass.sampled_page_cases.v1"
    assert first["manifest_validation"]["schema"] == "pdf_lab.second_pass.candidate_manifest_validation.v1"
    assert first["manifest_validation"]["ok"] is True
    assert first["selected_pages"] == second["selected_pages"]
    assert first["page_cases"] == second["page_cases"]


def test_sampler_rejects_invalid_sampling_parameters() -> None:
    sampler = _load_module()
    cases = [
        ({"sample_size": 0, "seed": 1}, "sample_size must be >= 1"),
        ({"sample_size": True, "seed": 1}, "sample_size must be >= 1"),
        ({"sample_size": "1", "seed": 1}, "sample_size must be >= 1"),
        ({"sample_size": 1, "seed": True}, "seed must be an integer: True"),
        ({"sample_size": 1, "seed": "1"}, "seed must be an integer: '1'"),
        ({"sample_size": 1, "seed": 1, "min_per_stratum": 0}, "min_per_stratum must be >= 1"),
        ({"sample_size": 1, "seed": 1, "min_per_stratum": True}, "min_per_stratum must be >= 1"),
        ({"sample_size": 1, "seed": 1, "min_per_stratum": "1"}, "min_per_stratum must be >= 1"),
        ({"sample_size": 1, "seed": 1, "random_reserve_fraction": -0.1}, "random_reserve_fraction must be >= 0 and < 1"),
        ({"sample_size": 1, "seed": 1, "random_reserve_fraction": 1.0}, "random_reserve_fraction must be >= 0 and < 1"),
        ({"sample_size": 1, "seed": 1, "random_reserve_fraction": True}, "random_reserve_fraction must be >= 0 and < 1"),
        ({"sample_size": 1, "seed": 1, "random_reserve_fraction": "0.2"}, "random_reserve_fraction must be >= 0 and < 1"),
    ]

    for kwargs, expected_error in cases:
        try:
            sampler.select_page_cases(_manifest(), **kwargs)
        except ValueError as exc:
            assert expected_error in str(exc)
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")


def test_sampler_rejects_invalid_candidate_manifest_contract() -> None:
    sampler = _load_module()
    manifest = {
        "schema": "wrong",
        "page_count": 2,
        "candidate_count": 3,
        "candidates": [
            {"candidate_id": "dup", "page_number": 1, "preset_type": "table"},
            {"candidate_id": "dup", "page_number": 3, "preset_type": "table"},
            {"page_number": 1, "preset_type": ""},
        ],
    }

    try:
        sampler.select_page_cases(manifest, sample_size=1, seed=1)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError for invalid candidate manifest")

    assert "manifest schema must be pdf_lab.second_pass.candidate_manifest.v1" in message
    assert "manifest candidates[1].page_number exceeds manifest page_count" in message
    assert "manifest candidates[2].candidate_id must be non-empty" in message
    assert "manifest candidates[2].preset_type must be non-empty" in message
    assert "manifest candidate_id values must be unique: ['dup']" in message


def test_sampler_cli_writes_failure_artifact_for_invalid_manifest(tmp_path: Path, monkeypatch) -> None:
    sampler = _load_module()
    manifest_path = tmp_path / "manifest.json"
    out_path = tmp_path / "sampled_page_cases.json"
    manifest_path.write_text(json.dumps(["not-object"]), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "select_stratified_page_cases.py",
            "--manifest",
            str(manifest_path),
            "--out",
            str(out_path),
            "--sample-size",
            "1",
            "--seed",
            "1",
        ],
    )

    exit_code = sampler.main()

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert payload["schema"] == "pdf_lab.second_pass.sampled_page_cases.v1"
    assert payload["ok"] is False
    assert payload["selected_count"] == 0
    assert payload["selected_pages"] == []
    assert payload["page_cases"] == []
    assert "manifest must be a JSON object" in payload["errors"][0]
    assert payload["sampling_audit"]["ok"] is False


def test_sampler_rejects_boolean_integer_fields_in_manifest_contract() -> None:
    sampler = _load_module()
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "page_count": True,
        "candidate_count": True,
        "candidates": [
            {"candidate_id": "cand:p0001:0000:table", "page_number": True, "preset_type": "table"},
        ],
    }

    try:
        sampler.select_page_cases(manifest, sample_size=1, seed=1)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError for boolean integer fields")

    assert "manifest candidate_count must be a non-negative integer" in message
    assert "manifest page_count must be null or a positive integer" in message
    assert "manifest candidates[0].page_number must be a positive integer" in message


def test_sampler_rejects_coerced_candidate_count_before_probability_basis() -> None:
    sampler = _load_module()
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "page_count": 5,
        "candidate_count": "1",
        "candidates": [
            {"candidate_id": "cand:p0001:0000:table", "page_number": 1, "preset_type": "table"},
        ],
    }

    try:
        sampler.select_page_cases(manifest, sample_size=1, seed=1)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError for string candidate_count")

    assert "manifest candidate_count must be a non-negative integer" in message


def test_sampler_page_feature_helpers_reject_coerced_page_numbers() -> None:
    sampler = _load_module()
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "page_count": 5,
        "candidate_count": 1,
        "candidates": [
            {"candidate_id": "cand:p0001:0000:table", "page_number": "1", "preset_type": "table"},
        ],
    }

    for helper in [sampler.page_features, sampler.stratify_candidates]:
        try:
            helper(manifest)
        except ValueError as exc:
            message = str(exc)
        else:
            raise AssertionError(f"expected ValueError from {helper.__name__}")
        assert "candidate 'cand:p0001:0000:table' page_number must be a positive integer: '1'" in message


def test_sampler_stratify_rejects_coerced_page_count() -> None:
    sampler = _load_module()
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "page_count": "5",
        "candidate_count": 1,
        "candidates": [
            {"candidate_id": "cand:p0001:0000:table", "page_number": 1, "preset_type": "table"},
        ],
    }

    try:
        sampler.stratify_candidates(manifest)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError for string page_count")

    assert "manifest page_count must be null or a positive integer: '5'" in message


def test_sampler_rejects_malformed_detection_reason_contract() -> None:
    sampler = _load_module()
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "page_count": 5,
        "candidate_count": 1,
        "candidates": [
            {
                "candidate_id": "cand:p0001:0000:table",
                "page_number": 1,
                "preset_type": "table",
                "detection_reason": "boundary_geometry",
            },
        ],
    }

    try:
        sampler.select_page_cases(manifest, sample_size=1, seed=1)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError for malformed detection_reason")

    assert "manifest candidates[0].detection_reason must be a list of non-empty strings" in message


def test_sampler_preserves_high_risk_and_position_strata() -> None:
    sampler = _load_module()
    result = sampler.select_page_cases(_manifest(), sample_size=9, seed=7)

    selected_presets = {
        preset
        for case in result["page_cases"]
        for preset in case["preset_counts"]
    }
    selected_strata = {
        stratum
        for case in result["page_cases"]
        for stratum in case["strata"]
    }

    assert {"table", "toc", "side_chrome"} <= selected_presets
    assert "position:frontmatter" in selected_strata
    assert "position:late_document" in selected_strata
    assert all(case["candidate_ids"] for case in result["page_cases"])
    assert all(case["selection_reason"] for case in result["page_cases"])
    assert result["sampling_audit"]["adequate_for_priority_strata"] is True
    assert "preset:table" in result["sampling_audit"]["covered_priority_strata"]
    assert "risk:high" in result["sampling_audit"]["covered_priority_strata"]
    assert result["sampling_audit"]["selection_records"]


def test_sampler_treats_all_non_text_presets_as_high_risk() -> None:
    sampler = _load_module()

    assert {"section_heading", "figure", "appendix"} <= sampler.HIGH_RISK_TYPES
    assert "text" not in sampler.HIGH_RISK_TYPES


def test_sampler_reports_adequacy_warnings_for_large_under_sampled_documents() -> None:
    sampler = _load_module()
    candidates = []
    presets = ["table", "toc", "equation", "footnote", "reference", "side_chrome", "unknown_layout"]
    for idx, preset in enumerate(presets, start=1):
        page = idx * 50
        candidates.append(_candidate(page, 0, preset, ["hardening_interest"]))
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "pdf_id": "nist:large",
        "pdf_path": "/tmp/nist.pdf",
        "page_count": 492,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }

    result = sampler.select_page_cases(manifest, sample_size=3, seed=42)
    audit = result["sampling_audit"]

    assert audit["schema"] == "pdf_lab.second_pass.sampling_audit.v1"
    assert audit["candidate_page_count"] == len(candidates)
    assert audit["recommended_min_sample_size"] >= 6
    assert audit["adequate_sample_size"] is False
    assert audit["statistical_significance_basis"]["adequate"] is False
    assert audit["statistical_significance_basis"]["candidate_page_population"] == len(candidates)
    assert audit["statistical_significance_basis"]["selected_page_count"] == result["selected_count"]
    assert audit["statistical_significance_basis"]["recommended_min_sample_size"] == audit["recommended_min_sample_size"]
    assert audit["missed_priority_strata"]
    assert any("below recommended minimum" in warning for warning in audit["warnings"])
    assert any("priority strata not represented" in warning for warning in audit["warnings"])


def test_sampler_never_selects_position_only_pages_without_candidates() -> None:
    sampler = _load_module()
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "pdf_id": "nist:sparse-frontmatter",
        "pdf_path": "/tmp/nist.pdf",
        "page_count": 20,
        "candidate_count": 1,
        "candidates": [_candidate(2, 0, "table", ["frontmatter_or_early_page", "hardening_interest"])],
    }

    result = sampler.select_page_cases(manifest, sample_size=3, seed=530800)

    assert result["selected_pages"] == [2]
    assert result["selected_count"] == 1
    assert result["page_cases"][0]["candidate_ids"] == ["cand:p0002:0000:table"]
    assert all(case["candidate_ids"] for case in result["page_cases"])
    assert "position:first_20" in result["page_cases"][0]["strata"]


def test_sampler_recommended_min_covers_priority_strata_with_random_reserve() -> None:
    sampler = _load_module()
    presets = [
        "toc",
        "section_heading",
        "table",
        "equation",
        "figure",
        "list",
        "reference",
        "footnote",
        "side_chrome",
        "unknown_layout",
        "appendix",
    ]
    pages = [1, 50, 80, 110, 140, 170, 200, 230, 260, 300, 399]
    candidates = []
    for idx, (page, preset) in enumerate(zip(pages, presets, strict=True)):
        reasons = ["hardening_interest"]
        if page == 1:
            reasons.append("frontmatter_or_early_page")
        if page == 399:
            reasons.append("late_document_page")
        candidates.append(_candidate(page, idx, preset, reasons))
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "pdf_id": "nist:large",
        "pdf_path": "/tmp/nist.pdf",
        "page_count": 400,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    initial = sampler.select_page_cases(manifest, sample_size=3, seed=42)
    recommended = initial["sampling_audit"]["recommended_min_sample_size"]

    result = sampler.select_page_cases(manifest, sample_size=recommended, seed=42)
    audit = result["sampling_audit"]

    assert recommended == len(candidates)
    assert result["selected_count"] == len(candidates)
    assert audit["adequate_sample_size"] is True
    assert audit["adequate_for_priority_strata"] is True
    assert audit["statistical_significance_basis"]["method"] == "stratified_priority_coverage_plus_weighted_random_reserve"
    assert audit["statistical_significance_basis"]["adequate"] is True
    assert audit["statistical_significance_basis"]["finite_population_fraction"] == 1.0
    assert audit["statistical_significance_basis"]["priority_stratum_count"] == audit["priority_strata_count"]
    assert audit["missed_priority_strata"] == []
    assert audit["warnings"] == []


def test_sampler_includes_probability_basis_for_each_case() -> None:
    sampler = _load_module()
    result = sampler.select_page_cases(_manifest(), sample_size=6, seed=1234)

    assert result["seed"] == 1234
    assert result["sampling_audit"]["seed"] == 1234
    assert result["sampling_audit"]["statistical_significance_basis"]["seed"] == 1234

    for case in result["page_cases"]:
        basis = case["selection_probability_basis"]
        assert basis["method"] == "max(weighted_page_score_inclusion_estimate,candidate_share_estimate)"
        assert basis["forced_page"] is False
        assert case["forced_by_human_annotation"] is False
        assert 0 < basis["weighted_page_score_inclusion_estimate"] <= 1
        assert 0 < basis["candidate_share_estimate"] <= 1
        assert case["selection_probability_estimate"] == max(
            basis["weighted_page_score_inclusion_estimate"],
            basis["candidate_share_estimate"],
        )


def test_sampler_forces_human_annotated_candidate_pages() -> None:
    sampler = _load_module()
    result = sampler.select_page_cases(_manifest(), sample_size=3, seed=1234, forced_pages=[300, 999])
    audit = result["sampling_audit"]

    assert 300 in result["selected_pages"]
    assert 999 not in result["selected_pages"]
    assert result["forced_pages"] == {
        "requested": [300, 999],
        "accepted": [300],
        "rejected": [999],
    }
    assert audit["accepted_forced_pages"] == [300]
    assert audit["rejected_forced_pages"] == [999]
    assert audit["forced_pages_are_additive"] is True
    assert audit["probabilistic_selected_count"] == 3
    assert result["selected_count"] == 4
    assert result["probabilistic_selected_pages"] == [
        case["page_number"]
        for case in result["page_cases"]
        if not case["forced_by_human_annotation"]
    ]
    assert any(record["stratum"] == "forced:human_annotated" for record in audit["selection_records"])
    assert any("forced pages without candidate evidence" in warning for warning in audit["warnings"])
    forced_case = next(case for case in result["page_cases"] if case["page_number"] == 300)
    assert forced_case["forced_by_human_annotation"] is True
    assert forced_case["selection_probability_estimate"] == 1.0
    assert forced_case["selection_probability_basis"]["method"] == "forced_human_annotation"
    assert forced_case["selection_probability_basis"]["forced_page"] is True
    assert "human_annotated_page" in forced_case["selection_reason"]


def test_forced_pages_do_not_crowd_out_probabilistic_stratified_budget() -> None:
    sampler = _load_module()
    result = sampler.select_page_cases(_manifest(), sample_size=4, seed=99, forced_pages=[1, 300, 399])
    audit = result["sampling_audit"]

    forced_selected = [
        case["page_number"]
        for case in result["page_cases"]
        if case["forced_by_human_annotation"]
    ]
    probabilistic_selected = [
        case["page_number"]
        for case in result["page_cases"]
        if not case["forced_by_human_annotation"]
    ]

    assert forced_selected == [1, 300, 399]
    assert len(probabilistic_selected) == 4
    assert set(probabilistic_selected).isdisjoint(forced_selected)
    assert result["probabilistic_selected_pages"] == probabilistic_selected
    assert result["selected_count"] == len(forced_selected) + len(probabilistic_selected)
    assert audit["statistical_significance_basis"]["probabilistic_selected_page_count"] == 4
    assert audit["statistical_significance_basis"]["accepted_forced_page_count"] == 3
    assert audit["statistical_significance_basis"]["forced_pages_are_additive"] is True


def test_load_forced_pages_accepts_list_or_pages_object(tmp_path: Path) -> None:
    sampler = _load_module()
    list_path = tmp_path / "forced-list.json"
    object_path = tmp_path / "forced-object.json"
    list_path.write_text("[1, 25]\n", encoding="utf-8")
    object_path.write_text('{"pages": [300, 399]}\n', encoding="utf-8")

    assert sampler.load_forced_pages(list_path) == [1, 25]
    assert sampler.load_forced_pages(object_path) == [300, 399]


def test_load_forced_pages_rejects_bool_page_numbers(tmp_path: Path) -> None:
    sampler = _load_module()
    forced_path = tmp_path / "forced-bool.json"
    forced_path.write_text("[true]\n", encoding="utf-8")

    try:
        sampler.load_forced_pages(forced_path)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError for boolean forced page")

    assert "forced page at index 0 is not an integer: True" in message


def test_select_page_cases_rejects_direct_bool_forced_pages() -> None:
    sampler = _load_module()

    try:
        sampler.select_page_cases(_manifest(), sample_size=1, seed=1, forced_pages=[True])
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError for direct boolean forced page")

    assert "forced page at index 0 is not an integer: True" in message


def test_select_page_cases_rejects_coerced_empty_forced_pages() -> None:
    sampler = _load_module()

    try:
        sampler.select_page_cases(_manifest(), sample_size=1, seed=1, forced_pages="")
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError for coerced empty forced_pages")

    assert "forced_pages must be null or a list of positive integers: ''" in message
