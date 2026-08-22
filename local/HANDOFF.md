# Handoff Report: pdf_oxide

**Timestamp**: 2026-08-22
**Active Agent**: Claude (Fable 5, Claude Code)
**Repository**: `/home/graham/workspace/experiments/pdf_oxide` (primary checkout — the ONLY valid work location)
**Branch at handoff**: `main` @ `e2dc53e1`, identical to `origin/main`, 0 stranded commits
**Immutable goal (PDF-EXTRACTION-GS001)**: NOT fully met — census at 320/346 resolved; 7 live defects, 19 unadjudicated

Every factual claim below was verified by a command read-back on 2026-08-20/21;
nothing is carried forward from older handoffs. The previous HANDOFF.md
(2026-07-30, branch `codex/issue-22-live-figure-content-20260728`) is fully
superseded: that branch's work was either landed on main or re-implemented
minimally, and its census numbers were stale.

## 1. Project Overview

- **Ecosystem**: Rust extractor with Python bindings (pyo3/maturin), plus PDF
  Lab evidence tooling in Python under `scripts/pdf_lab/`.
- **Core purpose**: fast PDF text/structure extraction; current hardening
  target is a 346-finding historical defect census on NIST SP 800-53r5
  (738 pages), adjudicated finding-by-finding against fresh extraction.
- **Working method (established, do not regress)**: one failing predicate
  BEFORE every fix; live corpus read-back AFTER; an agentic-evals fixture case
  locking every resolved defect (`fixtures/agentic_eval.json`, currently
  10/10 PASS, readiness READY, includes a negative control). GitHub closure
  goes through `/ask fix-issues --execute` with a named verify command.

## 2. Current State

### Census (the main scoreboard)
`artifacts/pdf_lab/census_regen_20260820/seed.json` (committed):

```
resolved_by_current_extraction : 320
unverified                     :  19
still_broken                   :   7
```

Was 47/299 carried-forward since 07-29. Every adjudication carries quoted
fresh evidence, commit, timestamp, and decided status (durable replay).
Evidence root: `artifacts/pdf_lab/census_regen_20260820/current_evidence/`
(55 pages, materialized at af0680fc). NOTE: page dirs are untracked bulk; the
manifest + ledger + report are committed.

### GitHub issues
Open: **#15, #17 only** — both unmaintained-crate advisories (`paste`
transitive via rav1e/tract-core; `ttf-parser` used directly AND via fontdb).
No patched versions exist; they can never pass a verify command. They are a
POLICY decision for the human, not agentic fixes. #2 #3 #6 #20 #21 #22 etc.
all closed with per-issue verify receipts.

### Test/tooling state
- `cargo test --lib --features python,rendering,office`: 4629 passed, 0 failed.
- Word-fidelity sweep: 55/55 census pages, 0 missing words vs PyMuPDF.
- All standing predicates PASS (`scripts/pdf_lab/check_*.py`).
- Known pre-existing failures NOT owned by this pass: 6 pytest collection
  errors (test_sampler_content.py etc.) and
  test_nist_page456_control_table_headers.py cell-bbox test — both reproduce
  with all recent changes stashed.
- The 2 dirty tracked files (`.batch_state.json`, `security-scan_task_state.json`)
  belong to other lanes. Do not clean or commit them.

### Build/interpreter traps (cost hours; read carefully)
1. `PYTHONPATH=python` tests load `python/pdf_oxide/pdf_oxide.abi3.so` — a
   COMMITTED STALE BINARY unless you refresh it from the wheel after every
   `maturin build`. Pattern: build wheel → `unzip -o` the `.so` → `cp` over
   `python/pdf_oxide/pdf_oxide.abi3.so` → also `uv pip install --force-reinstall`
   into `artifacts/pdf_lab/security_advisories_live_e2e/venv` (the 3.12 venv
   the corpus predicates use).
2. Always build the wheel with `--features python,rendering,office`; a build
   without `rendering` makes annotation-call tests fail with a misleading
   "Rendering feature not enabled".
3. Debug and release builds take DIFFERENT top-level `extract_text` branches
   for the tagged NIST PDF (see §4). Never conclude a fix works from a debug
   binary alone; verify through the release wheel.

## 3. What Is Working Well (landed this pass, all on origin/main)

