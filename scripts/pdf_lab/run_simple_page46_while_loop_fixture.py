#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from simple_page46_while_loop import page46_while_loop


RUN_ID = os.environ.get("PDF_LAB_SIMPLE_LOOP_RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
OUT = Path("artifacts/pdf_lab/simple_page46_while_loop_fixture") / RUN_ID
STATE = {"after_patch": False}


def write(name: str, payload: dict) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{len(list(OUT.glob('*.json'))):02d}_{name}.json").write_text(json.dumps(payload, indent=2) + "\n")
    return {"code_root": str(OUT / "code_root")}


def extract(**kw) -> dict:
    return {"page": 46, "attempt": kw["attempt"], "after_patch": STATE["after_patch"]}


def review(**kw) -> dict:
    return {"status": "pass" if kw["evidence"]["after_patch"] else "defect", "attempt": kw["attempt"]}


def coder_subagent(**kw) -> dict:
    STATE["after_patch"] = True
    return {"ok": True, "attempt": kw["attempt"], "changed_files": ["scripts/pdf_lab/snapshot_current_extraction.py"]}


def validate(**kw) -> dict:
    return {"ok": True, "attempt": kw["attempt"]}


if __name__ == "__main__":
    result = page46_while_loop(
        extract=extract,
        reviewer_subagent=review,
        coder_subagent=coder_subagent,
        validate=validate,
        save=write,
    )
    write("terminal", result)
    print(json.dumps({"out_dir": str(OUT), **result}, sort_keys=True))
