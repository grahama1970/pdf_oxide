#!/usr/bin/env python3
"""Prepare an isolated git workspace for pdf-lab live patch canaries."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
DEFAULT_INCLUDE_PATHS = [
    "AGENTS.md",
    ".gitignore",
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "Cargo.toml",
    "Cargo.lock",
    "benches",
    "python",
    "src",
    "scripts/pdf_lab",
    "tests",
    "schemas",
    "docs/spec",
    "pdf_oxide_cli",
    "pdf_oxide_mcp",
]
EXCLUDED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "artifacts",
    "node_modules",
    "target",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".so", ".pyd", ".dll", ".dylib"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def should_skip(path: Path) -> bool:
    if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
        return True
    return path.suffix in EXCLUDED_SUFFIXES


def validate_include_paths(include_paths: list[str]) -> list[str]:
    validated: list[str] = []
    errors: list[str] = []
    for include in include_paths:
        include_path = Path(include)
        if include_path.is_absolute():
            errors.append(f"include path must be relative: {include}")
            continue
        if any(part == ".." for part in include_path.parts):
            errors.append(f"include path must not traverse outside source root: {include}")
            continue
        normalized = include_path.as_posix()
        if normalized in {"", "."}:
            errors.append(f"include path must name a file or directory below source root: {include}")
            continue
        validated.append(normalized)
    if errors:
        raise ValueError("; ".join(errors))
    return validated


def copy_selected_paths(source_root: Path, dest_root: Path, include_paths: list[str]) -> tuple[list[str], list[str], list[str]]:
    copied: list[str] = []
    missing: list[str] = []
    skipped: list[str] = []
    for include in include_paths:
        source = source_root / include
        if not source.exists():
            missing.append(include)
            continue
        if source.is_symlink():
            skipped.append(include)
            continue
        if source.is_file():
            target = dest_root / include
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(include)
            continue
        for file_path in sorted(path for path in source.rglob("*") if path.is_file()):
            rel = file_path.relative_to(source_root)
            if file_path.is_symlink() or should_skip(rel):
                skipped.append(str(rel))
                continue
            target = dest_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, target)
            copied.append(str(rel))
    return sorted(copied), sorted(missing), sorted(skipped)


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def prepare_isolated_code_root(
    *,
    source_root: Path,
    dest_root: Path,
    include_paths: list[str],
    force: bool = False,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    dest_root = dest_root.resolve()
    include_paths = validate_include_paths(include_paths)
    if source_root == dest_root:
        raise ValueError("destination must not be the source root")
    if dest_root.exists():
        if not force:
            raise FileExistsError(f"destination already exists: {dest_root}")
        shutil.rmtree(dest_root)
    dest_root.mkdir(parents=True)
    copied, missing, skipped = copy_selected_paths(source_root, dest_root, include_paths)
    if not copied:
        raise ValueError("no files copied into isolated code root")

    init = run_git(["init"], dest_root)
    if init.returncode != 0:
        raise RuntimeError(init.stderr.strip() or "git init failed")
    exclude = dest_root / ".git/info/exclude"
    exclude.write_text(exclude.read_text(encoding="utf-8") + "\n/.pdf_lab_isolated_code_root.json\n", encoding="utf-8")
    run_git(["config", "user.name", "PDF Lab Harness"], dest_root)
    run_git(["config", "user.email", "pdf-lab-harness@example.invalid"], dest_root)
    add = run_git(["add", "."], dest_root)
    if add.returncode != 0:
        raise RuntimeError(add.stderr.strip() or "git add failed")
    commit = run_git(["commit", "-m", "pdf-lab: isolated code-root baseline"], dest_root)
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr.strip() or commit.stdout.strip() or "git commit failed")
    baseline = run_git(["rev-parse", "HEAD"], dest_root)
    if baseline.returncode != 0:
        raise RuntimeError(baseline.stderr.strip() or "git rev-parse failed")
    manifest = {
        "schema": "pdf_lab.second_pass.isolated_code_root.v1",
        "created_at": utc_now(),
        "source_root": str(source_root),
        "dest_root": str(dest_root),
        "include_paths": include_paths,
        "copied_files": copied,
        "copied_count": len(copied),
        "missing_include_paths": missing,
        "skipped_paths": skipped,
        "skipped_count": len(skipped),
        "baseline_commit": baseline.stdout.strip(),
        "git_status_short": "",
        "clean": False,
    }
    write_json(dest_root / ".pdf_lab_isolated_code_root.json", manifest)
    status = run_git(["status", "--short"], dest_root)
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or "git status failed")
    manifest["git_status_short"] = status.stdout
    manifest["clean"] = status.stdout.strip() == ""
    write_json(dest_root / ".pdf_lab_isolated_code_root.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=REPO)
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--include", action="append", dest="include_paths")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    manifest = prepare_isolated_code_root(
        source_root=args.source_root,
        dest_root=args.dest,
        include_paths=args.include_paths or DEFAULT_INCLUDE_PATHS,
        force=args.force,
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
