"""Transactional repair: isolated worktree, allowlist, real rollback.

This is the fixer leg of the extraction-repair loop. The old
run_rounds.py recorded status "reverted" without performing any git
operation; this module makes the transaction real:

1. Create an isolated git worktree at the repository's current HEAD
   (the preimage) on a dedicated attempt branch.
2. Apply a unified-diff patch inside the worktree. Every touched path
   must match the allowlist — a patch that strays outside is rejected
   before any verification spend (fail closed).
3. Run the deterministic verification command inside the worktree
   (e.g. `pdf-lab regression-check` or a focused pytest). Model prose
   is never consulted.
4. On verification PASS: commit inside the worktree and report the
   attempt branch + commit sha in the receipt. Promotion into the main
   branch is deliberately NOT performed here — that is tau's apply
   gate's decision.
5. On any failure: remove the worktree and delete the attempt branch —
   an actual rollback, recorded as such.

Receipt schema: ``pdf_lab.repair_transaction_receipt.v1``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "pdf_lab.repair_transaction_receipt.v1"

_DIFF_TARGET = re.compile(r"^\+\+\+ (?:b/)?(?P<path>\S+)", re.MULTILINE)


class RepairTransactionError(RuntimeError):
    pass


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise RepairTransactionError(
            f"git {' '.join(args)} failed: {result.stderr.strip()[:500]}"
        )
    return result


def patched_paths(patch_text: str) -> list[str]:
    paths = []
    for match in _DIFF_TARGET.finditer(patch_text):
        path = match.group("path")
        if path != "/dev/null":
            paths.append(path)
    return paths


def paths_outside_allowlist(paths: list[str], allowed: list[str]) -> list[str]:
    violations = []
    for path in paths:
        if not any(fnmatch.fnmatch(path, pattern) for pattern in allowed):
            violations.append(path)
    return violations


def run_repair_transaction(
    repo_root: Path,
    *,
    patch_text: str,
    allowed_paths: list[str],
    verify_command: list[str],
    output_dir: Path,
    defect_key: str = "",
    commit_message: str = "pdf-lab bounded repair attempt",
    verify_timeout_s: float = 600.0,
) -> dict[str, Any]:
    repo_root = Path(repo_root).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    preimage_sha = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
    attempt_id = uuid.uuid4().hex[:12]
    branch = f"pdf-lab-repair/{attempt_id}"
    worktree = output_dir / f"worktree-{attempt_id}"
    patch_path = output_dir / f"attempt-{attempt_id}.patch"
    patch_path.write_text(patch_text, encoding="utf-8")

    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "attempt_id": attempt_id,
        "defect_key": defect_key,
        "repo_root": str(repo_root),
        "preimage_sha": preimage_sha,
        "attempt_branch": branch,
        "patch_sha256": "sha256:" + hashlib.sha256(patch_text.encode("utf-8")).hexdigest(),
        "patch_path": str(patch_path),
        "allowed_paths": allowed_paths,
        "patched_paths": patched_paths(patch_text),
        "verify_command": verify_command,
        "status": None,
        "rollback": None,
        "verify": None,
        "commit_sha": None,
        "promotion": "NOT_PERFORMED (tau apply gate decides)",
        "semantic_truth": "NOT_CLAIMED",
    }

    violations = paths_outside_allowlist(receipt["patched_paths"], allowed_paths)
    if not receipt["patched_paths"]:
        receipt["status"] = "rejected_empty_patch"
        _write_receipt(output_dir, attempt_id, receipt)
        return receipt
    if violations:
        receipt["status"] = "rejected_path_violation"
        receipt["path_violations"] = violations
        _write_receipt(output_dir, attempt_id, receipt)
        return receipt

    _git(repo_root, "worktree", "add", "-b", branch, str(worktree), preimage_sha)
    try:
        apply_result = _git(worktree, "apply", "--index", str(patch_path), check=False)
        if apply_result.returncode != 0:
            receipt["status"] = "rolled_back_patch_apply_failed"
            receipt["apply_error"] = apply_result.stderr.strip()[:1000]
            receipt["rollback"] = _rollback(repo_root, worktree, branch)
            _write_receipt(output_dir, attempt_id, receipt)
            return receipt

        try:
            verify = subprocess.run(
                verify_command,
                cwd=str(worktree),
                capture_output=True,
                text=True,
                timeout=verify_timeout_s,
            )
            receipt["verify"] = {
                "exit_code": verify.returncode,
                "stdout_tail": verify.stdout[-2000:],
                "stderr_tail": verify.stderr[-2000:],
            }
            verified = verify.returncode == 0
        except subprocess.TimeoutExpired:
            receipt["verify"] = {"exit_code": None, "error": "verification timed out"}
            verified = False

        if not verified:
            receipt["status"] = "rolled_back_verification_failed"
            receipt["rollback"] = _rollback(repo_root, worktree, branch)
            _write_receipt(output_dir, attempt_id, receipt)
            return receipt

        _git(worktree, "commit", "-m", commit_message, "--no-verify")
        receipt["commit_sha"] = _git(worktree, "rev-parse", "HEAD").stdout.strip()
        receipt["status"] = "verified_awaiting_promotion"
        # The worktree is removed but the attempt branch (with the verified
        # commit) is kept for the apply gate to promote or discard.
        _git(repo_root, "worktree", "remove", "--force", str(worktree))
        receipt["rollback"] = None
        _write_receipt(output_dir, attempt_id, receipt)
        return receipt
    except Exception as exc:
        receipt["status"] = "rolled_back_internal_error"
        receipt["internal_error"] = str(exc)[:1000]
        receipt["rollback"] = _rollback(repo_root, worktree, branch)
        _write_receipt(output_dir, attempt_id, receipt)
        return receipt


def _rollback(repo_root: Path, worktree: Path, branch: str) -> dict[str, Any]:
    actions = []
    if worktree.exists():
        _git(repo_root, "worktree", "remove", "--force", str(worktree), check=False)
        actions.append("worktree_removed")
    _git(repo_root, "branch", "-D", branch, check=False)
    actions.append("attempt_branch_deleted")
    return {
        "performed": True,
        "actions": actions,
        "note": "preimage untouched; this is an actual rollback, not a status label",
    }


def _write_receipt(output_dir: Path, attempt_id: str, receipt: dict[str, Any]) -> None:
    (output_dir / f"repair_receipt-{attempt_id}.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "repair_receipt.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8"
    )
