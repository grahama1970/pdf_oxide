#!/usr/bin/env python3
"""Run OpenCode CLI JSON mode with event/heartbeat artifacts.

This is a thin adapter for the current local OpenCode server shape, where
`opencode run --attach ... --format json` works but the full documented HTTP
session API is not exposed on the tested port.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATUS_VALUES = {"patch_recommended", "no_patch_needed", "blocked"}
OWNER_VALUES = {"extractor", "materializer", "prompt", "preset", "test", "unknown"}
CHANGE_TYPES = {"modify", "add_test", "add_helper", "no_change"}
RISK_VALUES = {"low", "medium", "high"}
FORBIDDEN_TOOLS = {"edit", "write", "bash", "shell", "websearch", "scillm", "test"}
ACCEPTED_EVIDENCE_PREFIXES = ("accepted_evidence:",)
FORBIDDEN_CLAIM_PATTERNS = [
    re.compile(r"\bverified\b", re.IGNORECASE),
    re.compile(r"\bfixed\b", re.IGNORECASE),
    re.compile(r"\bgreen\b", re.IGNORECASE),
    re.compile(r"\btests?\s+passed\b", re.IGNORECASE),
    re.compile(r"\bready\s+to\s+(merge|ship|close)\b", re.IGNORECASE),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _path_exists(repo_dir: Path, value: str) -> bool:
    if value.startswith(ACCEPTED_EVIDENCE_PREFIXES):
        return True
    path = Path(value)
    if path.is_absolute():
        return path.exists()
    return (repo_dir / path).exists() or path.exists()


def _parent_exists(repo_dir: Path, value: str) -> bool:
    path = Path(value)
    if path.is_absolute():
        return path.parent.exists()
    return (repo_dir / path).parent.exists() or path.parent.exists()


def event_tool_name(event: dict[str, Any]) -> str | None:
    part = event.get("part") if isinstance(event.get("part"), dict) else {}
    candidates = [
        event.get("tool"),
        event.get("toolName"),
        event.get("name"),
        part.get("tool"),
        part.get("toolName"),
        part.get("name"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return None


def is_tool_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type") or "").lower()
    part = event.get("part") if isinstance(event.get("part"), dict) else {}
    part_type = str(part.get("type") or "").lower()
    return "tool" in event_type or "tool" in part_type or event_tool_name(event) is not None


def validate_analyzer_output(
    parsed: dict[str, Any] | None,
    *,
    final_text: str | None,
    repo_dir: Path,
    tool_count: int,
    min_tool_calls: int,
    max_tool_calls: int,
    forbidden_tool_events: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if parsed is None:
        return False, ["parsed JSON object is missing"]

    if tool_count < min_tool_calls:
        errors.append(f"tool_count {tool_count} is below min_tool_calls {min_tool_calls}")
    if tool_count > max_tool_calls:
        errors.append(f"tool_count {tool_count} exceeds max_tool_calls {max_tool_calls}")
    if forbidden_tool_events:
        tools = ", ".join(sorted({str(item.get("tool") or "unknown") for item in forbidden_tool_events}))
        errors.append(f"forbidden tool event observed: {tools}")

    text_for_claims = final_text or json.dumps(parsed, sort_keys=True)
    for pattern in FORBIDDEN_CLAIM_PATTERNS:
        match = pattern.search(text_for_claims)
        if match:
            errors.append(f"forbidden closure claim: {match.group(0)}")

    status = parsed.get("status")
    if status not in STATUS_VALUES:
        errors.append("status must be one of " + ", ".join(sorted(STATUS_VALUES)))
    if not _is_nonempty_str(parsed.get("candidate_id")):
        errors.append("candidate_id must be a non-empty string")
    if parsed.get("likely_owner") not in OWNER_VALUES:
        errors.append("likely_owner must be one of " + ", ".join(sorted(OWNER_VALUES)))
    if not _is_nonempty_str(parsed.get("residual_risk")):
        errors.append("residual_risk must be a non-empty string")

    evidence_used = parsed.get("evidence_used")
    if not isinstance(evidence_used, list) or not evidence_used:
        errors.append("evidence_used must be a non-empty list")
    else:
        for index, evidence in enumerate(evidence_used):
            if not isinstance(evidence, dict):
                errors.append(f"evidence_used[{index}] must be an object")
                continue
            path = evidence.get("path")
            reason = evidence.get("reason")
            if not _is_nonempty_str(path):
                errors.append(f"evidence_used[{index}].path must be a non-empty string")
            elif not _path_exists(repo_dir, path):
                errors.append(f"evidence_used[{index}].path does not exist: {path}")
            if not _is_nonempty_str(reason):
                errors.append(f"evidence_used[{index}].reason must be a non-empty string")

    patch_recommendations = parsed.get("patch_recommendations")
    if not isinstance(patch_recommendations, list):
        errors.append("patch_recommendations must be a list")
    elif status == "patch_recommended" and not patch_recommendations:
        errors.append("patch_recommended output must include patch_recommendations")
    else:
        for index, recommendation in enumerate(patch_recommendations):
            if not isinstance(recommendation, dict):
                errors.append(f"patch_recommendations[{index}] must be an object")
                continue
            file_path = recommendation.get("file")
            change_type = recommendation.get("change_type")
            if not _is_nonempty_str(file_path):
                errors.append(f"patch_recommendations[{index}].file must be a non-empty string")
            elif change_type in {"modify", "no_change"} and not _path_exists(repo_dir, file_path):
                errors.append(f"patch_recommendations[{index}].file does not exist: {file_path}")
            elif change_type in {"add_test", "add_helper"} and not _parent_exists(repo_dir, file_path):
                errors.append(f"patch_recommendations[{index}].file parent does not exist: {file_path}")
            if change_type not in CHANGE_TYPES:
                errors.append(f"patch_recommendations[{index}].change_type is invalid")
            for key in ("rationale", "patch_sketch"):
                if not _is_nonempty_str(recommendation.get(key)):
                    errors.append(f"patch_recommendations[{index}].{key} must be a non-empty string")
            if recommendation.get("risk") not in RISK_VALUES:
                errors.append(f"patch_recommendations[{index}].risk is invalid")

    tests_to_run = parsed.get("tests_to_run")
    if not isinstance(tests_to_run, list) or not all(_is_nonempty_str(item) for item in tests_to_run):
        errors.append("tests_to_run must be a list of non-empty strings")
    elif status == "patch_recommended" and not tests_to_run:
        errors.append("patch_recommended output must include tests_to_run")

    acceptance_checks = parsed.get("acceptance_checks")
    if not isinstance(acceptance_checks, list) or not all(
        _is_nonempty_str(item) for item in acceptance_checks
    ):
        errors.append("acceptance_checks must be a list of non-empty strings")
    elif status == "patch_recommended" and not acceptance_checks:
        errors.append("patch_recommended output must include acceptance_checks")

    return not errors, errors


def parse_jsonish(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attach", default="http://127.0.0.1:34107")
    parser.add_argument("--dir", default=str(Path.cwd()))
    parser.add_argument("--agent", required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--session")
    parser.add_argument("--deadline-s", type=float, default=300.0)
    parser.add_argument("--idle-s", type=float, default=60.0)
    parser.add_argument("--heartbeat-s", type=float, default=5.0)
    parser.add_argument("--min-tool-calls", type=int, default=0)
    parser.add_argument("--max-tool-calls", type=int, default=8)
    args = parser.parse_args()

    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    events_path = args.artifact_dir / "events.jsonl"
    heartbeats_path = args.artifact_dir / "heartbeats.jsonl"
    result_path = args.artifact_dir / "stream_result.json"
    stderr_path = args.artifact_dir / "stderr.txt"

    prompt = args.prompt_file.read_text(encoding="utf-8")
    cmd = [
        "opencode",
        "run",
        "--attach",
        args.attach,
        "--dir",
        args.dir,
        "--format",
        "json",
    ]
    if args.session:
        cmd.extend(["--session", args.session])
    cmd.extend(["--agent", args.agent])
    cmd.append(prompt)

    started = time.monotonic()
    last_event = started
    next_heartbeat = started
    final_text: str | None = None
    session_id: str | None = args.session
    tool_count = 0
    event_count = 0
    forbidden_tool_events: list[dict[str, Any]] = []
    terminal_reason: str | None = None
    stderr_chunks: list[str] = []

    proc = subprocess.Popen(
        cmd,
        cwd=args.dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        preexec_fn=os.setsid,
    )

    selector = selectors.DefaultSelector()
    assert proc.stdout is not None
    assert proc.stderr is not None
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")

    with events_path.open("w", encoding="utf-8") as events_f, heartbeats_path.open("w", encoding="utf-8") as hb_f:
        while True:
            now = time.monotonic()
            if now >= next_heartbeat:
                hb = {
                    "ts": utc_now(),
                    "elapsed_s": round(now - started, 3),
                    "pid": proc.pid,
                    "session_id": session_id,
                    "event_count": event_count,
                    "tool_count": tool_count,
                    "proc_returncode": proc.poll(),
                }
                hb_f.write(json.dumps(hb, sort_keys=True) + "\n")
                hb_f.flush()
                next_heartbeat = now + args.heartbeat_s

            if proc.poll() is not None:
                for stream, name in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
                    while True:
                        line = stream.readline()
                        if not line:
                            break
                        if name == "stdout":
                            events_f.write(line)
                            events_f.flush()
                        else:
                            stderr_chunks.append(line)
                terminal_reason = terminal_reason or "process_exited"
                break

            if (now - started) >= args.deadline_s:
                terminal_reason = "deadline_exceeded"
                os.killpg(proc.pid, signal.SIGTERM)
                break
            if (now - last_event) >= args.idle_s:
                terminal_reason = "idle_timeout"
                os.killpg(proc.pid, signal.SIGTERM)
                break

            ready = selector.select(timeout=min(0.5, max(0.0, next_heartbeat - now)))
            for key, _ in ready:
                line = key.fileobj.readline()
                if not line:
                    continue
                if key.data == "stderr":
                    stderr_chunks.append(line)
                    continue
                last_event = time.monotonic()
                event_count += 1
                events_f.write(line)
                events_f.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session_id = event.get("sessionID") or session_id
                if is_tool_event(event):
                    tool_count += 1
                    tool_name = event_tool_name(event)
                    if tool_name and any(forbidden in tool_name for forbidden in FORBIDDEN_TOOLS):
                        forbidden_tool_events.append({"tool": tool_name, "event": event})
                        terminal_reason = "forbidden_event"
                        os.killpg(proc.pid, signal.SIGTERM)
                        break
                    if tool_count > args.max_tool_calls:
                        terminal_reason = "tool_budget_exceeded"
                        os.killpg(proc.pid, signal.SIGTERM)
                        break
                if event.get("type") == "text":
                    part = event.get("part") if isinstance(event.get("part"), dict) else {}
                    text = part.get("text")
                    if isinstance(text, str):
                        final_text = text

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        terminal_reason = f"{terminal_reason}+sigkill"

    stderr_path.write_text("".join(stderr_chunks), encoding="utf-8")
    parsed = parse_jsonish(final_text)
    validation_ok, validation_errors = validate_analyzer_output(
        parsed,
        final_text=final_text,
        repo_dir=Path(args.dir),
        tool_count=tool_count,
        min_tool_calls=args.min_tool_calls,
        max_tool_calls=args.max_tool_calls,
        forbidden_tool_events=forbidden_tool_events,
    )
    if terminal_reason == "process_exited" and final_text is None:
        terminal_reason = "no_text_event"
    if terminal_reason == "process_exited" and parsed is None:
        terminal_reason = "schema_invalid"
    if terminal_reason == "process_exited" and not validation_ok:
        terminal_reason = "schema_invalid"

    result = {
        "schema": "pdf_oxide.opencode_json_stream_result.v1",
        "started_at": utc_now(),
        "command": cmd[:-1] + ["<prompt>"],
        "exit_code": proc.returncode,
        "terminal_reason": terminal_reason,
        "session_id": session_id,
        "event_count": event_count,
        "tool_count": tool_count,
        "min_tool_calls": args.min_tool_calls,
        "max_tool_calls": args.max_tool_calls,
        "forbidden_tool_events": forbidden_tool_events,
        "events_path": str(events_path),
        "heartbeats_path": str(heartbeats_path),
        "stderr_path": str(stderr_path),
        "final_text": final_text,
        "parsed": parsed,
        "validation_errors": validation_errors,
        "ok": proc.returncode == 0 and terminal_reason == "process_exited" and validation_ok,
    }
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
