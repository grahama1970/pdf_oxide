"""Issue #77 terminal-ledger contract (agent-skills#77, clarified spec).

A PATCH_DELEGATE_BLOCKED delegate response with a concrete reason must be
preserved as a typed still-open outcome — never collapsed into a generic
substrate failure. The invariant is proven by deterministic replay of the
artifact shape, not by a live model re-emitting the branch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "pdf_lab"))

from terminal_ledger import (  # noqa: E402
    classify_delegate_result,
    replay_delegate_artifact,
    write_terminal_ledger,
)


def test_unsupported_defect_blocked_is_typed_still_open_not_substrate():
    delegate_result = {
        "assistant_text": (
            "I inspected the extraction for page 193.\n"
            "PATCH_DELEGATE_BLOCKED reason=unsupported_defect"
        ),
        "diff": "--- a/x\n+++ b/x\n",
    }
    ledger = classify_delegate_result(delegate_result)
    # Exact acceptance contract from issue #77:
    assert ledger["terminal_status"] == "still_open"
    assert ledger["reason"] == "patch_delegate_unsupported_defect"
    assert ledger["patch_delegate_blocked_reason"] == "unsupported_defect"
    assert ledger["patch_delegate_blocked_claim"] == {"reason": "unsupported_defect"}
    assert ledger["terminal_status"] != "blocked_substrate"


def test_transport_error_is_substrate_failure():
    ledger = classify_delegate_result(
        {"assistant_text": "", "transport_error": "connection refused"}
    )
    assert ledger["terminal_status"] == "blocked_substrate"
    assert ledger["reason"] == "patch_delegate_substrate_error"


def test_empty_response_is_substrate_failure():
    ledger = classify_delegate_result({"assistant_text": "   "})
    assert ledger["terminal_status"] == "blocked_substrate"
    assert ledger["reason"] == "patch_delegate_empty_response"


def test_blocked_takes_precedence_over_transport_error_field():
    ledger = classify_delegate_result(
        {
            "assistant_text": "PATCH_DELEGATE_BLOCKED reason=unsupported_defect",
            "error": "later cleanup error",
        }
    )
    assert ledger["terminal_status"] == "still_open"
    assert ledger["patch_delegate_blocked_reason"] == "unsupported_defect"


def test_diff_without_blocker_is_patch_proposed():
    ledger = classify_delegate_result(
        {"assistant_text": "patch follows", "diff": "--- a\n+++ b\n"}
    )
    assert ledger["terminal_status"] == "patch_proposed"


def test_deterministic_replay_of_saved_live_artifact(tmp_path):
    artifact = tmp_path / "patch_attempt_01_opencode_host_result.json"
    artifact.write_text(
        json.dumps(
            {
                "assistant_text": "PATCH_DELEGATE_BLOCKED reason=unsupported_defect",
                "diff": "non-empty evidence",
            }
        ),
        encoding="utf-8",
    )
    ledger_path = replay_delegate_artifact(artifact, tmp_path / "out", page=193)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["schema_version"] == "pdf_lab.page_terminal_ledger.v1"
    assert ledger["page"] == 193
    assert ledger["terminal_status"] == "still_open"
    assert ledger["reason"] == "patch_delegate_unsupported_defect"
    assert ledger["context"]["replayed_from"] == str(artifact)
    assert ledger["semantic_truth"] == "NOT_CLAIMED"


def test_review_only_run_is_not_a_substrate_failure(tmp_path):
    path = write_terminal_ledger(
        tmp_path, page=28, delegate_result=None, context={"note": "review-only"}
    )
    ledger = json.loads(path.read_text(encoding="utf-8"))
    assert ledger["terminal_status"] == "no_repair_attempted"
    assert ledger["reason"] == "no_repair_delegate_configured"
