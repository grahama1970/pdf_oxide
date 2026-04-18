# PDF Oxide Self-Improvement Loop — Review Request

## The Problem I'm Solving

`pdf_oxide` is a Rust-native, MIT-licensed PDF extraction engine meant to replace
PyMuPDF (AGPL). `extract_for_pdflab.py` is the Python layer that consumes
`pdf_oxide`'s raw extraction primitives and produces structured blocks (with bbox +
blockType: header/text/table/figure/boilerplate) for the PDF Lab UI at
`http://localhost:3002/#pdf-lab`.

I need a deterministic self-improvement loop that:

1. Runs the extractor on a PDF
2. Identifies defects per page
3. Hands the defect list to `/code-runner` (codex backend, worktree-isolated LLM)
4. Regression-gates the proposed fix
5. Appends round outcome to a tally
6. Repeats until defects = 0 or N stall rounds

Runs 30+ rounds autonomously. The human checks the audit trail at the end.

## The Critical Issue I Need Reviewed

My first draft of the defect scanner used **PyMuPDF (fitz)** as ground truth. The
project owner correctly called this out as circular — using the thing we're trying
to REPLACE to benchmark the replacement. The review request is to validate the
**rewrite plan** that replaces PyMuPDF entirely with pdf_oxide's own raw APIs +
an optional VLM fallback.

## Rewrite Plan (Not Yet Implemented)

### Ground-truth sources (all from pdf_oxide itself — no PyMuPDF)

| Source API                          | Provides                               |
|-------------------------------------|----------------------------------------|
| `doc.get_toc()`                     | Section-header page map (authoritative) |
| `doc.extract_spans(page)`           | Text spans + bbox + font_size + bold    |
| `doc.extract_tables(page)`          | Raw tables with cell bboxes             |
| `doc.extract_images(page)`          | Raster images with bbox                 |
| `doc.extract_paths(page)`           | Vector graphics (line art figures)      |
| `doc.extract_lines(page)`           | Rules/dividers                          |
| `doc.extract_words(page)`           | Token-level with bbox                   |
| `doc.page_dimensions(page)`         | Page size for bbox normalization        |

### Full defect taxonomy (deterministic, 10 categories)

1. **MISSING_TEXT** — raw span has no extracted block covering its bbox
2. **MISSING_TABLE** — `extract_tables` row on page, no `type=table` block covers it
3. **MISSING_FIGURE** — `extract_images`/`extract_paths` element, no `type=figure` block
4. **MISSING_HEADER** — font-size outlier span, no `type=header` block; OR TOC entry for page has no matching header block
5. **MISSING_REQUIREMENT** — span contains modal verb (SHALL/MUST/SHOULD/WILL) but not in any extracted block
6. **MISCLASSIFIED** — block bbox contains image → should be figure; cells → table; outlier font → header
7. **TEXT_MISMATCH** — block.text ≠ concatenated spans inside its bbox (ligature/whitespace variance)
8. **OVERLAP** — same raw span in 2+ blocks of different types
9. **PHANTOM** — block bbox contains no spans, tables, or images (invented from nothing)
10. **WRONG_SECTION_STRUCTURE** — TOC says page=glossary but no term-definition table found

### VLM fallback (only for ambiguous rows)

Triggers: text partial-match, bbox with both image+text, path-only region (figure or divider?), type-ambiguous block.

Flow: `/pdf-screenshot` renders the page → draw red outline on the ambiguous bbox →
send composite to Gemini via `/scillm` or `/review-pdf` → VLM returns verdict.
Cached by (page, bbox, candidate_type) so reruns don't re-query.

Defect entries record `source: "deterministic"` or `source: "vlm"`.

### The 5-step round (unchanged from current)

    STEP 1 — EXTRACT          pdf_oxide.extract_for_pdflab.extract_pdf (in-process)
    STEP 2 — DIAGNOSE         scan_defects() — the rewrite under review
    STEP 3 — FIX              /code-runner worktree LLM loop (subprocess)
    STEP 4 — REGRESSION GATE  post-fix total defects < prior AND no cat regressed
    STEP 5 — TALLY            append round entry to rounds.json

## What I Want Reviewed

1. **Is the ground-truth choice sound?** pdf_oxide's own raw APIs as ground truth
   for the layout/classification logic that sits on top of them. Circular? Or fine
   because the raw-span extraction is the trusted primitive and we're testing the
   block-assignment logic?