| Commit | Fix | Guard |
| --- | --- | --- |
| 1fd6c066 | enumerated list siblings stay item-level (a./1./(a) no longer merge; symbol bullets still merge per #28) | tests/test_nist_page46_nested_list_segmentation.py |
| 4442184f | SymbolMT Type0 ToUnicode→PUA bullets decode via symbol table (0xB7→U+2022, NOT −0xF000) | check_pua_issue21.py + 4 unit tests + Wingdings negative control |
| 40759b93 | run_in_lead field marks bold "Title:" run-in leads (no mid-line split) | detect_run_in_lead unit tests + predicate |
| 385f46e0 | bbox_space stamped in annotation calls + snapshot pages; convention PROVEN pdf_points_bottom_left_xywh vs PyMuPDF oracle | check_bbox_space_issue20.py (falsifiable both directions) |
| 7af7632a | char dedup no longer deletes the glyph after a narrow space (identity now required) — was silent content loss in EVERY document | check_table_cell_truncation_p19.py (glyph-count parity) |
| 6e338162 | intra-line TJ back-jump reading order fixed in BOTH assembler families (total-order sort; per-run x-sort on the untagged path) | check_word_fidelity_sweep.py (8 pages missing → 0) |
| af0680fc | "(N) ALLCAPS \| ALLCAPS" enhancement titles classify as Header lvl 3, stop absorbing body | is_enhancement_title unit tests |
| e2dc53e1 | deterministic reconciler can no longer revert adjudicated ledger entries | guard in reconcile script; replay-restored ledger |

Harness: `scripts/pdf_lab/adjudicate_findings.py` (`packet --page N` /
`apply --decisions f.jsonl`, fail-closed, provenance-stamped).

## 4. What Is Currently Broken / Open

### Live defect families (7 still_broken, cell read-backs quoted in ledger)
1. **Appendix-grid duplicated+truncated name cells** (p457/464/486):
   `'DOMAIN AUTHENTICATION\nDOMAIN AUTHENTICAT'`, and the type glyph merged
   into the number cell (`'AC-4(17)\nT'`). Both external reviewers (see §6)
   independently predict the mechanism: small-caps emulated by TWO coincident
   draw runs; the second run clipped by the cell. First probe: dump per-char
   sequence/font/size/bbox for one such cell.
2. **Withdrawn-row notes split across columns** (p474/481):
   `'W: Incorporated into' | 'CM-10 a\nand SI-7.'` with a stray 'a'.

### Unadjudicated (19)
p30 footnotes-as-body (3), p382/p399 field-split bbox synthesis (9 across
both), p402/p404 shaded headings (4), p413 numeric false-list, p461
caption-table association, p157_unnamed (malformed empty finding — flag to
human rather than adjudicate).

### Architectural debt (from the 2026-08-21 webgpt+webgemini review — run
`ask-tau-review-...-8a3a1776ed9c`, both seats PASS, full responses in the run
dir under node-artifacts/)
- **BLOCKER for pass closure**: the debug/release branch divergence in
  `extract_text` (src/document.rs, structure_content_cache branch near the
  top of extract_text). Method both seats endorsed: instrument the single
  branch-selection decision point, dump every input predicate under both
  profiles, diff. Suspects: cfg(debug_assertions), FP sensitivity in 2pt
  clustering, HashMap iteration order.
- **Nearest-column fallback in src/tables/text_assign.rs is UNPROVEN** — it
  was a wrong hypothesis kept as a guard, has no failing-first test, and can
  force out-of-grid glyphs into confidently wrong cells. Needs its own
  adversarial fixture (bound by max-gap proportional to ruling thickness) or
  removal.
- **`sort_mcid_spans_reading_order` keys a HashMap on span.sequence** —
  uniqueness never verified; duplicates would silently corrupt line
  assignment. One-command check, do it first.
- **Identity-based char dedup hole**: a double-draw with ligature `ﬁ` on one
  layer and decomposed `f`+`i` on the other now SURVIVES as duplicate text.
- **The 111 bulk text-loss closures rest on a ≥4-letter word-SET sweep** —
  blind to multiplicity, order, placement, short tokens. Both reviewers:
  downgrade to "strong corroboration"; upgrade path is per-region token
  Counters (all tokens, counts, coarse x/y bins) BEFORE any further bulk
  adjudication.
