"""Poll scillm cursor_exec runs via events.jsonl and cursor-events.jsonl."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def exec_run_dir(run_id: str, artifact_root: Path | None = None) -> Path:
    root = artifact_root or Path("/tmp/scillm-exec")
    return root / run_id


def _as_workspace_path(workspace: Path, raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = workspace / path
    return path


def resolve_cursor_events_path(
    *,
    workspace: Path,
    run_id: str,
    result: dict[str, Any] | None = None,
) -> Path | None:
    """Prefer scillm result.cursor_events_path for this run_id; never pick newest glob."""
    if isinstance(result, dict):
        for key in ("cursor_events_path", "events_path"):
            raw = result.get(key)
            if isinstance(raw, str) and raw.strip():
                candidate = _as_workspace_path(workspace, raw.strip())
                if candidate.is_file() and candidate.name == "cursor-events.jsonl":
                    return candidate

    base = workspace / ".scillm" / "cursor-headless"
    if not base.is_dir():
        return None
    scoped = [
        path
        for path in base.glob("*/cursor-events.jsonl")
        if path.is_file() and run_id in path.parent.name
    ]
    if scoped:
        return max(scoped, key=lambda item: item.stat().st_mtime)

    return None


def find_cursor_events_path(workspace: Path) -> Path | None:
    """Deprecated: unscoped newest-file lookup. Use resolve_cursor_events_path."""
    base = workspace / ".scillm" / "cursor-headless"
    if not base.is_dir():
        return None
    candidates = [item for item in base.glob("*/cursor-events.jsonl") if item.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def _parse_jsonish(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        obj = json.loads(stripped)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(stripped[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def cursor_stream_terminal_status(event: dict[str, Any]) -> str | None:
    if event.get("type") != "result":
        return None
    if event.get("is_error") is True or event.get("subtype") == "error":
        return "error"
    if event.get("subtype") == "success" and event.get("is_error") is not True:
        return "success"
    return None


def ingest_cursor_events_file(path: Path) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)

    tool_call_count = sum(1 for e in events if e.get("type") == "tool_call")
    result_event: dict[str, Any] | None = None
    terminal_status: str | None = None
    for event in events:
        terminal = cursor_stream_terminal_status(event)
        if terminal is not None:
            result_event = event
            terminal_status = terminal

    return {
        "cursor_events_path": str(path),
        "event_line_count": len(events),
        "tool_call_count": tool_call_count,
        "result_event": result_event,
        "terminal_status": terminal_status,
        "last_event_type": events[-1].get("type") if events else None,
    }


def _stdout_events_may_contain_result(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") != "stdout":
            continue
        text = event.get("text")
        if not isinstance(text, str):
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and cursor_stream_terminal_status(parsed) is not None:
                return parsed
    return None


@dataclass
class ExecPollSnapshot:
    ts: str
    elapsed_s: float
    exec_events_path: str | None = None
    exec_event_line_count: int = 0
    cursor_events_path: str | None = None
    cursor_event_line_count: int = 0
    tool_call_count: int = 0
    terminal_status: str | None = None
    saw_terminal_result: bool = False
    http_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "elapsed_s": round(self.elapsed_s, 3),
            "exec_events_path": self.exec_events_path,
            "exec_event_line_count": self.exec_event_line_count,
            "cursor_events_path": self.cursor_events_path,
            "cursor_event_line_count": self.cursor_event_line_count,
            "tool_call_count": self.tool_call_count,
            "terminal_status": self.terminal_status,
            "saw_terminal_result": self.saw_terminal_result,
            "http_status": self.http_status,
        }


def poll_until_terminal_or_timeout(
    *,
    run_id: str,
    workspace: Path,
    scillm_url: str,
    headers: dict[str, str],
    timeout_s: float,
    poll_interval_s: float = 2.0,
    idle_timeout_s: float | None = None,
    max_tool_calls: int | None = None,
    http_done: Callable[[], bool],
    poll_trace_path: Path | None = None,
    exec_artifact_root: Path | None = None,
    cursor_events_hint: Path | None = None,
    scillm_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    trace: list[dict[str, Any]] = []
    last_cursor: dict[str, Any] = {}
    last_exec_lines = 0
    terminal_status: str | None = None
    result_event: dict[str, Any] | None = None
    exec_path = exec_run_dir(run_id, exec_artifact_root) / "events.jsonl"
    last_progress_at = started
    last_tool_count = 0
    last_event_count = 0

    while True:
        elapsed = time.monotonic() - started
        snap = ExecPollSnapshot(ts=utc_now(), elapsed_s=elapsed)
        snap.http_status = "done" if http_done() else "in_flight"

        if exec_path.is_file():
            lines = exec_path.read_text(encoding="utf-8", errors="replace").splitlines()
            snap.exec_events_path = str(exec_path)
            snap.exec_event_line_count = len(lines)
            last_exec_lines = len(lines)
            exec_events: list[dict[str, Any]] = []
            for line in lines[-200:]:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    exec_events.append(event)
            stdout_result = _stdout_events_may_contain_result(exec_events)
            if stdout_result is not None:
                result_event = stdout_result
                terminal_status = cursor_stream_terminal_status(stdout_result)

        cursor_path = cursor_events_hint
        if cursor_path is None or not cursor_path.is_file():
            cursor_path = resolve_cursor_events_path(
                workspace=workspace,
                run_id=run_id,
                result=scillm_result,
            )
        if cursor_path is not None:
            last_cursor = ingest_cursor_events_file(cursor_path)
            snap.cursor_events_path = last_cursor.get("cursor_events_path")
            snap.cursor_event_line_count = int(last_cursor.get("event_line_count") or 0)
            snap.tool_call_count = int(last_cursor.get("tool_call_count") or 0)
            if last_cursor.get("terminal_status"):
                terminal_status = str(last_cursor["terminal_status"])
                result_event = last_cursor.get("result_event")
                if isinstance(result_event, dict):
                    snap.saw_terminal_result = True
                    snap.terminal_status = terminal_status

        trace.append(snap.to_dict())
        if poll_trace_path is not None:
            poll_trace_path.parent.mkdir(parents=True, exist_ok=True)
            poll_trace_path.write_text(
                "\n".join(json.dumps(row, sort_keys=True) for row in trace) + "\n",
                encoding="utf-8",
            )

        tool_count = int(snap.tool_call_count or 0)
        event_count = int(snap.cursor_event_line_count or 0)
        if tool_count > last_tool_count or event_count > last_event_count:
            last_tool_count = tool_count
            last_event_count = event_count
            last_progress_at = time.monotonic()

        if max_tool_calls is not None and tool_count >= max_tool_calls:
            terminal_status = terminal_status or "tool_budget_exceeded"
            break

        if terminal_status in {"success", "error"}:
            break

        if (
            idle_timeout_s is not None
            and http_done()
            and (time.monotonic() - last_progress_at) >= idle_timeout_s
        ):
            terminal_status = terminal_status or "stalled"
            break

        if elapsed >= timeout_s:
            terminal_status = terminal_status or "timeout"
            break
        time.sleep(poll_interval_s)

    return {
        "run_id": run_id,
        "elapsed_s": round(time.monotonic() - started, 3),
        "exec_events_path": str(exec_path) if exec_path.is_file() else None,
        "exec_event_line_count": last_exec_lines,
        "cursor_events_path": last_cursor.get("cursor_events_path"),
        "cursor_event_line_count": last_cursor.get("event_line_count", 0),
        "tool_call_count": last_cursor.get("tool_call_count", 0),
        "terminal_status": terminal_status,
        "result_event": result_event,
        "saw_terminal_result": terminal_status in {"success", "error"},
        "poll_snapshots": len(trace),
    }



def _separator_chunk_error(error: Any) -> bool:
    if not isinstance(error, str):
        return False
    lowered = error.lower()
    return "separator" in lowered and "chunk" in lowered


def _reconcile_exec_ok(
    *,
    payload: dict[str, Any],
    result: dict[str, Any],
    parsed: dict[str, Any] | None,
    poll_summary: dict[str, Any],
    workspace: Path,
    run_id: str,
) -> tuple[bool, dict[str, Any]]:
    """Trust run-scoped cursor-events.jsonl terminal success over stale HTTP failure."""
    poll_summary = dict(poll_summary)
    events_path = resolve_cursor_events_path(workspace=workspace, run_id=run_id, result=result)
    if events_path is not None:
        ingested = ingest_cursor_events_file(events_path)
        poll_summary["cursor_events_path"] = ingested.get("cursor_events_path")
        poll_summary["cursor_event_line_count"] = ingested.get("event_line_count", 0)
        poll_summary["tool_call_count"] = ingested.get("tool_call_count", 0)
        poll_summary["terminal_status"] = ingested.get("terminal_status")
        poll_summary["result_event"] = ingested.get("result_event")
        poll_summary["saw_terminal_result"] = ingested.get("terminal_status") in {"success", "error"}

    terminal = poll_summary.get("terminal_status")
    stream_completed = bool(result.get("stream_completed")) or bool(result.get("recovered_from_stream"))
    poll_saw_result = bool(poll_summary.get("saw_terminal_result"))
    scillm_ok = payload.get("status") == "completed" and bool(result.get("ok"))

    ok = scillm_ok and isinstance(parsed, dict) and (stream_completed or poll_saw_result)
    recovered = False
    if not ok and terminal == "success" and isinstance(parsed, dict):
        ok = True
        recovered = True
    if not ok and terminal == "success" and poll_saw_result:
        failure_type = result.get("failure_type")
        error = result.get("error") or payload.get("error")
        if failure_type in {None, "", "process_error", "stream_read_error"} or _separator_chunk_error(error):
            ok = isinstance(parsed, dict)
            recovered = ok

    poll_summary["reconciled_ok"] = ok
    poll_summary["recovered_from_cursor_events"] = recovered
    return ok, poll_summary


PDF_ALLOWLIST = sorted([
    "python/pdf_oxide/extract_for_pdflab.py",
    "src/tables/mod.rs",
    "src/tables/text_assign.rs",
    "src/tables/types.rs",
    "src/extractors/block_classifier.rs",
])


def run_scillm_cursor_exec(
    repo: Path,
    *,
    prompt: str,
    skills_csv: str,
    artifact_dir: Path,
    label: str,
    profile: str,
    scillm_url: str,
    headers: dict[str, str],
    force: bool = False,
    timeout_s: float = 300.0,
    idle_timeout_s: float = 90.0,
    max_tool_calls: int | None = None,
    poll_interval_s: float = 2.0,
    exec_artifact_root: Path | None = None,
    allow_write_paths: list[str] | None = None,
) -> dict[str, Any]:
    timeout_s = min(float(timeout_s), 300.0)
    idle_timeout_s = min(float(idle_timeout_s), timeout_s / 2)
    run_id = f"pdf-lab-{label}-{artifact_dir.name}-{int(datetime.now(timezone.utc).timestamp())}"
    metadata: dict[str, Any] = {"skills": skills_csv}
    sandbox = "read-only"
    if force:
        sandbox = "workspace-write"
        metadata["cursor_force"] = True
        if allow_write_paths:
            metadata["allow_write_paths"] = allow_write_paths

    body = {
        "run_id": run_id,
        "id": label,
        "type": "cursor_exec",
        "model": profile,
        "cwd": str(repo.resolve()),
        "sandbox": sandbox,
        "timeout_s": timeout_s,
        "idle_timeout_s": idle_timeout_s,
        "metadata": metadata,
        "node_goal": label,
        "prompt": prompt,
    }
    request_path = artifact_dir / f"{label}_scillm_request.json"
    request_path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")

    poll_trace_path = artifact_dir / f"{label}_poll_trace.jsonl"
    response_holder: dict[str, Any] = {}
    error_holder: dict[str, str] = {}

    def _post() -> None:
        try:
            with httpx.Client(timeout=timeout_s + 180.0) as client:
                response_holder["response"] = client.post(
                    f"{scillm_url.rstrip('/')}/v1/scillm/exec",
                    headers=headers,
                    json=body,
                )
        except Exception as exc:
            error_holder["error"] = str(exc)

    started = datetime.now(timezone.utc)
    thread = threading.Thread(target=_post, daemon=True)
    thread.start()

    cursor_hint = resolve_cursor_events_path(workspace=repo, run_id=run_id)
    poll_summary = poll_until_terminal_or_timeout(
        run_id=run_id,
        workspace=repo,
        scillm_url=scillm_url,
        headers=headers,
        timeout_s=timeout_s,
        poll_interval_s=poll_interval_s,
        idle_timeout_s=idle_timeout_s,
        max_tool_calls=max_tool_calls,
        http_done=lambda: "response" in response_holder or "error" in error_holder,
        poll_trace_path=poll_trace_path,
        exec_artifact_root=exec_artifact_root,
        cursor_events_hint=cursor_hint,
    )

    thread.join(timeout=timeout_s + 180.0)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()

    poll_saw_result = bool(poll_summary.get("saw_terminal_result"))
    poll_terminal = poll_summary.get("terminal_status")
    poll_result_event = poll_summary.get("result_event")
    poll_parsed: dict[str, Any] | None = None
    if isinstance(poll_result_event, dict):
        raw = poll_result_event.get("result")
        if isinstance(raw, str):
            poll_parsed = _parse_jsonish(raw)

    if error_holder.get("error") and not poll_saw_result:
        receipt = {
            "label": label,
            "backend": "scillm-cursor-exec",
            "profile": profile,
            "skills_csv": skills_csv,
            "run_id": run_id,
            "elapsed_s": elapsed,
            "ok": False,
            "error": error_holder["error"],
            "poll": poll_summary,
            "poll_trace_path": str(poll_trace_path),
            "parsed": poll_parsed,
        }
        (artifact_dir / f"{label}_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return receipt

    if "response" not in response_holder:
        events_path = resolve_cursor_events_path(workspace=repo, run_id=run_id)
        if events_path is not None:
            ingested = ingest_cursor_events_file(events_path)
            poll_summary = {**poll_summary, **{k: ingested.get(k) for k in ("cursor_events_path", "terminal_status", "result_event", "tool_call_count")}}
            poll_summary["saw_terminal_result"] = ingested.get("terminal_status") in {"success", "error"}
            poll_terminal = ingested.get("terminal_status")
            poll_saw_result = bool(poll_summary.get("saw_terminal_result"))
            if isinstance(ingested.get("result_event"), dict):
                raw = ingested["result_event"].get("result")
                if isinstance(raw, str):
                    poll_parsed = _parse_jsonish(raw) or poll_parsed
        ok = poll_saw_result and poll_terminal == "success" and isinstance(poll_parsed, dict)
        receipt = {
            "label": label,
            "backend": "scillm-cursor-exec",
            "profile": profile,
            "skills_csv": skills_csv,
            "run_id": run_id,
            "elapsed_s": elapsed,
            "ok": ok,
            "http_response_missing": True,
            "error": error_holder.get("error"),
            "poll": poll_summary,
            "poll_trace_path": str(poll_trace_path),
            "stream_completed": poll_saw_result,
            "parsed": poll_parsed,
            "failure_type": None if ok else "http_response_missing",
        }
        (artifact_dir / f"{label}_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return receipt

    resp: httpx.Response = response_holder["response"]
    payload = resp.json() if resp.content else {}
    response_path = artifact_dir / f"{label}_scillm_response.json"
    response_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    final_events = resolve_cursor_events_path(workspace=repo, run_id=run_id, result=result)
    if final_events is not None:
        final_ingested = ingest_cursor_events_file(final_events)
        poll_summary = {
            **poll_summary,
            "cursor_events_path": final_ingested.get("cursor_events_path"),
            "cursor_event_line_count": final_ingested.get("event_line_count", 0),
            "tool_call_count": final_ingested.get("tool_call_count", 0),
            "terminal_status": final_ingested.get("terminal_status"),
            "result_event": final_ingested.get("result_event"),
            "saw_terminal_result": final_ingested.get("terminal_status") in {"success", "error"},
        }
    nested = result.get("result")
    parsed = nested if isinstance(nested, dict) else None
    if parsed is None:
        parsed = _parse_jsonish(str(result.get("text") or ""))
    if parsed is None and isinstance(result.get("response_path"), str):
        try:
            response_doc = json.loads(Path(result["response_path"]).read_text(encoding="utf-8"))
            parsed = _parse_jsonish(str(response_doc.get("text") or ""))
        except Exception:
            parsed = None

    cursor_extracted = result.get("cursor_extracted") if isinstance(result.get("cursor_extracted"), dict) else {}
    stream_completed = bool(result.get("stream_completed"))
    poll_summary = dict(poll_summary)
    ok, poll_summary = _reconcile_exec_ok(
        payload=payload,
        result=result,
        parsed=parsed,
        poll_summary=poll_summary,
        workspace=repo,
        run_id=run_id,
    )
    events_path = resolve_cursor_events_path(workspace=repo, run_id=run_id, result=result)
    if events_path is not None:
        poll_summary["cursor_events_path"] = str(events_path)

    receipt = {
        "label": label,
        "backend": "scillm-cursor-exec",
        "profile": profile,
        "skills_csv": skills_csv,
        "run_id": run_id,
        "http_status": resp.status_code,
        "scillm_status": payload.get("status"),
        "failure_type": result.get("failure_type"),
        "stream_completed": stream_completed or bool(result.get("recovered_from_stream")),
        "recovered_from_stream": result.get("recovered_from_stream"),
        "events_path": result.get("events_path") or result.get("cursor_events_path"),
        "cursor_events_path": str(events_path) if events_path else poll_summary.get("cursor_events_path"),
        "receipt_path": result.get("receipt_path"),
        "response_path": result.get("response_path"),
        "scillm_response_path": str(response_path),
        "scillm_request_path": str(request_path),
        "poll_trace_path": str(poll_trace_path),
        "poll": poll_summary,
        "elapsed_s": float(result.get("elapsed_s") or elapsed),
        "tool_call_count": cursor_extracted.get("tool_call_count") if cursor_extracted else poll_summary.get("tool_call_count"),
        "parsed": parsed,
        "ok": ok,
        "recovered_from_cursor_events": poll_summary.get("recovered_from_cursor_events"),
    }
    (artifact_dir / f"{label}_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt
