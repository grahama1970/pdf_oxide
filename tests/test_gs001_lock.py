"""GS001 contract/goal lock tool — fail-closed behavior.

The lock tool must refuse to hash a contract with pending rows or a goal
with unresolved pins, and must produce a stable hash once complete.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import gs001_lock  # noqa: E402

DRAFT_CONTRACT = (
    REPO_ROOT / "golden_slices" / "gs001_nist_page28" / "expected_elements_v3.draft.json"
)


def _complete_contract():
    return {
        "schema_version": "pdf_lab.golden_slice_expected.v3",
        "slice_id": "GS001",
        "contract_status": "locked",
        "contract_version": 3,
        "source_pdf": {"sha256": "sha256:" + "a" * 64, "page_index": 27},
        "expected_elements": [
            {
                "id": "gs001:row:1",
                "page": 27,
                "type": "chapter_label",
                "text": "CHAPTER ONE",
                "bbox": [0.1, 0.05, 0.5, 0.08],
            }
        ],
        "waivers": [
            {
                "waiver_id": "w1",
                "type": "header_footer_noise",
                "decision": "accepted_page_chrome_noise",
                "signed_by": "graham@grahama.co",
            }
        ],
    }


def test_committed_draft_contract_is_not_lockable():
    contract = json.loads(DRAFT_CONTRACT.read_text(encoding="utf-8"))
    blockers = gs001_lock.contract_blockers(contract)
    assert blockers, "draft with pending rows must not be lockable"
    assert any("pending_recovery" in blocker for blocker in blockers)
    assert any("source_pdf.sha256" in blocker for blocker in blockers)


def test_complete_contract_locks_with_stable_hash(tmp_path):
    contract = _complete_contract()
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    receipt_path = tmp_path / "receipt.json"
    assert gs001_lock.lock_contract(path, receipt_path) == 0
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["schema_version"] == "pdf_lab.expected_contract_lock.v1"
    first_hash = receipt["contract_sha256"]
    assert first_hash.startswith("sha256:")

    # Key order must not change the hash (canonical JSON).
    reordered = json.loads(json.dumps(contract))
    reordered["waivers"], reordered["expected_elements"] = (
        reordered["waivers"],
        reordered["expected_elements"],
    )
    path2 = tmp_path / "contract2.json"
    path2.write_text(json.dumps(reordered, indent=4), encoding="utf-8")
    receipt2_path = tmp_path / "receipt2.json"
    assert gs001_lock.lock_contract(path2, receipt2_path) == 0
    receipt2 = json.loads(receipt2_path.read_text(encoding="utf-8"))
    assert receipt2["contract_sha256"] == first_hash


def test_unsigned_waiver_blocks_lock(tmp_path):
    contract = _complete_contract()
    contract["waivers"][0].pop("signed_by")
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    assert gs001_lock.lock_contract(path, None) == 1


def test_committed_goal_is_not_lockable_yet():
    goal_path = REPO_ROOT / "GOAL.md"
    pins = gs001_lock.goal_pins(goal_path.read_text(encoding="utf-8"))
    assert pins["goal_id"] == "PDF-EXTRACTION-GS001-TAU-V1"
    assert any("PENDING" in value for value in pins.values())
    assert gs001_lock.lock_goal(goal_path, None) == 1


def test_goal_locks_when_all_pins_resolved(tmp_path):
    goal_text = (REPO_ROOT / "GOAL.md").read_text(encoding="utf-8")
    resolved = (
        goal_text.replace("PENDING_BASELINE_AUDIT", "sha256:" + "b" * 64)
        .replace("PENDING_SOURCE_PDF", "sha256:" + "c" * 64)
        .replace("PENDING_ROW_RECOVERY", "sha256:" + "d" * 64)
        .replace(
            "PENDING_PACKET_RECOVERY (stratified NIST pages 20 468 401 415 483 34 31 32 33 23)",
            "sha256:" + "e" * 64,
        )
    )
    goal_path = tmp_path / "GOAL.md"
    goal_path.write_text(resolved, encoding="utf-8")
    receipt_path = tmp_path / "goal_lock.json"
    assert gs001_lock.lock_goal(goal_path, receipt_path) == 0
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["goal_id"] == "PDF-EXTRACTION-GS001-TAU-V1"
    assert receipt["goal_hash"].startswith("sha256:")
