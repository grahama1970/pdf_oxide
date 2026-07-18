#!/usr/bin/env python3
"""Page-scoped second-pass repair loop runner (committed rebuild).

Rebuilt from agent-skills issues #70-#77: the original June runner lived
only at the author's workstation with hard-coded /mnt and $HOME paths.
This version takes every environmental fact from a runtime manifest so a
clean checkout can replay it, and it uses the committed deterministic
pieces: pdf-lab's final agent pass (with fail-closed scillm preflight),
the fingerprinted second-pass backlog, and the #77 terminal-ledger
contract.

Manifest (pdf_lab.runtime_manifest.v1) example::

    {
      "schema_version": "pdf_lab.runtime_manifest.v1",
      "pdf_lab_dir": "/path/to/agent-skills/skills/pdf-lab",
      "extraction_json": "/path/to/full-extraction.json",
      "output_root": "/path/to/artifacts/run-001",
      "second_pass_model": null,
      "delegate_artifact_dir": null
    }

With ``second_pass_model: null`` the run is fully offline-deterministic.
With ``delegate_artifact_dir`` set, saved live delegate artifacts
(patch_attempt_*_result.json) are replayed through the current
terminal-ledger writer — the deterministic closure proof issue #77
requires.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from terminal_ledger import replay_delegate_artifact, write_terminal_ledger

MANIFEST_SCHEMA = "pdf_lab.runtime_manifest.v1"


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise SystemExit(
            f"unsupported manifest schema: {manifest.get('schema_version')!r}"
        )
    for field in ("pdf_lab_dir", "extraction_json", "output_root"):
        if not manifest.get(field):
            raise SystemExit(f"manifest missing required field: {field}")
    return manifest


def run_page(manifest: dict, page: int) -> dict:
    pdf_lab_dir = Path(manifest["pdf_lab_dir"]).expanduser()
    extraction_json = Path(manifest["extraction_json"]).expanduser()
    output_root = Path(manifest["output_root"]).expanduser()
    page_dir = output_root / f"page_{page:04d}"
    page_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(pdf_lab_dir))
    from lib.agentic import run_final_agent_pass  # noqa: E402 (manifest-scoped import)

    result = run_final_agent_pass(
        extraction_json,
        output_dir=page_dir,
        second_pass_model=manifest.get("second_pass_model"),
    )

    delegate_dir = manifest.get("delegate_artifact_dir")
    if delegate_dir:
        artifacts = sorted(
            Path(delegate_dir).expanduser().glob(f"*p{page:04d}*result*.json")
        ) or sorted(Path(delegate_dir).expanduser().glob("patch_attempt_*result*.json"))
        if artifacts:
            ledger_path = replay_delegate_artifact(artifacts[0], page_dir, page=page)
        else:
            ledger_path = write_terminal_ledger(
                page_dir,
                page=page,
                delegate_result=None,
                context={"note": "no delegate artifact found for this page"},
            )
    else:
        ledger_path = write_terminal_ledger(
            page_dir,
            page=page,
            delegate_result=None,
            context={"note": "no repair delegate configured (review-only run)"},
        )

    return {
        "page": page,
        "triage_queue": str(result.triage_queue_path),
        "task_count": result.task_count,
        "terminal_ledger": str(ledger_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--pages", type=int, nargs="+", required=True)
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    summary = [run_page(manifest, page) for page in args.pages]
    output_root = Path(manifest["output_root"]).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "run_summary.json").write_text(
        json.dumps({"schema_version": "pdf_lab.page_run_summary.v1", "pages": summary}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