2. **Is the defect taxonomy complete?** What element types am I missing?
3. **Is the VLM fallback architecture right?** Determinstic first, VLM only for
   ambiguity, cached. Alternative: always overlay + VLM for a 2nd opinion?
4. **Is the 5-step round structure correct for `/code-runner`?** The DoD command
   is re-extract → re-diagnose → regression_check. Should the fix step use a
   different backend (e.g. gpt-5.3-codex vs claude)?
5. **PyMuPDF removal** — any API I'll lose that pdf_oxide doesn't cover?
   (I've verified: extract_spans, extract_tables, extract_images, extract_paths,
   extract_words, extract_text, page_dimensions, get_toc are all exposed.)

---

## File 1: The Loop Harness (CURRENT — uses fitz, needs rewrite)

`scripts/pdf_oxide_loop/run_rounds.py`

```python
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
```

---

## File 2: The Extractor Under Test

`python/pdf_oxide/extract_for_pdflab.py`

This is what the LLM edits in STEP 3 of each round. Contains hardcoded NIST-specific
patterns I want to remove and replace with generalizable position/frequency/rotation
mechanisms.

```python
"""PDF extraction for PDF Lab UI.

Creates extraction JSON compatible with ux-lab/PdfLabView.
Uses PyMuPDF (fitz) for text/table extraction and Python classification.

This is the CANONICAL source for classification logic.
tests/test_extraction_classification.py mirrors these functions.
"""
import json
import re
from pathlib import Path
import fitz

import pdf_oxide


# =============================================================================
# Classification Constants (keep in sync with test_extraction_classification.py)
# =============================================================================

HEADER_CONTINUATIONS = [
    'Systems and Organizations',
    'Information Systems and Organizations',
]

SIMPLE_HEADERS = ['ABSTRACT', 'KEYWORDS', 'ERRATA', 'REFERENCES', 'GLOSSARY', 'ACRONYMS']

BOILERPLATE = ['NIST Special Publication', 'Document Title Page 1']

# Running header patterns (page chrome that repeats)
RUNNING_HEADER_PATTERNS = [
    r'^NIST\s+SP\s+800-\d+.*_{10,}',  # NIST SP with underline
    r'_{50,}$',  # Long underline at end
]

# Control family prefixes (NIST SP 800-53)
CONTROL_FAMILIES = [
    'AC', 'AT', 'AU', 'CA', 'CM', 'CP', 'IA', 'IR', 'MA', 'MP',
    'PE', 'PL', 'PM', 'PS', 'PT', 'RA', 'SA', 'SC', 'SI', 'SR'
]


# =============================================================================
# Classification Functions
# =============================================================================

def classify_toc_title(title: str) -> str:
    """Classify a TOC entry title as header or text."""
    title = title.strip()

    # Document title (NIST SP...)
    if re.match(r'^NIST\s+SP', title, re.I):
        return 'header'

    # Chapter/section headers (including APPENDIX)
    if re.match(r'^(CHAPTER|INTRODUCTION|THE FUNDAMENTALS|THE CONTROLS|APPENDIX)', title, re.I):
        return 'header'

    # Numbered sections like "1.1 PURPOSE"
    if re.match(r'^\d+\.\d+\s+[A-Z]', title):
        return 'header'

    # Control IDs like "AC-1 POLICY"
    control_pattern = '|'.join(CONTROL_FAMILIES)
    if re.match(rf'^({control_pattern})-\d+', title):
        return 'header'

    # Simple section names
    if title.upper() in SIMPLE_HEADERS:
        return 'header'

    return 'text'


def _is_likely_sentence(text: str) -> bool:
    """Check if text is likely a sentence (body text) rather than a title."""
    # Long text is likely a sentence
    if len(text) > 150:
        return True

    # Contains sentence indicators (common verbs/phrases in body text)
    sentence_indicators = [
        r'\bis\b', r'\bare\b', r'\bwas\b', r'\bwere\b',
        r'\bprovides?\b', r'\bdescribes?\b', r'\bdefines?\b',
        r'\bincludes?\b', r'\bcontains?\b', r'\baddresses\b',
        r'\bensures?\b', r'\brequires?\b', r'\bshall\b', r'\bmust\b'
    ]
    text_lower = text.lower()
    for pattern in sentence_indicators:
        if re.search(pattern, text_lower):
            return True

    # Multiple sentences (period followed by capital letter)
    if re.search(r'\.\s+[A-Z]', text):
        return True

    return False

def classify_block(text: str) -> str:
    """Classify a block based on its text content."""
    clean_text = text.strip()

    # Page numbers
    if re.match(r'^Page\s+\d+(?:\s+of\s+\d+)?$', clean_text, re.I):
        return 'page_number'

    # Running headers (page chrome with underlines)
    for pattern in RUNNING_HEADER_PATTERNS:
        if re.search(pattern, clean_text, re.I):
            return 'boilerplate'

    # Boilerplate
    if clean_text in BOILERPLATE:
        return 'boilerplate'

    # Header continuations
    if clean_text in HEADER_CONTINUATIONS:
        return 'header'

    # NIST SP document titles
    if re.match(r'^NIST\s+SP', clean_text, re.I):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Table content patterns
    if '|' in clean_text and len(clean_text.split('|')) >= 3:
        return 'table'

    # Chapter/section headers
    if re.match(r'^(CHAPTER|INTRODUCTION|THE FUNDAMENTALS|THE CONTROLS|APPENDIX)', clean_text, re.I):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Numbered sections
    if re.match(r'^\d+\.\d+\s+[A-Z]', clean_text):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Control IDs
    control_pattern = '|'.join(CONTROL_FAMILIES)
    if re.match(rf'^({control_pattern})-\d+', clean_text):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Control enhancements
    if re.match(r'^\(\d+\)\s+[A-Z]', clean_text):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Table of Contents header
    if 'Table of Contents' in clean_text:
        return 'header'

    # Simple section names
    if clean_text.upper() in SIMPLE_HEADERS:
        return 'header'

    return 'text'


def parse_toc_entry(line: str) -> dict | None:
    """Parse a TOC line with dot leaders."""
    # Simple pattern: title ... page_number
    match = re.match(r'^(.+?)\.{3,}\s*(\d+)\s*$', line.strip())
    if match:
        return {
            'title': match.group(1).strip(),
            'page': int(match.group(2))
        }
    return None


def boxes_overlap(bbox1, bbox2, threshold=0.1):
    """Check if two bounding boxes overlap significantly."""
    x1_min, y1_min, x1_max, y1_max = bbox1
    x2_min, y2_min, x2_max, y2_max = bbox2

    x_overlap = max(0, min(x1_max, x2_max) - max(x1_min, x2_min))
    y_overlap = max(0, min(y1_max, y2_max) - max(y1_min, y2_min))
    intersection = x_overlap * y_overlap

    area1 = (x1_max - x1_min) * (y1_max - y1_min)
    area2 = (x2_max - x2_min) * (y2_max - y2_min)
    smaller = min(area1, area2)
    if smaller <= 0:
        return False
    return (intersection / smaller) >= threshold


# =============================================================================
# TOC-based section typing
# =============================================================================

def _normalize_toc_entries(toc_entries_or_doc) -> list[dict]:
    """Accept either a TOC entry list or a pdf_oxide document."""
    if hasattr(toc_entries_or_doc, 'get_toc'):
        toc_entries = toc_entries_or_doc.get_toc().get('entries', [])
    elif isinstance(toc_entries_or_doc, dict):
        toc_entries = toc_entries_or_doc.get('entries', [])
    else:
        toc_entries = toc_entries_or_doc
    return [entry for entry in toc_entries if isinstance(entry, dict)]


def _structured_toc_entries(oxide_doc) -> list[dict]:
    """Return pdf_oxide TOC entries with a non-empty title."""
    return [
        entry for entry in _normalize_toc_entries(oxide_doc)
        if (entry.get('title') or entry.get('text') or '').strip()
    ]


def _classify_section_title(title: str) -> str:
    upper = title.upper()
    if 'ERRATA' in upper:
        return 'errata'
    if 'GLOSSARY' in upper:
        return 'glossary'
    if 'ACRONYM' in upper:
        return 'acronyms'
    if ('SUMMARY' in upper or 'SUMMARIES' in upper) and ('CONTROL' in upper or 'APPENDIX' in upper):
        return 'summaries'
    if 'REFERENCE' in upper:
        return 'references'
    return 'body'


def build_section_ranges(toc_entries_or_doc, effective_page_count: int) -> list[dict]:
    """Build non-decreasing section ranges from TOC entries.

    Tests exercise both one-based synthetic TOC pages and zero-based pdf_oxide
    TOC pages, so the base is inferred from the presence of page 0.
    """
    entries = _normalize_toc_entries(toc_entries_or_doc)
    if effective_page_count <= 0:
        return []

    headers = [entry for entry in entries if isinstance(entry.get('page'), int)]
    headers.sort(key=lambda entry: entry['page'])

    if not headers:
        return [{'title': 'document', 'start': 0, 'end': effective_page_count - 1, 'type': 'body'}]

    page_base = 0 if any(entry.get('page') == 0 for entry in headers) else 1
    max_index = effective_page_count - 1
    ranges = []

    for idx, entry in enumerate(headers):
        title = (entry.get('title') or entry.get('text') or '').strip()
        start = max(0, min(entry['page'] - page_base, max_index))

        if idx + 1 < len(headers):
            next_start = max(0, min(headers[idx + 1]['page'] - page_base, max_index))
            end = max(start, next_start - 1)
        else:
            end = max_index

        ranges.append({
            'title': title,
            'start': start,
            'end': end,
            'type': _classify_section_title(title),
        })

    return ranges


def section_type_for_page(page_num: int, ranges: list[dict]) -> str:
    """Look up which section a page belongs to."""
    for section in ranges:
        if section['start'] <= page_num <= section['end']:
            return _classify_section_title(section.get('title', ''))
    return 'body'


def _merge_bracket_citation_rows(table_text: str) -> str:
    """Attach glossary citation rows to the preceding term definition."""
    lines = [line.rstrip() for line in table_text.splitlines() if line.strip()]
    if len(lines) < 2:
        return table_text

    citation_re = re.compile(r'^\[\s*[^\]]+\s*\]$')
    merged = [lines[0]]

    for line in lines[1:]:
        stripped = line.strip()
        if '|' in stripped:
            left, right = [part.strip() for part in stripped.split('|', 1)]
            if citation_re.match(left) and len(merged) > 1 and '|' in merged[-1]:
                prev_left, prev_right = [part.strip() for part in merged[-1].split('|', 1)]
                extra = ' '.join(part for part in (left, right) if part)
                merged[-1] = f'{prev_left} | {prev_right} {extra}'.strip()
                continue
        elif citation_re.match(stripped) and len(merged) > 1 and '|' in merged[-1]:
            prev_left, prev_right = [part.strip() for part in merged[-1].split('|', 1)]
            merged[-1] = f'{prev_left} | {prev_right} {stripped}'.strip()
            continue
        merged.append(stripped)

    return '\n'.join(merged)


def _strip_watermark_phrases(text: str) -> str:
    """Remove watermark boilerplate fragments from extracted table text."""
    cleaned = text
    for phrase in WATERMARK_PHRASES:
        cleaned = cleaned.replace(phrase, '')
    cleaned = re.sub(r' {2,}', ' ', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


WATERMARK_PHRASES = (
    'This publication is available',
    'https://doi.org',
    'free of charge from',
)


def _normalize_chrome_text(text: str) -> str:
    """Normalize text for frequency-based chrome detection."""
    return re.sub(r'\d+', '#', text.strip())[:120]


def detect_doc_chrome(doc, max_pages: int, top_bottom_frac: float = 0.08,
                      min_page_fraction: float = 0.30) -> tuple[set, set]:
    """Document-wide running-chrome and rotated-watermark detection.

    Generalizable (no PDF-specific content). Uses:
      - y-position: text in top/bottom N% of page is chrome candidate.
      - line rotation (line.dir): non-horizontal lines are watermark candidates.
      - frequency: any normalized string recurring in >=min_page_fraction of
        pages is flagged as chrome/watermark.

    Returns (chrome_strings, watermark_strings) as sets of normalized strings.
    """
    from collections import Counter
    top_bottom_counter: Counter = Counter()
    sidebar_counter: Counter = Counter()
    pages_scanned = min(max_pages, doc.page_count)

    for page_num in range(pages_scanned):
        page = doc[page_num]
        h = page.rect.height
        for blk in page.get_text('dict').get('blocks', []):
            if blk.get('type') != 0:
                continue
            for line in blk.get('lines', []):
                spans = line.get('spans', [])
                text = ' '.join(s.get('text', '').strip() for s in spans).strip()
                if not text or len(text) < 3:
                    continue
                norm = _normalize_chrome_text(text)
                bbox = line.get('bbox', (0.0, 0.0, 0.0, 0.0))
                _, y0, _, y1 = bbox
                dx, dy = line.get('dir', (1.0, 0.0))
                if abs(dy) > 0.1:
                    sidebar_counter[norm] += 1
                    continue
                if y1 < h * top_bottom_frac or y0 > h * (1.0 - top_bottom_frac):
                    top_bottom_counter[norm] += 1

    threshold = max(5, int(pages_scanned * min_page_fraction))
    chrome = {s for s, c in top_bottom_counter.items() if c >= threshold}
    watermarks = {s for s, c in sidebar_counter.items() if c >= threshold}
    return chrome, watermarks


def _line_is_rotated(line: dict) -> bool:
    dx, dy = line.get('dir', (1.0, 0.0))
    return abs(dy) > 0.1

# Page-footer row patterns: "APPENDIX X | PAGE N", "CHAPTER N | PAGE M".
_FOOTER_ROW_RE = re.compile(
    r'^\s*(APPENDIX\s+[A-Z]|CHAPTER\s+\w+)\s*\|\s*PAGE\s+\d+\s*$',
    re.IGNORECASE,
)
# Rotated-watermark fragments that leak into leftmost columns (e.g. NIST "53r5").
_SIDEBAR_FRAGMENT_RE = re.compile(r'^\d+[A-Za-z]+\d*$')


def _is_footer_or_watermark_row(row_text: str) -> bool:
    stripped = row_text.strip()
    if not stripped:
        return False
    if _FOOTER_ROW_RE.match(stripped):
        return True
    first_cell = stripped.split('|', 1)[0].strip()
    return bool(_SIDEBAR_FRAGMENT_RE.match(first_cell))


def _strip_sidebar_fragment(cell: str) -> str:
    tokens = cell.split()
    if len(tokens) >= 2 and _SIDEBAR_FRAGMENT_RE.match(tokens[0]):
        return ' '.join(tokens[1:])
    return cell


def extract_pdf(pdf_path: str, output_path: str | None = None, max_pages: int | None = None) -> dict:
    """Extract PDF content for PDF Lab UI - optimized for speed."""
    doc = fitz.open(pdf_path)
    pdf_name = Path(pdf_path).name

    # TOC is the structure source for section-aware extraction.
    oxide_doc = pdf_oxide.open(pdf_path)
    toc_entries = _structured_toc_entries(oxide_doc)

    total_pages = doc.page_count
    if max_pages is not None:
        total_pages = min(total_pages, max_pages)

    section_ranges = build_section_ranges(toc_entries, total_pages)

    # Document-wide chrome/watermark detection (position + rotation + frequency).
    # Generalizable — no PDF-specific strings.
    doc_chrome, doc_watermarks = detect_doc_chrome(doc, total_pages)

    blocks = []
    block_id = 0

    print(f'Extracting {total_pages} pages from {pdf_name}...')
    print(f'  pdf_oxide TOC sections:')
    for r in section_ranges:
        print(f"    pages {r['start']}-{r['end']}: [{r['type']}] {r['title'][:60]}")

    for page_num in range(total_pages):
        if page_num % 50 == 0:
            print(f'  Page {page_num}...')

        page = doc[page_num]
        page_width, page_height = page.rect.width, page.rect.height
        section_type = section_type_for_page(page_num, section_ranges)

        # Borderless tabular sections use the shared Rust definition-list path so
        # glossary/acronym extraction is not reimplemented per caller.
        if section_type in ('glossary', 'acronyms'):
            x_mid_frac = 0.25 if section_type == 'acronyms' else 0.35
            try:
                rust_tables = oxide_doc.extract_tables(
                    page_num,
                    strategy='definition_list',
                    x_mid_ratio=x_mid_frac,
                )
            except Exception:
                rust_tables = []

            for table in rust_tables:
                rows = table.get('data') or []
                table_text_parts = ['TERM | DEFINITION']
                for row in rows:
                    if len(row) < 2:
                        continue
                    term = _strip_sidebar_fragment(_strip_watermark_phrases(str(row[0]).strip()))
                    definition = _strip_watermark_phrases(str(row[1]).strip())
                    if term and definition and not _is_footer_or_watermark_row(f'{term} | {definition}'):
                        table_text_parts.append(f'{term} | {definition}')

                if len(table_text_parts) == 1:
                    continue

                table_text = _merge_bracket_citation_rows('\n'.join(table_text_parts))
                table_text_parts = table_text.splitlines()

                x0, y0, x1, y1 = table.get('bbox', (0.0, 0.0, page_width, page_height))
                norm_bbox = [
                    x0 / page_width,
                    y0 / page_height,
                    x1 / page_width,
                    y1 / page_height,
                ]
                blocks.append({
                    'id': f'block_{block_id}',
                    'page': page_num,
                    'bbox': norm_bbox,
                    'blockType': 'table',
                    'text': '\n'.join(table_text_parts),
                    'qids': None,
                    'tocEntries': None,
                    'confidence': 0.9,
                    'tableKind': section_type,
                })
                block_id += 1
            continue

        table_bboxes = []
        try:
            tables = page.find_tables()
            table_bboxes = [t.bbox for t in tables.tables]

            for i, table in enumerate(tables.tables):
                bbox = table.bbox
                norm_bbox = [
                    bbox[0] / page_width,
                    bbox[1] / page_height,
                    bbox[2] / page_width,
                    bbox[3] / page_height,
                ]

                try:
                    table_data = table.extract()
                    if table_data:
                        table_text_parts = []
                        for row in table_data:
                            if row:
                                row_cells = [str(cell).strip() for cell in row if cell and str(cell).strip()]
                                if row_cells:
                                    table_text_parts.append(' | '.join(row_cells))
                        table_text = '\n'.join(table_text_parts) if table_text_parts else f'[Table {i+1}]'
                    else:
                        table_text = f'[Table {i+1}]'
                except Exception:
                    table_text = f'[Table {i+1}]'

                table_text = _strip_watermark_phrases(table_text)
                table_text = '\n'.join(
                    line for line in table_text.splitlines()
                    if not _is_footer_or_watermark_row(line)
                )

                clean_text = table_text.replace('\n', ' ').strip()
                if len(table_text) < 30 and '\n' not in table_text:
                    continue
                if clean_text.isupper() and len(clean_text.split()) < 8 and '\n' not in table_text:
                    continue

                blocks.append({
                    'id': f'block_{block_id}',
                    'page': page_num,
                    'bbox': norm_bbox,
                    'blockType': 'table',
                    'text': table_text,
                    'qids': None,
                    'tocEntries': None,
                    'confidence': 0.95,
                })
                block_id += 1
        except Exception:
            table_bboxes = []

        # Simple text extraction
        try:
            text_dict = page.get_text('dict')

            for blk in text_dict.get('blocks', []):
                if blk.get('type') != 0:  # Skip image blocks
                    continue

                bbox = blk['bbox']

                # Skip if overlaps with table
                if any(boxes_overlap(bbox, tb, 0.5) for tb in table_bboxes):
                    continue

                # Blocks whose lines are all rotated (sidebar/watermark) → boilerplate.
                lines = blk.get('lines', [])
                if lines and all(_line_is_rotated(ln) for ln in lines):
                    rotated_text_parts = []
                    for line in lines:
                        line_parts = [s.get('text', '').strip() for s in line.get('spans', [])
                                      if s.get('text', '').strip()]
                        if line_parts:
                            rotated_text_parts.append(' '.join(line_parts))
                    rotated_text = '\n'.join(rotated_text_parts).strip()
                    if rotated_text:
                        norm_bbox = [
                            bbox[0] / page_width, bbox[1] / page_height,
                            bbox[2] / page_width, bbox[3] / page_height,
                        ]
                        blocks.append({
                            'id': f'block_{block_id}',
                            'page': page_num,
                            'bbox': norm_bbox,
                            'blockType': 'boilerplate',
                            'text': rotated_text,
                            'qids': None,
                            'tocEntries': None,
                            'confidence': 0.9,
                        })
                        block_id += 1
                    continue

                # Simple text extraction (skip rotated lines within mixed blocks).
                text_parts = []
                for line in lines:
                    if _line_is_rotated(line):
                        continue
                    line_text = []
                    for span in line.get('spans', []):
                        span_text = span.get('text', '').strip()
                        if span_text:
                            line_text.append(span_text)
                    if line_text:
                        text_parts.append(' '.join(line_text))

                text = '\n'.join(text_parts).strip()
                if not text or len(text) < 3:
                    continue

                # Doc-wide chrome filter: recurring top/bottom strings become boilerplate.
                norm = _normalize_chrome_text(text)
                if norm in doc_chrome or norm in doc_watermarks:
                    # Emit as boilerplate so UI can still show, but not as content.
                    norm_bbox = [
                        bbox[0] / page_width, bbox[1] / page_height,
                        bbox[2] / page_width, bbox[3] / page_height,
                    ]
                    blocks.append({
                        'id': f'block_{block_id}',
                        'page': page_num,
                        'bbox': norm_bbox,
                        'blockType': 'boilerplate',
                        'text': text,
                        'qids': None,
                        'tocEntries': None,
                        'confidence': 0.9,
                    })
                    block_id += 1
                    continue

                # Simple bbox normalization
                norm_bbox = [
                    bbox[0] / page_width,
                    bbox[1] / page_height,
                    bbox[2] / page_width,
                    bbox[3] / page_height,
                ]

                # Classify
                block_type = classify_block(text)

                # Simple TOC detection
                toc_entries = None
                if '...' in text:
                    lines = text.split('\n')
                    parsed_entries = []
                    for line in lines[:5]:  # Limit for speed
                        entry = parse_toc_entry(line.strip())
                        if entry:
                            entry_type = classify_toc_title(entry['title'])
                            parsed_entries.append({
                                'title': entry['title'],
                                'page': entry['page'],
                                'type': entry_type,
                            })
                    if parsed_entries:
                        toc_entries = parsed_entries
                        block_type = 'header'

                blocks.append({
                    'id': f'block_{block_id}',
                    'page': page_num,
                    'bbox': norm_bbox,
                    'blockType': block_type,
                    'text': text,
                    'qids': None,
                    'tocEntries': toc_entries,
                    'confidence': 0.95,
                })
                block_id += 1
        except Exception as e:
            print(f'Warning: Error processing page {page_num}: {e}')
            continue

    result = {
        'pdfUrl': f'/{pdf_name}',
        'pageCount': total_pages,
        'blocks': blocks,
    }

    # Print summary
    type_counts = {}
    for b in blocks:
        t = b['blockType']
        type_counts[t] = type_counts.get(t, 0) + 1

    print(f'\nExtraction complete:')
    for t, c in sorted(type_counts.items()):
        print(f'  {t}: {c}')

    if output_path:
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2)
        print(f'\nSaved to: {output_path}')

    return result


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Extract PDF for PDF Lab UI')
    parser.add_argument('pdf_path', help='Path to PDF file')
    parser.add_argument('-o', '--output', help='Output JSON path')
    parser.add_argument('--max-pages', type=int, help='Maximum pages to process (for testing)')

    args = parser.parse_args()

    output = args.output
    if not output:
        pdf_name = Path(args.pdf_path).stem
        output = f'{pdf_name}-extraction.json'

    extract_pdf(args.pdf_path, output, max_pages=args.max_pages)
```

