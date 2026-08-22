#!/usr/bin/env python3
"""Issue #33 guard: debug and release extract_text branch parity."""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PDF_CANDIDATES = [
    Path("/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/NIST_SP_800-53r5.pdf"),
    Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf"),
]


def find_pdf() -> Path:
    for path in PDF_CANDIDATES:
        if path.exists():
            return path
    print(
        "NIST_SP_800-53r5.pdf not found at expected corpus paths: "
        + ", ".join(str(path) for path in PDF_CANDIDATES),
        file=sys.stderr,
    )
    raise SystemExit(2)


def run_extract(pdf: Path, trace_path: Path, *, release: bool) -> None:
    env = os.environ.copy()
    env["PDF_OXIDE_BRANCH_TRACE"] = str(trace_path)

    command = ["cargo", "run", "--quiet"]
    if release:
        command.append("--release")
    command.extend(["--example", "extract_text_simple", "--", str(pdf)])

    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=420,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr[-6000:], file=sys.stderr)
        raise SystemExit(result.returncode)


def read_trace(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        print(f"empty trace {path}", file=sys.stderr)
        raise SystemExit(1)
    return rows


def without_profile(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{key: value for key, value in row.items() if key != "profile"} for row in rows]


def main() -> int:
    pdf = find_pdf()
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(os.environ.get("PDF_OXIDE_BRANCH_TRACE_DIR", f"/tmp/pdf_oxide_issue33_branch_trace_{stamp}"))
    out_dir.mkdir(parents=True, exist_ok=True)

    debug_trace = out_dir / "debug.jsonl"
    release_trace = out_dir / "release.jsonl"

    run_extract(pdf, debug_trace, release=False)
    run_extract(pdf, release_trace, release=True)

    debug_rows = read_trace(debug_trace)
    release_rows = read_trace(release_trace)

    if debug_rows[0].get("profile") != "debug":
        print(f"first debug profile was {debug_rows[0].get('profile')!r}", file=sys.stderr)
        return 1
    if release_rows[0].get("profile") != "release":
        print(f"first release profile was {release_rows[0].get('profile')!r}", file=sys.stderr)
        return 1

    debug_decisions = without_profile(debug_rows)
    release_decisions = without_profile(release_rows)
    if debug_decisions != release_decisions:
        for index, (debug_row, release_row) in enumerate(zip(debug_decisions, release_decisions)):
            if debug_row != release_row:
                print(
                    f"branch trace differs at line {index}: debug={debug_row} release={release_row}",
                    file=sys.stderr,
                )
                return 1
        print(
            f"branch trace length differs: debug={len(debug_rows)} release={len(release_rows)}",
            file=sys.stderr,
        )
        return 1

    branches: dict[str, int] = {}
    for row in debug_rows:
        branch = str(row["branch"])
        branches[branch] = branches.get(branch, 0) + 1

    print(
        "BRANCH_PARITY_OK "
        f"pages={len(debug_rows)} "
        f"branches={json.dumps(branches, sort_keys=True)} "
        f"debug_trace={debug_trace} "
        f"release_trace={release_trace}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