- **Ledger design**: adjudications and the reconciler are still competing
  writers to `current_status`, separated only by one `if`. Target design
  (both reviewers, identical): append-only adjudication events;
  `current_status` a derived projection; reconciler writes only
  `deterministic_status`.
- reorder_intra_line_runs / MCID sort are UNTESTED on RTL, bidi, math,
  rotated text — ranked the #1 cross-document regression risk by both seats.

## 5. Next Steps (ordered)

1. **Minutes**: verify `span.sequence` uniqueness per page (assert in the MCID
   sort or prove global uniqueness); write the adversarial fixture for the
   nearest-column fallback or revert it.
2. **Slice A — token-Counter fidelity oracle**: per-region (coarse x/y bin)
   token Counters, all token lengths, counts not sets, pdf_oxide vs PyMuPDF.
   Land BEFORE further bulk adjudication (both reviewers' strongest
   methodology point). Re-run over the 111 bulk closures as confirmation.
3. **Slice B — duplicated small-caps cells** (biggest live defect family):
   per-char dump of one cell to confirm the two-run mechanism, then a
   run-aware/geometric dedup (not adjacent-char). Failing predicate exists in
   the ledger quotes; formalize as a check script first.
4. **Slice C — debug/release divergence root cause** (pass-closure blocker):
   instrument the branch selection, diff predicates across profiles.
5. Withdrawn-row column splits (p474/481), then the 19 unadjudicated via
   `adjudicate_findings.py packet` — footnotes p30 first (3 findings).
6. Ledger redesign to event-sourcing when next touching the census pipeline.
7. Human decisions pending: #15/#17 policy (close as not-planned vs migrate
   ttf-parser→harfrust and drop the tract 'ml' stack); Wingdings encoding
   table (new ticket, out of #21's scope); p157_unnamed malformed finding.

## 6. Project Context Required for Success

Read before continuing:
- `fixtures/agentic_eval.json` — the regression contract; run via
  `/home/graham/.claude/skills/agentic-evals/run.sh run fixtures/agentic_eval.json`.
  Fixture rules learned the hard way: version 2, claims as an object, needs at
  least one negative and one real_world case, commands run with fixtures/ as
  cwd, no per-case env (wrap in `bash -c`), cargo output needs
  `grep 'test result:'` (its tail is blank lines).
- `scripts/pdf_lab/adjudicate_findings.py` and `check_*.py` — the predicates.
- `artifacts/pdf_lab/census_regen_20260820/seed.json` — the ledger.
- Review run (both seats' full POSITION/EVIDENCE/RISKS/ANSWERS/DISSENT):
  `/mnt/storage12tb/skills/ask/outputs/.ask_artifacts/tau-dag-runs/ask-tau-review-the-pdf-oxide-agentic-sec-8a3a1776ed9c/node-artifacts/`
- Corpus paths: NIST 800-53r5 at
  `/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/NIST_SP_800-53r5.pdf`
  (0-based index = printed page − 1 for this copy); 800-53Ar5 and NASA
  handbook under `/mnt/storage12tb/extractor_corpus/`. The NASA ticket
  document is `engineering/12 NASA_SP-2016-6105 Rev 2.pdf` (sha b8e28d12) —
  NOT the inbox/government copy; testing the wrong copy once produced a false
  "fixed" reading.

Operational constraints (unchanged, operator-standing):
- Work only in this primary checkout; never create worktrees. `main` is the
  only branch and must stay directly pushable.
- `/ask fix-issues` is the standard GitHub-issue loop (dry-run first,
  `--workdir` required, close only on a passing verify).
- Every resolved bug gets an agentic-evals fixture — non-negotiable.
- Ask browser lanes: ONE attachment per webgpt/webgemini seat; no absolute
  local paths or `~<digits>` in prompt bundles (preflight fails closed) —
  sanitize, then submit. Stale provider tabs are rebindable with
  `/browser-oracle open-bind`.
- Measurement discipline: a regex character class written with `\uXXXX`
  escapes can silently degrade (observed: it became a literal hyphen and
  counted `-` as PUA, producing false readings in both directions). Use
  explicit ord() ranges and give every checker a negative control.