---

## File 3: The Current Defect Scanner (uses fitz, to be REPLACED)

`/tmp/page_diff.py`

```python
"""Per-page extraction defect scanner.

Compares extract_for_pdflab.py JSON output against PyMuPDF ground truth
for every page. Identifies elements that failed to extract properly.

Defect categories (mechanism-level):
  TEXT_LOST:         Visible text on page not captured in any block
  CHROME_LEAK:       Running header/footer text appears as content block
  ROTATED_LEAK:      Rotated/sidebar text appears as content block
  BLOCK_MISCLASS:    Block text looks like a header/table but typed as text (or vice versa)
  EMPTY_BLOCK:       Extracted block has empty/whitespace text
  DUPLICATE_BLOCK:   Same text extracted twice on same page
"""
import json
import sys
import re
from collections import Counter, defaultdict
from pathlib import Path

import fitz

PDF = '/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf'
EXTRACTION = '/tmp/nist_reextraction.json'

def main():
    data = json.loads(Path(EXTRACTION).read_text())
    blocks_by_page = defaultdict(list)
    for b in data['blocks']:
        blocks_by_page[b.get('page')].append(b)

    doc = fitz.open(PDF)
    defects = []  # (page, category, detail)

    # Pass 1: find running headers/footers by frequency across pages.
    # A line that appears in top 8% or bottom 8% of page on >=30% of pages is chrome.
    top_bottom_counter = Counter()
    for page_num in range(doc.page_count):
        page = doc[page_num]
        h = page.rect.height
        for blk in page.get_text('blocks'):
            x0, y0, x1, y1, text = blk[:5]
            text = text.strip()
            if not text:
                continue
            if y1 < h * 0.08 or y0 > h * 0.92:
                # normalize page-number-bearing lines
                norm = re.sub(r'\d+', '#', text)[:120]
                top_bottom_counter[norm] += 1
    threshold = max(5, int(doc.page_count * 0.3))
    chrome_strings = {s for s, c in top_bottom_counter.items() if c >= threshold}

    # Pass 2: per-page defect scan.
    for page_num in range(doc.page_count):
        page = doc[page_num]
        w, h = page.rect.width, page.rect.height
        visible_blocks = page.get_text('blocks')
        extracted = blocks_by_page.get(page_num, [])

        # Collect extracted text as a single string for overlap checks.
        extracted_text = ' '.join((b.get('text') or '') for b in extracted)
        extracted_text_lower = extracted_text.lower()

        # A. Missing text: visible text not in extracted output.
        for blk in visible_blocks:
            x0, y0, x1, y1, txt = blk[:5]
            txt_clean = txt.strip()
            if len(txt_clean) < 20:  # skip trivial
                continue
            norm = re.sub(r'\d+', '#', txt_clean)[:120]
            if norm in chrome_strings:
                continue  # chrome — not expected to appear as content
            # Check first 40 chars appear in extracted output
            snippet = txt_clean[:40].lower()
            if snippet not in extracted_text_lower:
                defects.append((page_num, 'TEXT_LOST', txt_clean[:80]))

        # B. Chrome leak in extracted content.
        for b in extracted:
            if b.get('blockType') == 'boilerplate':
                continue
            txt = (b.get('text') or '').strip().splitlines()
            for line in txt:
                norm = re.sub(r'\d+', '#', line.strip())[:120]
                if norm and norm in chrome_strings:
                    defects.append((page_num, 'CHROME_LEAK', line[:80]))
                    break

        # C. Rotated leak: extracted text contains fragments that look like rotated watermarks
        #    (heuristic: leftmost 5% of page with very narrow bbox in horizontal axis).
        for b in extracted:
            if b.get('blockType') == 'boilerplate':
                continue
            bbox = b.get('bbox', [0,0,1,1])
            # bbox is normalized in extract_for_pdflab
            if bbox[2] - bbox[0] < 0.06 and bbox[0] < 0.06:
                defects.append((page_num, 'ROTATED_LEAK', (b.get('text') or '')[:80]))

        # D. Empty blocks.
        for b in extracted:
            txt = (b.get('text') or '').strip()
            if not txt or len(txt) < 2:
                defects.append((page_num, 'EMPTY_BLOCK', b.get('blockType') or '?'))

        # E. Duplicate blocks.
        seen = {}
        for b in extracted:
            txt = (b.get('text') or '').strip()
            if len(txt) < 30:
                continue
            key = (b.get('blockType'), txt[:60])
            if key in seen:
                defects.append((page_num, 'DUPLICATE_BLOCK', txt[:80]))
            seen[key] = True

    # Summary
    by_cat = Counter(d[1] for d in defects)
    print(f'Total pages: {doc.page_count}')
    print(f'Total extracted blocks: {len(data["blocks"])}')
    print(f'Chrome strings (freq>={threshold}):')
    for s, c in sorted(top_bottom_counter.items(), key=lambda x: -x[1])[:10]:
        if c >= threshold:
            print(f'  {c:4d}x "{s[:80]}"')
    print()
    print(f'Defect categories:')
    for cat, n in by_cat.most_common():
        print(f'  {cat}: {n}')
    print()
    # Examples per category
    for cat in by_cat:
        examples = [d for d in defects if d[1] == cat][:5]
        print(f'--- {cat} (first 5) ---')
        for p, c, detail in examples:
            print(f'  page {p}: {detail}')
        print()

    # Per-page worst offenders
    page_scores = Counter(d[0] for d in defects)
    print('Top 20 worst pages by defect count:')
    for p, n in page_scores.most_common(20):
        cats = Counter(d[1] for d in defects if d[0] == p)
        print(f'  page {p}: {n} defects — {dict(cats)}')

    # Write full report
    Path('/tmp/nist_defects.json').write_text(json.dumps({
        'summary': dict(by_cat),
        'chrome_strings': [s for s, c in top_bottom_counter.items() if c >= threshold],
        'defects': defects,
        'page_scores': dict(page_scores),
    }, indent=2))

if __name__ == '__main__':
    main()
```

## Request to Reviewer

Please assess:

1. **Architecture:** Does the rewrite plan make sense? What would you change?
2. **Defect taxonomy:** Complete? Missing categories? Any you'd drop?
3. **Ground truth:** Is pdf_oxide self-benchmarking sound, or do I need an
   independent reference (e.g. OCR of rendered pages)?
4. **VLM fallback:** Sufficient? Or should visual verification be mandatory for
   every block?
5. **Loop harness:** Is the 5-step structure + `/code-runner` integration correct?
   Any anti-patterns?
6. **Bonus:** Anything in `extract_for_pdflab.py` that screams "this will block
   convergence" — e.g. hardcoded regexes, missing section types, bad defaults.

Return a structured diff or concrete recommendations. Thanks.
