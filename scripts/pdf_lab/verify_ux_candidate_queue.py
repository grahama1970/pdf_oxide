#!/usr/bin/env python3
"""Audit a PDF Lab UX candidate queue against disk artifacts and signoffs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_optional_json(path: Path) -> tuple[Any | None, str | None]:
    if not path.exists():
        return None, f"missing:{path}"
    try:
        return _read_json(path), None
    except json.JSONDecodeError as exc:
        return None, f"invalid_json:{path}:{exc}"


def _count_regions(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("regions", "elements", "expected_elements", "blocks"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
    return 0


def _agent_decision(agent_payload: Any) -> dict[str, Any]:
    if not isinstance(agent_payload, dict):
        return {}
    decision = agent_payload.get("agent_decision")
    if isinstance(decision, dict):
        return decision
    second_pass = agent_payload.get("second_pass")
    if isinstance(second_pass, dict):
        return second_pass
    return agent_payload


def _signoff_keys(signoffs_payload: Any, project_id: str) -> set[str]:
    if not isinstance(signoffs_payload, dict):
        return set()
    keys: set[str] = set()
    for container_key in ("signoffs", "decisions", "items"):
        container = signoffs_payload.get(container_key)
        if isinstance(container, dict):
            keys.update(str(key) for key in container)
        elif isinstance(container, list):
            for item in container:
                if isinstance(item, dict):
                    key = item.get("key") or item.get("id") or item.get("candidate_key")
                    if key is not None:
                        keys.add(str(key))
    keys.update(str(key) for key in signoffs_payload if isinstance(key, str))
    prefix = f"{project_id}::"
    return {key for key in keys if key.startswith(prefix)}


def _project_url_to_path(project_root: Path, url: Any) -> Path | None:
    if not isinstance(url, str) or not url:
        return None
    marker = f"/pdf-lab-projects/{project_root.name}/"
    if url.startswith(marker):
        return project_root / url[len(marker) :]
    if url.startswith("/"):
        return project_root.parent.parent / url.lstrip("/")
    return project_root / url


def audit_queue(project_root: Path, signoffs_path: Path | None) -> dict[str, Any]:
    project_path = project_root / "project.json"
    project = _read_json(project_path)
    project_id = str(project.get("project_id") or project.get("id") or project_root.name)
    pages = project.get("pages")
    if not isinstance(pages, list):
        raise SystemExit(f"project pages must be a list: {project_path}")

    signoffs_payload: Any = {}
    signoff_errors: list[str] = []
    if signoffs_path is not None:
        signoffs_payload, err = _load_optional_json(signoffs_path)
        if err:
            signoff_errors.append(err)
            signoffs_payload = {}
    signed_keys = _signoff_keys(signoffs_payload, project_id)

    page_rows: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("slug") or page.get("id") or page.get("page_id") or "")
        expected_path = _project_url_to_path(project_root, page.get("expected_elements_url"))
        second_pass_path = _project_url_to_path(project_root, page.get("second_pass_url"))
        page_png_path = _project_url_to_path(project_root, page.get("image_url"))
        page_dir = expected_path.parent if expected_path is not None else project_root / page_id
        required = {
            "expected_elements": expected_path or page_dir / "expected_elements.json",
            "agent_second_pass": second_pass_path or page_dir / "agent_second_pass.json",
            "release_extraction_blocks": page_dir / "release_extraction_blocks.json",
            "page_png": page_png_path or page_dir / "page.png",
            "bbox_overlay_png": page_dir / "bbox_overlay.png",
        }
        missing = [name for name, path in required.items() if not path.exists()]
        expected_payload, expected_err = _load_optional_json(required["expected_elements"])
        agent_payload, agent_err = _load_optional_json(required["agent_second_pass"])
        release_payload, release_err = _load_optional_json(required["release_extraction_blocks"])
        json_errors = [err for err in (expected_err, agent_err, release_err) if err]

        decision = _agent_decision(agent_payload)
        status = str(decision.get("status") or decision.get("decision") or "unknown")
        human_review_required = bool(decision.get("human_review_required", False))
        fix_error_requests = decision.get("fix_error_requests")
        reviewer_questions = decision.get("reviewer_questions") or decision.get("questions")
        signoff_key = f"{project_id}::{page_id}"
        has_signoff = signoff_key in signed_keys

        if has_signoff:
            queue_state = "human_signed_off"
        elif human_review_required:
            queue_state = "needs_human_signoff_or_amendment"
        elif status == "closure_rematerialized":
            queue_state = "machine_rematerialized_needs_artifact_sync_or_confirmation"
        else:
            queue_state = "unknown_requires_review"

        page_rows.append(
            {
                "page_id": page_id,
                "anchor_page": page.get("anchor_page") or page.get("page_number"),
                "title": page.get("title"),
                "project_status": page.get("status"),
                "required_files_present": not missing and not json_errors,
                "missing_files": missing,
                "json_errors": json_errors,
                "expected_region_count": _count_regions(expected_payload),
                "release_block_count": _count_regions(release_payload),
                "agent_status": status,
                "human_review_required": human_review_required,
                "fix_error_request_count": len(fix_error_requests)
                if isinstance(fix_error_requests, list)
                else 0,
                "reviewer_question_count": len(reviewer_questions)
                if isinstance(reviewer_questions, list)
                else 0,
                "signoff_key": signoff_key,
                "has_signoff": has_signoff,
                "queue_state": queue_state,
            }
        )

    summary = {
        "project_id": project_id,
        "total_candidate_pages": len(page_rows),
        "pages_with_required_files": sum(1 for row in page_rows if row["required_files_present"]),
        "machine_rematerialized_pages": sum(
            1 for row in page_rows if row["agent_status"] == "closure_rematerialized"
        ),
        "human_review_required_pages": sum(1 for row in page_rows if row["human_review_required"]),
        "signed_off_pages": sum(1 for row in page_rows if row["has_signoff"]),
        "pending_pages": sum(1 for row in page_rows if not row["has_signoff"]),
        "queue_states": {},
        "signoff_errors": signoff_errors,
    }
    for row in page_rows:
        state = row["queue_state"]
        summary["queue_states"][state] = summary["queue_states"].get(state, 0) + 1

    return {
        "project_path": str(project_path),
        "signoffs_path": str(signoffs_path) if signoffs_path else None,
        "summary": summary,
        "pages": page_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--signoffs", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    result = audit_queue(args.project_root, args.signoffs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
