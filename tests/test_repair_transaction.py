"""Transactional repair: worktree isolation, allowlist, REAL rollback.

The old loop recorded "reverted" as a status string without any git
operation. These tests pin the actual transaction semantics.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "pdf_lab"))

from repair_transaction import (  # noqa: E402
    paths_outside_allowlist,
    patched_paths,
    run_repair_transaction,
)


def _make_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    for key, value in (
        ("user.email", "noreply@anthropic.com"),
        ("user.name", "Claude"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(["git", "-C", str(path), "config", key, value], check=True)
    (path / "extractor.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "forbidden.py").write_text("SECRET = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-qm", "init", "--no-verify"], check=True
    )
    return path


GOOD_PATCH = """--- a/extractor.py
+++ b/extractor.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""

FORBIDDEN_PATCH = """--- a/forbidden.py
+++ b/forbidden.py
@@ -1 +1 @@
-SECRET = 1
+SECRET = 2
"""


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _branches(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", "-a"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_patch_path_extraction_and_allowlist():
    assert patched_paths(GOOD_PATCH) == ["extractor.py"]
    assert paths_outside_allowlist(["extractor.py"], ["extractor.py"]) == []
    assert paths_outside_allowlist(["forbidden.py"], ["extractor.py", "presets/*"]) == [
        "forbidden.py"
    ]


def test_verified_repair_lands_on_attempt_branch_not_main(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    before = _head(repo)
    receipt = run_repair_transaction(
        repo,
        patch_text=GOOD_PATCH,
        allowed_paths=["extractor.py"],
        verify_command=[sys.executable, "-c", "import extractor; assert extractor.VALUE == 2"],
        output_dir=tmp_path / "out",
        defect_key="sha256:" + "a" * 64,
    )
    assert receipt["status"] == "verified_awaiting_promotion"
    assert receipt["commit_sha"] and receipt["commit_sha"] != before
    assert _head(repo) == before, "main branch preimage untouched"
    assert receipt["attempt_branch"] in _branches(repo), "verified commit preserved on attempt branch"
    assert receipt["promotion"].startswith("NOT_PERFORMED")
    written = json.loads((tmp_path / "out" / "repair_receipt.json").read_text())
    assert written["schema_version"] == "pdf_lab.repair_transaction_receipt.v1"


def test_failed_verification_performs_real_rollback(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    before = _head(repo)
    receipt = run_repair_transaction(
        repo,
        patch_text=GOOD_PATCH,
        allowed_paths=["extractor.py"],
        verify_command=[sys.executable, "-c", "raise SystemExit(1)"],
        output_dir=tmp_path / "out",
    )
    assert receipt["status"] == "rolled_back_verification_failed"
    assert receipt["rollback"]["performed"] is True
    assert "worktree_removed" in receipt["rollback"]["actions"]
    assert _head(repo) == before
    assert receipt["attempt_branch"] not in _branches(repo), "attempt branch deleted"
    assert not any(p.name.startswith("worktree-") for p in (tmp_path / "out").iterdir() if p.is_dir())


def test_patch_outside_allowlist_is_rejected_before_any_git_work(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    receipt = run_repair_transaction(
        repo,
        patch_text=FORBIDDEN_PATCH,
        allowed_paths=["extractor.py", "python/pdf_oxide/presets/*"],
        verify_command=[sys.executable, "-c", "pass"],
        output_dir=tmp_path / "out",
    )
    assert receipt["status"] == "rejected_path_violation"
    assert receipt["path_violations"] == ["forbidden.py"]
    assert receipt["verify"] is None, "no verification spend on rejected patches"
    assert receipt["attempt_branch"] not in _branches(repo)


def test_malformed_patch_rolls_back(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    receipt = run_repair_transaction(
        repo,
        patch_text="--- a/extractor.py\n+++ b/extractor.py\n@@ garbage @@\n",
        allowed_paths=["extractor.py"],
        verify_command=[sys.executable, "-c", "pass"],
        output_dir=tmp_path / "out",
    )
    assert receipt["status"] == "rolled_back_patch_apply_failed"
    assert receipt["rollback"]["performed"] is True


def test_empty_patch_is_rejected(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    receipt = run_repair_transaction(
        repo,
        patch_text="no diff here",
        allowed_paths=["extractor.py"],
        verify_command=[sys.executable, "-c", "pass"],
        output_dir=tmp_path / "out",
    )
    assert receipt["status"] == "rejected_empty_patch"
