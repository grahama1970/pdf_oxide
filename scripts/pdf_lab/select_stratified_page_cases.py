#!/usr/bin/env python3
"""Select seeded stratified page cases from a candidate manifest."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HIGH_RISK_TYPES = {
    "table",
    "section_heading",
    "list",
    "figure",
    "appendix",
    "unknown_layout",
    "side_chrome",
    "reference",
    "footnote",
    "toc",
    "equation",
}
CORE_COVERAGE_STRATA = {
    "risk:high",
    "geometry:boundary",
    "position:frontmatter",
    "position:late_document",
    "position:first_20",
    "position:last_15_percent",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_forced_pages(path: Path | None) -> list[int]:
    if path is None:
        return []
    payload = load_json(path)
    raw_pages = payload.get("pages") if isinstance(payload, dict) else payload
    if not isinstance(raw_pages, list):
        raise ValueError("forced pages input must be a JSON list or an object with a pages list")
    pages: list[int] = []
    for index, page in enumerate(raw_pages):
        if not isinstance(page, int):
            raise ValueError(f"forced page at index {index} is not an integer: {page!r}")
        if page < 1:
            raise ValueError(f"forced page at index {index} must be >= 1: {page!r}")
        pages.append(page)
    return pages


def _page_number(candidate: dict[str, Any]) -> int:
    return int(candidate["page_number"])


def _candidate_weight(candidate: dict[str, Any]) -> float:
    preset = str(candidate.get("preset_type") or "text")
    weight = 1.0
    if preset in HIGH_RISK_TYPES:
        weight += 3.0
    detection_reason = candidate.get("detection_reason")
    reasons = set(detection_reason if isinstance(detection_reason, list) else [])
    if "boundary_geometry" in reasons:
        weight += 1.0
    if "large_region" in reasons:
        weight += 0.75
    if "long_text_region" in reasons:
        weight += 0.5
    return weight


def validate_candidate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        errors.append("manifest must be a JSON object")
        manifest = {}
    if manifest.get("schema") != "pdf_lab.second_pass.candidate_manifest.v1":
        errors.append("manifest schema must be pdf_lab.second_pass.candidate_manifest.v1")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        errors.append("manifest candidates must be a list")
        candidates = []
    declared_candidate_count = manifest.get("candidate_count")
    if not isinstance(declared_candidate_count, int) or declared_candidate_count < 0:
        errors.append("manifest candidate_count must be a non-negative integer")
    elif declared_candidate_count != len(candidates):
        errors.append("manifest candidate_count does not match candidates length")
    page_count = manifest.get("page_count")
    if page_count is not None and (not isinstance(page_count, int) or page_count < 1):
        errors.append("manifest page_count must be null or a positive integer")
        page_count = None
    seen_candidate_ids: list[str] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            errors.append(f"manifest candidates[{index}] must be an object")
            continue
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            errors.append(f"manifest candidates[{index}].candidate_id must be non-empty")
        else:
            seen_candidate_ids.append(candidate_id)
        page_number = candidate.get("page_number")
        if not isinstance(page_number, int) or page_number < 1:
            errors.append(f"manifest candidates[{index}].page_number must be a positive integer")
        elif isinstance(page_count, int) and page_number > page_count:
            errors.append(f"manifest candidates[{index}].page_number exceeds manifest page_count")
        preset_type = candidate.get("preset_type")
        if not isinstance(preset_type, str) or not preset_type:
            errors.append(f"manifest candidates[{index}].preset_type must be non-empty")
        detection_reason = candidate.get("detection_reason")
        if detection_reason is not None and (
            not isinstance(detection_reason, list)
            or not all(isinstance(reason, str) and reason for reason in detection_reason)
        ):
            errors.append(f"manifest candidates[{index}].detection_reason must be a list of non-empty strings")
    duplicate_candidate_ids = sorted(
        candidate_id
        for candidate_id, count in Counter(seen_candidate_ids).items()
        if count > 1
    )
    if duplicate_candidate_ids:
        errors.append(f"manifest candidate_id values must be unique: {duplicate_candidate_ids}")
    return {
        "schema": "pdf_lab.second_pass.candidate_manifest_validation.v1",
        "ok": not errors,
        "errors": errors,
        "candidate_count": len(candidates),
        "declared_candidate_count": declared_candidate_count,
    }


def page_features(manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    features: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "candidate_ids": [],
            "preset_counts": Counter(),
            "score": 0.0,
            "reasons": set(),
        }
    )
    for candidate in manifest.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        page = _page_number(candidate)
        preset = str(candidate.get("preset_type") or "text")
        features[page]["candidate_ids"].append(candidate["candidate_id"])
        features[page]["preset_counts"][preset] += 1
        features[page]["score"] += _candidate_weight(candidate)
        detection_reason = candidate.get("detection_reason")
        for reason in detection_reason if isinstance(detection_reason, list) else []:
            features[page]["reasons"].add(str(reason))
    out: dict[int, dict[str, Any]] = {}
    for page, value in features.items():
        out[page] = {
            "candidate_ids": sorted(value["candidate_ids"]),
            "preset_counts": dict(sorted(value["preset_counts"].items())),
            "score": round(float(value["score"]), 6),
            "reasons": sorted(value["reasons"]),
        }
    return out


def stratify_candidates(manifest: dict[str, Any]) -> dict[str, set[int]]:
    page_count = int(manifest.get("page_count") or 0)
    strata: dict[str, set[int]] = defaultdict(set)
    candidate_pages: set[int] = set()
    for candidate in manifest.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        page = _page_number(candidate)
        candidate_pages.add(page)
        preset = str(candidate.get("preset_type") or "text")
        strata[f"preset:{preset}"].add(page)
        if preset in HIGH_RISK_TYPES:
            strata["risk:high"].add(page)
        detection_reason = candidate.get("detection_reason")
        reasons = set(detection_reason if isinstance(detection_reason, list) else [])
        if "boundary_geometry" in reasons:
            strata["geometry:boundary"].add(page)
        if "frontmatter_or_early_page" in reasons:
            strata["position:frontmatter"].add(page)
        if "late_document_page" in reasons:
            strata["position:late_document"].add(page)
    if page_count:
        for page in sorted(candidate_pages):
            if page <= min(page_count, 20):
                strata["position:first_20"].add(page)
            if page >= max(1, int(page_count * 0.85)):
                strata["position:last_15_percent"].add(page)
    return dict(strata)


def _weighted_choice_without_replacement(
    rng: random.Random,
    pages: list[int],
    scores: dict[int, float],
    count: int,
) -> list[int]:
    remaining = list(dict.fromkeys(sorted(pages)))
    selected: list[int] = []
    while remaining and len(selected) < count:
        total = sum(max(0.001, scores.get(page, 1.0)) for page in remaining)
        pick = rng.random() * total
        cursor = 0.0
        chosen = remaining[-1]
        for page in remaining:
            cursor += max(0.001, scores.get(page, 1.0))
            if cursor >= pick:
                chosen = page
                break
        selected.append(chosen)
        remaining.remove(chosen)
    return selected


def _priority_strata(strata: dict[str, set[int]]) -> set[str]:
    return {
        stratum
        for stratum in strata
        if stratum.startswith("preset:") or stratum in CORE_COVERAGE_STRATA
    }


def _sample_size_with_stratum_capacity(*, required_stratum_slots: int, random_reserve_fraction: float) -> int:
    if required_stratum_slots <= 0:
        return 0
    for sample_size in range(required_stratum_slots, required_stratum_slots * 4 + 1):
        reserve = max(0, int(round(sample_size * random_reserve_fraction)))
        if sample_size - reserve >= required_stratum_slots:
            return sample_size
    return required_stratum_slots


def _recommended_min_sample_size(
    *,
    candidate_page_count: int,
    priority_stratum_count: int,
    random_reserve_fraction: float,
) -> int:
    if candidate_page_count <= 0:
        return 0
    scale_floor = 12 if candidate_page_count >= 50 else min(candidate_page_count, 6)
    scale_probe = int(math.ceil(math.sqrt(candidate_page_count)))
    stratum_capacity_floor = _sample_size_with_stratum_capacity(
        required_stratum_slots=priority_stratum_count,
        random_reserve_fraction=random_reserve_fraction,
    )
    return min(candidate_page_count, max(scale_floor, scale_probe, stratum_capacity_floor))


def build_sampling_audit(
    *,
    manifest: dict[str, Any],
    strata: dict[str, set[int]],
    all_pages: list[int],
    selected_pages: list[int],
    probabilistic_selected_pages: list[int],
    stratum_records: list[dict[str, Any]],
    sample_size: int,
    seed: int,
    random_reserve_fraction: float,
    requested_forced_pages: list[int] | None = None,
    accepted_forced_pages: list[int] | None = None,
    rejected_forced_pages: list[int] | None = None,
) -> dict[str, Any]:
    selected = set(selected_pages)
    candidate_pages = sorted(all_pages)
    priority = _priority_strata(strata)
    covered = sorted(stratum for stratum, pages in strata.items() if selected & pages)
    missed = sorted(stratum for stratum, pages in strata.items() if not selected & pages)
    priority_covered = sorted(stratum for stratum in priority if stratum in covered)
    priority_missed = sorted(stratum for stratum in priority if stratum in missed)
    recommended_min = _recommended_min_sample_size(
        candidate_page_count=len(candidate_pages),
        priority_stratum_count=len(priority),
        random_reserve_fraction=random_reserve_fraction,
    )
    warnings: list[str] = []
    if sample_size < recommended_min:
        warnings.append(
            f"requested sample_size {sample_size} is below recommended minimum {recommended_min} "
            f"for {len(candidate_pages)} candidate pages and {len(priority)} priority strata"
        )
    if rejected_forced_pages:
        warnings.append(f"forced pages without candidate evidence were ignored: {sorted(rejected_forced_pages)}")
    if priority_missed:
        warnings.append(f"priority strata not represented in selected pages: {priority_missed}")
    reserve_records = [record for record in stratum_records if record["stratum"].startswith("reserve:")]
    if random_reserve_fraction > 0 and not reserve_records and len(selected_pages) < len(candidate_pages):
        warnings.append("weighted random reserve did not select an additional page")
    finite_population_fraction = round(len(selected_pages) / max(1, len(candidate_pages)), 6)
    statistical_significance_basis = {
        "method": "stratified_priority_coverage_plus_weighted_random_reserve",
        "seed": seed,
        "candidate_page_population": len(candidate_pages),
        "selected_page_count": len(selected_pages),
        "probabilistic_selected_page_count": len(probabilistic_selected_pages),
        "accepted_forced_page_count": len(accepted_forced_pages or []),
        "forced_pages_are_additive": True,
        "finite_population_fraction": finite_population_fraction,
        "sqrt_population_floor": int(math.ceil(math.sqrt(len(candidate_pages)))) if candidate_pages else 0,
        "large_document_floor": 12 if len(candidate_pages) >= 50 else min(len(candidate_pages), 6),
        "priority_stratum_count": len(priority),
        "recommended_min_sample_size": recommended_min,
        "random_reserve_fraction": random_reserve_fraction,
        "random_reserve_record_count": len(reserve_records),
        "adequacy_rule": (
            "adequate_sample_size && adequate_for_priority_strata && "
            "selected_count >= recommended_min_sample_size"
        ),
        "adequate": len(selected_pages) >= recommended_min and not priority_missed,
    }
    return {
        "schema": "pdf_lab.second_pass.sampling_audit.v1",
        "pdf_id": manifest.get("pdf_id"),
        "page_count": manifest.get("page_count"),
        "candidate_count": manifest.get("candidate_count"),
        "candidate_page_count": len(candidate_pages),
        "requested_sample_size": sample_size,
        "seed": seed,
        "selected_count": len(selected_pages),
        "probabilistic_selected_count": len(probabilistic_selected_pages),
        "forced_pages_are_additive": True,
        "recommended_min_sample_size": recommended_min,
        "requested_forced_pages": sorted(requested_forced_pages or []),
        "accepted_forced_pages": sorted(accepted_forced_pages or []),
        "rejected_forced_pages": sorted(rejected_forced_pages or []),
        "random_reserve_fraction": random_reserve_fraction,
        "priority_strata": sorted(priority),
        "priority_strata_count": len(priority),
        "covered_priority_strata": priority_covered,
        "missed_priority_strata": priority_missed,
        "covered_strata": covered,
        "missed_strata": missed,
        "strata_coverage_ratio": round(len(covered) / max(1, len(strata)), 6),
        "priority_strata_coverage_ratio": round(len(priority_covered) / max(1, len(priority)), 6),
        "statistical_significance_basis": statistical_significance_basis,
        "selection_records": stratum_records,
        "adequate_for_priority_strata": not priority_missed,
        "adequate_sample_size": len(selected_pages) >= recommended_min,
        "warnings": warnings,
    }


def select_page_cases(
    manifest: dict[str, Any],
    *,
    sample_size: int,
    seed: int,
    min_per_stratum: int = 1,
    random_reserve_fraction: float = 0.20,
    forced_pages: list[int] | None = None,
) -> dict[str, Any]:
    if sample_size < 1:
        raise ValueError("sample_size must be >= 1")
    if min_per_stratum < 1:
        raise ValueError("min_per_stratum must be >= 1")
    if not 0 <= random_reserve_fraction < 1:
        raise ValueError("random_reserve_fraction must be >= 0 and < 1")
    manifest_validation = validate_candidate_manifest(manifest)
    if not manifest_validation["ok"]:
        raise ValueError("invalid candidate manifest: " + "; ".join(manifest_validation["errors"]))
    rng = random.Random(seed)
    features = page_features(manifest)
    strata = stratify_candidates(manifest)
    all_pages = sorted(features)
    requested_forced_pages = sorted(set(int(page) for page in (forced_pages or [])))
    candidate_page_set = set(all_pages)
    accepted_forced_pages = [page for page in requested_forced_pages if page in candidate_page_set]
    rejected_forced_pages = [page for page in requested_forced_pages if page not in candidate_page_set]
    if not all_pages:
        selected_pages: list[int] = []
        stratum_records = []
    else:
        forced_page_set = set(accepted_forced_pages)
        selected: set[int] = set()
        stratum_records = []
        if accepted_forced_pages:
            stratum_records.append(
                {
                    "stratum": "forced:human_annotated",
                    "candidate_page_count": len(accepted_forced_pages),
                    "selected_pages": accepted_forced_pages,
                }
            )
        prioritized_strata = sorted(
            strata.items(),
            key=lambda item: (
                0 if item[0].startswith("preset:") else 1,
                len(item[1]),
                item[0],
            ),
        )
        reserve = max(0, int(round(sample_size * random_reserve_fraction)))
        stratum_budget = max(0, sample_size - reserve)
        for stratum, pages in prioritized_strata:
            if len(selected) >= stratum_budget:
                break
            remaining = [page for page in sorted(pages) if page not in selected and page not in forced_page_set]
            if not remaining:
                continue
            count = min(min_per_stratum, stratum_budget - len(selected), len(remaining))
            picked = _weighted_choice_without_replacement(
                rng,
                remaining,
                {page: features.get(page, {}).get("score", 1.0) for page in remaining},
                count,
            )
            selected.update(picked)
            stratum_records.append({"stratum": stratum, "candidate_page_count": len(pages), "selected_pages": picked})

        remaining_pages = [page for page in all_pages if page not in selected and page not in forced_page_set]
        reserve_count = max(0, sample_size - len(selected))
        random_picks = _weighted_choice_without_replacement(
            rng,
            remaining_pages,
            {page: features.get(page, {}).get("score", 1.0) for page in remaining_pages},
            reserve_count,
        )
        selected.update(random_picks)
        probabilistic_selected_pages = sorted(selected)
        selected.update(accepted_forced_pages)
        selected_pages = sorted(selected)
        if random_picks:
            stratum_records.append({"stratum": "reserve:weighted_random", "candidate_page_count": len(remaining_pages), "selected_pages": random_picks})
    if not all_pages:
        probabilistic_selected_pages = []

    sampling_audit = build_sampling_audit(
        manifest=manifest,
        strata=strata,
        all_pages=all_pages,
        selected_pages=selected_pages,
        probabilistic_selected_pages=probabilistic_selected_pages,
        stratum_records=stratum_records,
        sample_size=sample_size,
        seed=seed,
        random_reserve_fraction=random_reserve_fraction,
        requested_forced_pages=requested_forced_pages,
        accepted_forced_pages=accepted_forced_pages,
        rejected_forced_pages=rejected_forced_pages,
    )
    page_cases = []
    total_candidate_count = max(1, int(manifest.get("candidate_count") or 0))
    total_page_score = max(0.001, sum(max(0.001, float(features.get(page, {}).get("score", 1.0))) for page in all_pages))
    for rank, page in enumerate(selected_pages, start=1):
        page_feature = features.get(page, {"candidate_ids": [], "preset_counts": {}, "score": 0.0, "reasons": []})
        forced_by_human = page in set(accepted_forced_pages)
        selected_strata = sorted(
            stratum
            for stratum, pages in strata.items()
            if page in pages
        )
        candidate_count = len(page_feature["candidate_ids"])
        base_weight_probability = min(
            1.0,
            (sample_size * max(0.001, float(page_feature["score"]))) / total_page_score,
        )
        candidate_share_probability = min(1.0, max(candidate_count, 1) / total_candidate_count)
        selection_probability_estimate = 1.0 if forced_by_human else round(max(base_weight_probability, candidate_share_probability), 8)
        selection_probability_basis = (
            {
                "method": "forced_human_annotation",
                "forced_page": True,
                "weighted_page_score_inclusion_estimate": round(base_weight_probability, 8),
                "candidate_share_estimate": round(candidate_share_probability, 8),
                "page_score": page_feature["score"],
                "total_page_score": round(total_page_score, 6),
                "candidate_count_on_page": candidate_count,
                "total_candidate_count": total_candidate_count,
            }
            if forced_by_human
            else {
                "method": "max(weighted_page_score_inclusion_estimate,candidate_share_estimate)",
                "forced_page": False,
                "weighted_page_score_inclusion_estimate": round(base_weight_probability, 8),
                "candidate_share_estimate": round(candidate_share_probability, 8),
                "page_score": page_feature["score"],
                "total_page_score": round(total_page_score, 6),
                "candidate_count_on_page": candidate_count,
                "total_candidate_count": total_candidate_count,
            }
        )
        page_cases.append(
            {
                "case_id": f"page_case_{rank:04d}_p{page:04d}",
                "page_number": page,
                "page_index": page - 1,
                "candidate_ids": page_feature["candidate_ids"],
                "preset_counts": page_feature["preset_counts"],
                "selection_score": page_feature["score"],
                "strata": selected_strata,
                "forced_by_human_annotation": forced_by_human,
                "selection_probability_estimate": selection_probability_estimate,
                "selection_probability_basis": selection_probability_basis,
                "selection_reason": _selection_reason(page_feature, selected_strata, forced_by_human=forced_by_human),
            }
        )

    return {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "created_at": utc_now(),
        "manifest_schema": manifest.get("schema"),
        "manifest_validation": manifest_validation,
        "pdf_id": manifest.get("pdf_id"),
        "pdf_path": manifest.get("pdf_path"),
        "page_count": manifest.get("page_count"),
        "seed": seed,
        "requested_sample_size": sample_size,
        "forced_pages": {
            "requested": requested_forced_pages,
            "accepted": accepted_forced_pages,
            "rejected": rejected_forced_pages,
        },
        "selected_count": len(page_cases),
        "selected_pages": [case["page_number"] for case in page_cases],
        "probabilistic_selected_pages": probabilistic_selected_pages,
        "strata": [
            {"stratum": stratum, "page_count": len(pages)}
            for stratum, pages in sorted(strata.items())
        ],
        "sampling_audit": sampling_audit,
        "page_cases": page_cases,
    }


def _selection_reason(page_feature: dict[str, Any], strata: list[str], *, forced_by_human: bool = False) -> list[str]:
    reasons: list[str] = []
    if forced_by_human:
        reasons.append("human_annotated_page")
    if any(stratum.startswith("preset:") and stratum.split(":", 1)[1] in HIGH_RISK_TYPES for stratum in strata):
        reasons.append("high_risk_preset")
    if "risk:high" in strata:
        reasons.append("high_risk_candidate")
    if any(stratum.startswith("position:") for stratum in strata):
        reasons.append("document_position_stratum")
    if "geometry:boundary" in strata:
        reasons.append("boundary_geometry")
    if page_feature.get("score", 0) > 10:
        reasons.append("high_candidate_score")
    return reasons or ["stratified_sample"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--sample-size", type=int, default=12)
    parser.add_argument("--seed", type=int, default=530800)
    parser.add_argument("--min-per-stratum", type=int, default=1)
    parser.add_argument("--random-reserve-fraction", type=float, default=0.20)
    parser.add_argument("--forced-pages-json", type=Path)
    args = parser.parse_args()

    if args.sample_size < 1:
        print("--sample-size must be >= 1", file=sys.stderr)
        return 2
    manifest = load_json(args.manifest)
    result = select_page_cases(
        manifest,
        sample_size=args.sample_size,
        seed=args.seed,
        min_per_stratum=args.min_per_stratum,
        random_reserve_fraction=args.random_reserve_fraction,
        forced_pages=load_forced_pages(args.forced_pages_json),
    )
    write_json(args.out, result)
    print(json.dumps({"out": str(args.out), "selected_pages": result["selected_pages"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
