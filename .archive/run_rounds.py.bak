#!/usr/bin/env python3
"""Deterministic self-improvement loop for pdf_oxide extraction.

Works on ANY PDF — paths derived from the PDF's stem so state doesn't collide.

===============================================================================
HOW DEFECTS ARE DETECTED (READ THIS)
===============================================================================

Analysis method: TEXT + BBOX + ROTATION comparison. PURELY DETERMINISTIC.

    DOES use:
      - PyMuPDF (fitz) to walk every page of the SOURCE PDF
      - page.get_text("dict") for span-level rotation + bbox
      - page.get_text("blocks") for block-level visible text
      - The EXTRACTOR's JSON output (list of blocks with bbox + text + type)

    DOES NOT use:
      - /pdf-lab UI (no browser, no CDP)
      - /pdf-screenshot (no page rendering)
      - Any VLM, Gemini, or image-based comparison
      - LLM inference of any kind in the diagnosis step

For each page the scanner produces 5 defect categories by comparing the
extractor's output against PyMuPDF ground truth:

    TEXT_LOST        Visible text in `page.get_text("blocks")` (≥20 chars, not
                     rotated, not doc-wide chrome) whose first 40 chars are not
                     present in ANY extracted block on that page.
    CHROME_LEAK      Extracted content-typed block whose text matches a
                     doc-wide recurring top/bottom string (position+frequency
                     detection, same heuristic the extractor uses).
    ROTATED_LEAK     Extracted content-typed block whose text matches a line
                     whose PyMuPDF `line.dir` has |dy|>0.1 (rotated).
    DUPLICATE_BLOCK  Same (blockType, text_prefix_60) emitted twice on one
                     page.
    EMPTY_BLOCK      Extracted block with <2 chars of non-whitespace text.

The LLM (via /code-runner in STEP 3) reads `.defects.json` and tries to reduce
the largest category. The LLM never judges — it proposes a code patch, and
this script's regression gate (STEP 4) is the deterministic referee.

===============================================================================
THE 5 STEPS PER ROUND
===============================================================================

    STEP 1 — EXTRACT          pdf_oxide.extract_for_pdflab.extract_pdf (in-proc)
                              Output: <pdf>.extraction.json (blocks with bbox)
    STEP 2 — DIAGNOSE         scan_defects() classifies all defects (in-proc)
                              Output: <pdf>.defects.json (per-category + per-page)
    STEP 3 — FIX              /code-runner worktree-isolated LLM loop (subprocess)
                              LLM edits python/pdf_oxide/extract_for_pdflab.py
                              DoD: STEP 1+2+4 must pass inside worktree
    STEP 4 — REGRESSION GATE  post-fix vs prior round: total decreased AND no
                              category increased
    STEP 5 — TALLY            append round entry to rounds.json with status
                              (committed | reverted | code-runner-failed)

HALT when: total_defects == 0, OR stall_limit rounds without improvement,
           OR max_rounds reached.

Usage:
    python3 run_rounds.py --pdf /path/to/file.pdf
    python3 run_rounds.py --pdf file.pdf --max-rounds 30 --stall-limit 2
    python3 run_rounds.py --pdf file.pdf --workdir /tmp/pdf_oxide_loop
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

# ---------- paths ----------

REPO = Path(__file__).resolve().parents[2]  # pdf_oxide repo root
CODE_RUNNER = Path("/home/graham/.claude/skills/code-runner/run.sh")

# Make the pdf_oxide package importable.
sys.path.insert(0, str(REPO / "python"))
import fitz  # noqa: E402  (PyMuPDF)
from pdf_oxide.extract_for_pdflab import extract_pdf  # noqa: E402


@dataclass(frozen=True)
class LoopPaths:
    """All file paths for one PDF's improvement loop. Derived from PDF stem."""
    pdf: Path
    workdir: Path

    @property
    def key(self) -> str:
        # Stem + short hash handles PDFs with same name in different locations.
        h = hashlib.sha1(str(self.pdf).encode()).hexdigest()[:8]
        return f"{self.pdf.stem}_{h}"

    @property
    def extraction(self) -> Path:
        return self.workdir / f"{self.key}.extraction.json"

    @property
    def defects(self) -> Path:
        return self.workdir / f"{self.key}.defects.json"

    @property
    def rounds(self) -> Path:
        return self.workdir / f"{self.key}.rounds.json"

    @property
    def log_dir(self) -> Path:
        return self.workdir / f"{self.key}.logs"

    def ensure(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


# ---------- logging ----------

def utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def banner(msg: str, log: TextIO) -> None:
    s = f"{'=' * 60}\n{msg}\n{'=' * 60}"
    print(s); log.write(s + "\n")


def step_header(n: int, name: str, log: TextIO) -> None:
    s = f"\n── STEP {n} — {name} " + "─" * max(0, 60 - len(name) - 10)
    print(s); log.write(s + "\n")


def line(msg: str, log: TextIO) -> None:
    print(msg); log.write(msg + "\n")


# ---------- defect scanner (in-process) ----------

def _norm(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip())[:120]


def scan_defects(pdf_path: Path, extraction: dict) -> dict:
    """Classify defects by comparing extraction to PyMuPDF ground truth.

    Mirrors extract_for_pdflab's chrome/rotation detection so the metric is
    symmetric (no penalty for correct chrome filtering).
    """
    blocks_by_page = defaultdict(list)
    for b in extraction.get("blocks", []):
        blocks_by_page[b.get("page")].append(b)

    doc = fitz.open(pdf_path)
    top_bottom_counter: Counter = Counter()
    rotated_texts: set[str] = set()

    # Pass 1: chrome (position+frequency) and rotated text across all pages.
    for page_num in range(doc.page_count):
        page = doc[page_num]
        h = page.rect.height
        for blk in page.get_text("dict").get("blocks", []):
            if blk.get("type") != 0:
                continue
            for ln in blk.get("lines", []):
                spans = ln.get("spans", [])
                text = " ".join(s.get("text", "").strip() for s in spans).strip()
                if not text:
                    continue
                _, dy = ln.get("dir", (1.0, 0.0))
                if abs(dy) > 0.1:
                    rotated_texts.add(text[:40].lower())
                    continue
                _, y0, _, y1 = ln.get("bbox", (0.0, 0.0, 0.0, 0.0))
                if y1 < h * 0.08 or y0 > h * 0.92:
                    top_bottom_counter[_norm(text)] += 1

    threshold = max(5, int(doc.page_count * 0.3))
    chrome = {s for s, c in top_bottom_counter.items() if c >= threshold}

    # Pass 2: per-page defect classification.
    defects: list[tuple[int, str, str]] = []
    for page_num in range(doc.page_count):
        page = doc[page_num]
        extracted = blocks_by_page.get(page_num, [])
        extracted_lower = " ".join((b.get("text") or "") for b in extracted).lower()

        for blk in page.get_text("blocks"):
            txt = blk[4].strip() if len(blk) > 4 else ""
            if len(txt) < 20:
                continue
            if _norm(txt) in chrome:
                continue
            if txt[:40].lower() in rotated_texts:
                continue
            if txt[:40].lower() not in extracted_lower:
                defects.append((page_num, "TEXT_LOST", txt[:80]))

        for b in extracted:
            if b.get("blockType") == "boilerplate":
                continue
            for ln in (b.get("text") or "").splitlines():
                if _norm(ln) in chrome:
                    defects.append((page_num, "CHROME_LEAK", ln[:80]))
                    break
            if (b.get("text") or "")[:40].lower() in rotated_texts:
                defects.append((page_num, "ROTATED_LEAK", (b.get("text") or "")[:80]))
            if len((b.get("text") or "").strip()) < 2:
                defects.append((page_num, "EMPTY_BLOCK", b.get("blockType") or "?"))

        seen: dict = {}
        for b in extracted:
            t = (b.get("text") or "").strip()
            if len(t) < 30:
                continue
            key = (b.get("blockType"), t[:60])
            if key in seen:
                defects.append((page_num, "DUPLICATE_BLOCK", t[:80]))
            seen[key] = True

    total_pages = doc.page_count
    doc.close()
    return {
        "summary": dict(Counter(d[1] for d in defects)),
        "defects": defects,
        "page_scores": dict(Counter(d[0] for d in defects)),
        "chrome_strings": sorted(chrome),
        "total_pages": total_pages,
    }


# ---------- metrics ----------

def total_defects(summary: dict) -> int:
    return sum(v for v in summary.values() if isinstance(v, int))


def read_prior(rounds_path: Path) -> tuple[dict, int]:
    if not rounds_path.exists():
        return {}, 0
    state = json.loads(rounds_path.read_text())
    if state.get("rounds"):
        prior = state["rounds"][-1].get("post_defects", {})
    else:
        prior = state.get("baseline", {})
        prior = {k: v for k, v in prior.items() if isinstance(v, int)}
    return prior, total_defects(prior)


# ---------- 5 steps ----------

def step_extract(paths: LoopPaths, log: TextIO) -> dict:
    step_header(1, "EXTRACT", log)
    line(f"  extract_pdf({paths.pdf.name}) → {paths.extraction.name}", log)
    t0 = time.time()
    result = extract_pdf(str(paths.pdf), output_path=str(paths.extraction))
    elapsed = time.time() - t0
    counts: dict = {}
    for b in result.get("blocks", []):
        k = b.get("blockType", "?")
        counts[k] = counts.get(k, 0) + 1
    line(f"  elapsed: {elapsed:.1f}s  total_blocks: {len(result.get('blocks', []))}", log)
    line(f"  by_type: {counts}", log)
    return result


def step_diagnose(paths: LoopPaths, extraction: dict, log: TextIO) -> dict:
    step_header(2, "DIAGNOSE (in-process defect scan)", log)
    t0 = time.time()
    defects = scan_defects(paths.pdf, extraction)
    elapsed = time.time() - t0
    paths.defects.write_text(json.dumps(defects, indent=2))
    summary = defects["summary"]
    top5 = sorted(defects["page_scores"].items(), key=lambda kv: -kv[1])[:5]
    line(f"  elapsed: {elapsed:.1f}s  total_defects: {total_defects(summary)}", log)
    line(f"  by_category: {summary}", log)
    line(f"  worst 5 pages: {top5}", log)
    return summary


FIX_PROMPT_TEMPLATE = """Reduce the LARGEST defect category in {defects_path} via a
generalizable mechanism fix in python/pdf_oxide/extract_for_pdflab.py.

HARD RULES:
- NO hardcoded PDF-specific strings. Use position, rotation, frequency, or
  structural cues only.
- NO edits outside python/pdf_oxide/extract_for_pdflab.py.
- MUST NOT increase any other defect category.

CATEGORIES:
- TEXT_LOST: visible text missing. Likely over-aggressive filter or bbox overlap.
- ROTATED_LEAK: rotated text emitted as content. Fix: emit as boilerplate.
- CHROME_LEAK: recurring top/bottom strings emitted as content. Fix: emit as
  boilerplate via position+frequency detection.
- DUPLICATE_BLOCK: same text emitted twice. Fix: dedupe by (bbox, text prefix).
- EMPTY_BLOCK: whitespace-only block. Fix: skip at emission.
"""


def step_fix(paths: LoopPaths, log: TextIO) -> bool:
    step_header(3, "FIX (/code-runner, worktree-isolated)", log)
    line("  allowlist: python/pdf_oxide/extract_for_pdflab.py", log)
    line(f"  read_context: {paths.defects.name}, {paths.rounds.name}", log)
    line("  DoD: re-extract + re-diagnose → total decreased, no regression", log)
    if not CODE_RUNNER.exists():
        line(f"  ERROR: code-runner missing at {CODE_RUNNER}", log)
        return False
    prompt = FIX_PROMPT_TEMPLATE.format(defects_path=paths.defects)
    dod = (
        f"python3 {Path(__file__).resolve()} "
        f"--pdf {paths.pdf} --workdir {paths.workdir} --dod-check"
    )
    cmd = [
        str(CODE_RUNNER),
        "--prompt", prompt,
        "--allowlist", "python/pdf_oxide/extract_for_pdflab.py",
        "--read-context", str(paths.defects),
        "--read-context", str(paths.rounds),
        "--backend", "codex",
        "--dod-command", dod,
    ]
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    elapsed = time.time() - t0
    line(f"  elapsed: {elapsed:.1f}s  exit: {r.returncode}", log)
    line(f"  stdout tail:\n{r.stdout[-1500:]}", log)
    if r.returncode != 0:
        line(f"  stderr tail: {r.stderr[-500:]}", log)
    return r.returncode == 0


def step_regression(prior: dict, summary: dict, log: TextIO) -> tuple[bool, list[str]]:
    step_header(4, "REGRESSION GATE", log)
    curr = total_defects(summary)
    prev = total_defects(prior) if prior else curr
    regressed = [cat for cat, n in summary.items()
                 if isinstance(prior.get(cat), int) and n > prior[cat]]
    line(f"  prior_total: {prev}", log)
    line(f"  curr_total:  {curr}", log)
    line(f"  delta:       {prev - curr:+d}", log)
    line(f"  regressions: {regressed}", log)
    passed = curr < prev and not regressed
    line(f"  VERDICT: {'PASS' if passed else 'FAIL'}", log)
    return passed, regressed


def step_tally(paths: LoopPaths, round_num: int, summary: dict, prior: dict,
               cr_ok: bool, passed: bool, regressed: list[str], log: TextIO) -> dict:
    step_header(5, "TALLY", log)
    state = json.loads(paths.rounds.read_text()) if paths.rounds.exists() else {
        "pdf": str(paths.pdf), "baseline": {}, "rounds": []
    }
    state.setdefault("rounds", [])
    curr = total_defects(summary)
    prev = total_defects(prior) if prior else curr
    status = (
        "committed" if (cr_ok and passed) else
        "reverted" if cr_ok else
        "code-runner-failed"
    )
    entry = {
        "round": round_num,
        "timestamp": utcnow_iso(),
        "post_defects": summary,
        "total_defects": curr,
        "prior_total": prev,
        "delta": prev - curr,
        "regressions": regressed,
        "code_runner_ok": cr_ok,
        "regression_gate": "PASS" if passed else "FAIL",
        "status": status,
    }
    state["rounds"].append(entry)
    paths.rounds.write_text(json.dumps(state, indent=2))
    line(f"  entry: {json.dumps(entry, indent=2)}", log)
    return entry


# ---------- driver ----------

def preflight(paths: LoopPaths, log: TextIO) -> dict:
    banner("PRE-FLIGHT: baseline measurement", log)
    if paths.rounds.exists():
        state = json.loads(paths.rounds.read_text())
        if state.get("baseline"):
            baseline = {k: v for k, v in state["baseline"].items() if isinstance(v, int)}
            line(f"  using existing baseline: {baseline}", log)
            return baseline
    extraction = step_extract(paths, log)
    summary = step_diagnose(paths, extraction, log)
    paths.rounds.write_text(json.dumps({
        "pdf": str(paths.pdf),
        "baseline": summary,
        "rounds": [],
    }, indent=2))
    line(f"  wrote baseline to {paths.rounds}", log)
    return summary


def dod_check(paths: LoopPaths) -> int:
    """Invoked by /code-runner as DoD. Re-extracts, re-diagnoses, regression-gates."""
    extraction = extract_pdf(str(paths.pdf), output_path=str(paths.extraction))
    defects = scan_defects(paths.pdf, extraction)
    paths.defects.write_text(json.dumps(defects, indent=2))
    summary = defects["summary"]
    prior, prev = read_prior(paths.rounds)
    curr = total_defects(summary)
    regressed = [cat for cat, n in summary.items()
                 if isinstance(prior.get(cat), int) and n > prior[cat]]
    print(f"curr_total={curr} prior_total={prev} regressed={regressed}")
    if curr < prev and not regressed:
        print("PASS"); return 0
    print("FAIL"); return 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", required=True, type=Path,
                    help="Path to PDF to improve extraction for")
    ap.add_argument("--workdir", type=Path, default=Path("/tmp/pdf_oxide_loop"),
                    help="State + log directory (default: /tmp/pdf_oxide_loop)")
    ap.add_argument("--max-rounds", type=int, default=30)
    ap.add_argument("--stall-limit", type=int, default=2)
    ap.add_argument("--dod-check", action="store_true",
                    help="Internal: regression gate invoked by /code-runner")
    args = ap.parse_args()

    pdf = args.pdf.resolve()
    if not pdf.exists():
        sys.exit(f"PDF not found: {pdf}")

    paths = LoopPaths(pdf=pdf, workdir=args.workdir.resolve())
    paths.ensure()

    if args.dod_check:
        return dod_check(paths)

    with (paths.log_dir / "preflight.log").open("w") as log:
        preflight(paths, log)

    stall = 0
    last_total: int | None = None

    for _ in range(args.max_rounds):
        state = json.loads(paths.rounds.read_text())
        round_num = len(state.get("rounds", [])) + 1
        log_path = paths.log_dir / f"round_{round_num:02d}.log"

        with log_path.open("w") as log:
            banner(f"ROUND {round_num}/{args.max_rounds}  pdf={pdf.name}", log)
            prior, prev = read_prior(paths.rounds)
            line(f"prior_total={prev}  prior_by_cat={prior}", log)

            cr_ok = step_fix(paths, log)
            extraction = step_extract(paths, log)
            summary = step_diagnose(paths, extraction, log)
            passed, regressed = step_regression(prior, summary, log)
            entry = step_tally(paths, round_num, summary, prior, cr_ok, passed, regressed, log)

            banner(f"ROUND {round_num} END  status={entry['status']}  "
                   f"delta={entry['delta']:+d}", log)

        print(f"log: {log_path}")

        if entry["total_defects"] == 0:
            print("=== HALT: 0 defects ==="); return 0

        if last_total is not None and entry["total_defects"] >= last_total:
            stall += 1
            print(f"stall {stall}/{args.stall_limit}")
            if stall >= args.stall_limit:
                print(f"=== HALT: {args.stall_limit} rounds without improvement ===")
                return 0
        else:
            stall = 0
        last_total = entry["total_defects"]

    print(f"=== HALT: {args.max_rounds} rounds completed ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
