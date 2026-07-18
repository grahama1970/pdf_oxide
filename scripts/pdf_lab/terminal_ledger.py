"""Terminal-ledger writer for the PDF Lab page repair loop.

Rebuilt from the agent-skills issue #77 clarified acceptance contract
(the original June implementation ran only on the author's machine and
was never committed). The invariant is deterministic and must not depend
on a live model choosing a branch:

Given a coder-delegate result whose assistant_text contains
``PATCH_DELEGATE_BLOCKED reason=<reason>``, the terminal ledger must
preserve that typed blocker::

    {
      "terminal_status": "still_open",
      "reason": "patch_delegate_<reason>",
      "patch_delegate_blocked_reason": "<reason>",
      "patch_delegate_blocked_claim": {"reason": "<reason>"}
    }

It must NOT be collapsed into a generic substrate failure. Unsupported
work is a routing outcome; substrate failure means the harness itself
could not operate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

LEDGER_SCHEMA = "pdf_lab.page_terminal_ledger.v1"

_BLOCKED_PATTERN = re.compile(
    r"PATCH_DELEGATE_BLOCKED\s+reason=(?P<reason>[a-zA-Z0-9_\-]+)"
)


def classify_delegate_result(delegate_result: dict[str, Any]) -> dict[str, Any]:
    """Deterministically classify a coder-delegate result artifact.

    Input is the saved delegate artifact (e.g.
    patch_attempt_01_opencode_host_result.json): at minimum
    ``assistant_text``; optionally ``diff`` / ``diff_evidence`` and
    transport error fields.
    """
    assistant_text = str(delegate_result.get("assistant_text") or "")
    transport_error = delegate_result.get("transport_error") or delegate_result.get(
        "error"
    )

    blocked = _BLOCKED_PATTERN.search(assistant_text)
    if blocked:
        reason = blocked.group("reason")
        return {
            "terminal_status": "still_open",
            "reason": f"patch_delegate_{reason}",
            "patch_delegate_blocked_reason": reason,
            "patch_delegate_blocked_claim": {"reason": reason},
            "diff_evidence_present": bool(
                delegate_result.get("diff") or delegate_result.get("diff_evidence")
            ),
        }

    if transport_error:
        return {
            "terminal_status": "blocked_substrate",
            "reason": "patch_delegate_substrate_error",
            "substrate_error": str(transport_error)[:500],
        }

    if not assistant_text.strip():
        return {
            "terminal_status": "blocked_substrate",
            "reason": "patch_delegate_empty_response",
        }

    diff = delegate_result.get("diff") or delegate_result.get("diff_evidence")
    if diff:
        return {
            "terminal_status": "patch_proposed",
            "reason": "patch_delegate_returned_diff",
            "diff_evidence_present": True,
        }

    return {
        "terminal_status": "still_open",
        "reason": "patch_delegate_no_actionable_output",
        "diff_evidence_present": False,
    }


def write_terminal_ledger(
    output_dir: Path,
    *,
    page: int,
    delegate_result: dict[str, Any] | None,
    attempt: int = 1,
    context: dict[str, Any] | None = None,
) -> Path:
    if delegate_result is None:
        # Review-only run: no repair was attempted, which is neither a
        # blocker nor a substrate failure.
        classification: dict[str, Any] = {
            "terminal_status": "no_repair_attempted",
            "reason": "no_repair_delegate_configured",
        }
    else:
        classification = classify_delegate_result(delegate_result)
    ledger = {
        "schema_version": LEDGER_SCHEMA,
        "page": int(page),
        "attempt": int(attempt),
        **classification,
        "context": context or {},
        "semantic_truth": "NOT_CLAIMED",
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "terminal_ledger.json"
    path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def replay_delegate_artifact(artifact_path: Path, output_dir: Path, *, page: int) -> Path:
    """Deterministic replay of a saved live delegate artifact through the
    current ledger writer — the closure proof issue #77 requires."""
    delegate_result = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    return write_terminal_ledger(
        Path(output_dir),
        page=page,
        delegate_result=delegate_result,
        context={"replayed_from": str(artifact_path)},
    )
