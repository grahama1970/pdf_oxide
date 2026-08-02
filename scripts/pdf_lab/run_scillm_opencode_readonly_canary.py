#!/usr/bin/env python3
"""Run deterministic read-only canaries for scillm OpenCode serve and transport."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _import_pdf_lab_modules() -> tuple[Any, Any, Any]:
    import sys

    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    import prepare_isolated_code_root as prepare_mod  # noqa: PLC0415
    import run_page_second_pass_dag as page_dag  # noqa: PLC0415
    import run_second_pass_harness as harness_mod  # noqa: PLC0415

    return prepare_mod, page_dag, harness_mod


def run_git_status(code_root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["git", "-C", str(code_root), "status", "--short"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        "schema": "pdf_lab.second_pass.git_status_probe.v1",
        "code_root": str(code_root.resolve()),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "clean": completed.returncode == 0 and completed.stdout.strip() == "",
    }


def build_readonly_prompt(code_root: Path) -> str:
    return (
        "You are a read-only pdf-lab scillm/OpenCode canary executor. "
        "Inspect the mounted workspace enough to prove you can access it, then stop.\n\n"
        f"Workspace root: {code_root.resolve()}\n\n"
        "Hard contract:\n"
        "- Do not edit, create, delete, move, stage, or commit any file.\n"
        "- Run only read-only inspection commands such as pwd, git status --short, ls, and rg --files.\n"
        "- Return a concise assistant_text summary naming the workspace root and at least one repository path observed.\n"
        "- The resulting diff must be empty. This is a canary for transport and executor evidence, not a patch attempt."
    )


def call_opencode_serve_readonly(
    *,
    base_url: str,
    auth_token: str,
    caller_skill: str,
    code_root: Path,
    agent: str,
    skills: list[str],
    timeout_s: float,
    model: str | None,
) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    body: dict[str, Any] = {
        "prompt": build_readonly_prompt(code_root),
        "agent": agent,
        "skills": skills,
        "timeout_s": timeout_s,
        "cleanup_session": True,
        "cwd": str(code_root.resolve()),
        "scillm_metadata": {
            "graph_node": "scillm_opencode_serve_readonly_canary",
            "caller": "pdf-lab",
        },
    }
    if model:
        body["model"] = model
    response = httpx.post(
        f"{base_url.rstrip('/')}/v1/scillm/opencode/runs",
        headers={
            "Authorization": f"Bearer {auth_token}",
            "X-Caller-Skill": caller_skill,
            "Content-Type": "application/json",
        },
        json=body,
        timeout=timeout_s,
    )
    response.raise_for_status()
    return {
        "schema": "pdf_lab.second_pass.opencode_serve_readonly_canary_receipt.v1",
        "endpoint": "POST /v1/scillm/opencode/runs",
        "http_status": response.status_code,
        "request": body,
        "raw_response": response.json(),
    }


def call_transport_readonly(
    *,
    base_url: str,
    auth_token: str,
    caller_skill: str,
    code_root: Path,
    agent: str,
    skills: list[str],
    timeout_s: float,
    model: str | None,
) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    _, page_dag, _ = _import_pdf_lab_modules()
    root = f"{base_url.rstrip('/')}/v1/scillm/opencode/transport"
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "X-Caller-Skill": caller_skill,
        "Content-Type": "application/json",
    }
    create_body = {
        "dag_node_id": "pdf_lab_scillm_transport_readonly_canary",
        "workspace": str(code_root.resolve()),
        "title": "pdf-lab scillm transport read-only canary",
    }
    child_body = {
        "role": "canary",
        "agent": agent,
        "mode": "read_only",
        "title": "Read-only canary",
        "skills": skills,
    }
    message_body: dict[str, Any] = {
        "prompt": build_readonly_prompt(code_root),
        "agent": agent,
        "role": "canary",
        "stream": True,
        "timeout_s": timeout_s,
        "heartbeat_s": 15,
        "wait_idle": True,
        "skills": skills,
    }
    if model:
        message_body["model"] = model
    with httpx.Client(timeout=timeout_s) as client:
        create_response = client.post(f"{root}/runs", headers=headers, json=create_body)
        create_response.raise_for_status()
        create_raw = create_response.json()
        transport_run_id = create_raw["transport_run_id"]
        child_response = client.post(f"{root}/runs/{transport_run_id}/children", headers=headers, json=child_body)
        child_response.raise_for_status()
        child_raw = child_response.json()
        message_status_code: int | None = None
        try:
            with client.stream(
                "POST",
                f"{root}/runs/{transport_run_id}/message",
                headers=headers,
                json=message_body,
            ) as message_response:
                message_status_code = message_response.status_code
                message_response.raise_for_status()
                event_stream = page_dag.parse_transport_sse_response(message_response, max_elapsed_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 - replay may still preserve useful failure evidence.
            event_stream = page_dag.build_transport_session_error_stream(type(exc).__name__, str(exc))
        try:
            with client.stream(
                "GET",
                f"{root}/runs/{transport_run_id}/events/stream",
                headers=headers,
                timeout=timeout_s,
            ) as replay_response:
                replay_response.raise_for_status()
                replay_stream = page_dag.parse_transport_sse_response(
                    replay_response,
                    max_elapsed_s=10.0,
                    deadline_event_is_error=False,
                )
            event_stream = page_dag.merge_transport_event_streams(event_stream, replay_stream)
        except Exception as exc:  # noqa: BLE001 - replay is additive.
            event_stream["event_replay_error"] = {"error_type": type(exc).__name__, "error": str(exc)}
    return {
        "schema": "pdf_lab.second_pass.opencode_transport_readonly_canary_receipt.v1",
        "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
        "http_status": message_status_code,
        "transport_run_id": transport_run_id,
        "request": {
            "create_run_body": create_body,
            "create_child_body": child_body,
            "message_body": message_body,
        },
        "create_response": create_raw,
        "child_response": child_raw,
        "event_stream": event_stream,
        "message_response": event_stream.get("final_result") or {},
    }


def _assistant_text_from_serve(raw: dict[str, Any]) -> str:
    return str(raw.get("assistant_text") or raw.get("output") or raw.get("text") or "")


def _diff_from_payload(payload: dict[str, Any]) -> Any:
    return payload.get("diff", [])


def validate_serve_readonly_receipt(receipt: dict[str, Any], status_probe: dict[str, Any]) -> dict[str, Any]:
    raw = receipt.get("raw_response") if isinstance(receipt, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    status = raw.get("status")
    assistant_text = _assistant_text_from_serve(raw)
    diff = _diff_from_payload(raw)
    errors: list[str] = []
    if status not in {"completed", "success", "ok"}:
        errors.append(f"OpenCode serve status is not completed/success/ok: {status}")
    if not assistant_text.strip():
        errors.append("OpenCode serve canary returned no assistant_text/output/text")
    if bool(diff):
        errors.append("OpenCode serve read-only canary returned a non-empty diff")
    if not status_probe.get("clean"):
        errors.append("OpenCode serve read-only canary left the isolated worktree dirty")
    return {
        "schema": "pdf_lab.second_pass.opencode_serve_readonly_canary_validation.v1",
        "ok": not errors,
        "errors": errors,
        "status": status or "unknown",
        "assistant_text_present": bool(assistant_text.strip()),
        "diff_observed": diff if diff is not None else [],
        "worktree_clean": bool(status_probe.get("clean")),
        "git_status": status_probe,
    }


def validate_transport_readonly_receipt(receipt: dict[str, Any], status_probe: dict[str, Any]) -> dict[str, Any]:
    event_stream = receipt.get("event_stream") if isinstance(receipt, dict) else {}
    if not isinstance(event_stream, dict):
        event_stream = {}
    message = receipt.get("message_response") if isinstance(receipt, dict) else {}
    if not isinstance(message, dict):
        message = {}
    delivery_state = message.get("delivery_state") or message.get("status") or event_stream.get("delivery_state")
    assistant_text = str(message.get("assistant_text") or message.get("output") or message.get("text") or "")
    diff = _diff_from_payload(message)
    errors: list[str] = []
    if delivery_state not in {"completed", "acted", "idle_seen"}:
        errors.append(f"transport delivery_state is not completed/acted/idle_seen: {delivery_state}")
    if not event_stream.get("saw_message_completed"):
        errors.append("transport read-only canary did not observe message.completed")
    if event_stream.get("parse_errors"):
        errors.append("transport read-only canary stream contained parse errors")
    if event_stream.get("session_errors"):
        errors.append("transport read-only canary stream contained session errors")
    if event_stream.get("tool_errors"):
        errors.append("transport read-only canary stream contained tool errors")
    if event_stream.get("permission_requests"):
        errors.append("transport read-only canary requested permission")
    if not assistant_text.strip():
        errors.append("transport read-only canary returned no assistant_text/output/text")
    if bool(diff):
        errors.append("transport read-only canary returned a non-empty diff")
    if not status_probe.get("clean"):
        errors.append("transport read-only canary left the isolated worktree dirty")
    return {
        "schema": "pdf_lab.second_pass.opencode_transport_readonly_canary_validation.v1",
        "ok": not errors,
        "errors": errors,
        "delivery_state": delivery_state or "unknown",
        "assistant_text_present": bool(assistant_text.strip()),
        "diff_observed": diff if diff is not None else [],
        "worktree_clean": bool(status_probe.get("clean")),
        "git_status": status_probe,
    }


def run_canary(
    *,
    out_dir: Path,
    code_root: Path,
    prepare_isolated_dest: Path | None,
    prepare_force: bool,
    base_url: str,
    auth_token: str,
    caller_skill: str,
    timeout_s: float,
    agent: str,
    skills: list[str],
    model: str | None,
    mounted_workspace_prefixes: list[Path] | None,
) -> dict[str, Any]:
    prepare_mod, page_dag, harness_mod = _import_pdf_lab_modules()
    effective_model = page_dag.resolve_effective_opencode_model(
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        opencode_model=model,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    isolated_manifest = None
    effective_code_root = code_root.resolve()
    if prepare_isolated_dest:
        isolated_manifest = prepare_mod.prepare_isolated_code_root(
            source_root=REPO,
            dest_root=prepare_isolated_dest,
            include_paths=prepare_mod.DEFAULT_INCLUDE_PATHS,
            force=prepare_force,
        )
        effective_code_root = Path(isolated_manifest["dest_root"]).resolve()
    visibility = harness_mod.validate_scillm_live_code_root(
        code_root=effective_code_root,
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        mounted_prefixes=mounted_workspace_prefixes or harness_mod.parse_mounted_workspace_prefixes(),
        isolated_code_root_manifest=isolated_manifest,
    )
    write_json(out_dir / "scillm_code_root_visibility.json", visibility)
    preflight_serve = page_dag.preflight_scillm_surface(
        base_url=base_url,
        auth_token=auth_token,
        caller_skill=caller_skill,
        surface="opencode_serve",
        timeout_s=timeout_s,
    )
    preflight_transport = page_dag.preflight_scillm_surface(
        base_url=base_url,
        auth_token=auth_token,
        caller_skill=caller_skill,
        surface="opencode_transport",
        timeout_s=timeout_s,
    )
    write_json(out_dir / "preflight_opencode_serve.json", preflight_serve)
    write_json(out_dir / "preflight_opencode_transport.json", preflight_transport)

    serve_receipt: dict[str, Any] | None = None
    transport_receipt: dict[str, Any] | None = None
    serve_validation: dict[str, Any]
    transport_validation: dict[str, Any]
    if not visibility["ok"]:
        serve_validation = {
            "schema": "pdf_lab.second_pass.opencode_serve_readonly_canary_validation.v1",
            "ok": False,
            "errors": ["scillm_code_root_not_mounted"],
        }
        transport_validation = {
            "schema": "pdf_lab.second_pass.opencode_transport_readonly_canary_validation.v1",
            "ok": False,
            "errors": ["scillm_code_root_not_mounted"],
        }
    elif not preflight_serve["ok"] or not preflight_transport["ok"]:
        serve_validation = {
            "schema": "pdf_lab.second_pass.opencode_serve_readonly_canary_validation.v1",
            "ok": False,
            "errors": ["preflight_failed"],
        }
        transport_validation = {
            "schema": "pdf_lab.second_pass.opencode_transport_readonly_canary_validation.v1",
            "ok": False,
            "errors": ["preflight_failed"],
        }
    else:
        serve_receipt = call_opencode_serve_readonly(
            base_url=base_url,
            auth_token=auth_token,
            caller_skill=caller_skill,
            code_root=effective_code_root,
            agent=agent,
            skills=skills,
            timeout_s=timeout_s,
            model=effective_model,
        )
        write_json(out_dir / "opencode_serve_readonly_receipt.json", serve_receipt)
        serve_validation = validate_serve_readonly_receipt(serve_receipt, run_git_status(effective_code_root))
        write_json(out_dir / "opencode_serve_readonly_validation.json", serve_validation)
        transport_receipt = call_transport_readonly(
            base_url=base_url,
            auth_token=auth_token,
            caller_skill=caller_skill,
            code_root=effective_code_root,
            agent=agent,
            skills=skills,
            timeout_s=timeout_s,
            model=effective_model,
        )
        write_json(out_dir / "opencode_transport_readonly_receipt.json", transport_receipt)
        page_dag.write_transport_event_artifacts(out_dir, transport_receipt["event_stream"], prefix="readonly_")
        transport_validation = validate_transport_readonly_receipt(transport_receipt, run_git_status(effective_code_root))
    write_json(out_dir / "opencode_serve_readonly_validation.json", serve_validation)
    write_json(out_dir / "opencode_transport_readonly_validation.json", transport_validation)

    ok = bool(visibility["ok"] and preflight_serve["ok"] and preflight_transport["ok"] and serve_validation["ok"] and transport_validation["ok"])
    report = {
        "schema": "pdf_lab.second_pass.scillm_opencode_readonly_canary_report.v1",
        "created_at": utc_now(),
        "out_dir": str(out_dir),
        "code_root": str(effective_code_root),
        "requested_opencode_model": model,
        "opencode_model": effective_model,
        "opencode_model_defaulted": effective_model is not None and model is None,
        "isolated_code_root_manifest": isolated_manifest,
        "scillm_code_root_visibility": visibility,
        "preflight_opencode_serve": preflight_serve,
        "preflight_opencode_transport": preflight_transport,
        "opencode_serve_receipt": serve_receipt,
        "opencode_serve_validation": serve_validation,
        "opencode_transport_receipt": transport_receipt,
        "opencode_transport_validation": transport_validation,
        "terminal_status": "passed" if ok else "failed_closed",
        "errors": [
            *visibility.get("errors", []),
            *preflight_serve.get("errors", []),
            *preflight_transport.get("errors", []),
            *serve_validation.get("errors", []),
            *transport_validation.get("errors", []),
        ],
    }
    write_json(out_dir / "canary_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--code-root", type=Path, default=REPO)
    parser.add_argument("--prepare-isolated-code-root", type=Path, dest="prepare_isolated_dest")
    parser.add_argument("--prepare-code-root-force", action="store_true", dest="prepare_force")
    parser.add_argument("--scillm-base-url", default=os.environ.get("SCILLM_API_BASE", "http://localhost:4001"))
    parser.add_argument("--scillm-auth-token", default=os.environ.get("SCILLM_PROXY_KEY", "sk-dev-proxy-123"))
    parser.add_argument("--caller-skill", default="pdf-lab")
    parser.add_argument("--timeout-s", type=float, default=180.0)
    parser.add_argument("--opencode-agent", default="build")
    parser.add_argument("--opencode-skill", action="append", dest="skills")
    parser.add_argument("--opencode-model")
    parser.add_argument("--scillm-mounted-workspace-prefix", action="append", type=Path, dest="mounted_workspace_prefixes")
    args = parser.parse_args()
    report = run_canary(
        out_dir=args.out,
        code_root=args.code_root,
        prepare_isolated_dest=args.prepare_isolated_dest,
        prepare_force=args.prepare_force,
        base_url=args.scillm_base_url,
        auth_token=args.scillm_auth_token,
        caller_skill=args.caller_skill,
        timeout_s=args.timeout_s,
        agent=args.opencode_agent,
        skills=args.skills or ["memory", "scillm"],
        model=args.opencode_model,
        mounted_workspace_prefixes=args.mounted_workspace_prefixes,
    )
    print(json.dumps({"out": str(args.out), "terminal_status": report["terminal_status"]}, sort_keys=True))
    return 0 if report["terminal_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
