# GS001 Handoff — for the local project agent

Updated: 2026-07-18. Branch: `claude/pdf-oxide-project-state-nl6oef`
(pdf_oxide, agent-skills, tau — all three carry this branch).

## Pull this work (do this first)

All three repos, same branch name. From your existing checkouts:

```bash
# 1. pdf_oxide — the primary repo (engine, goal, contract, runner, fixer, UI)
cd ~/workspace/experiments/pdf_oxide
git fetch origin claude/pdf-oxide-project-state-nl6oef
git checkout claude/pdf-oxide-project-state-nl6oef
# NOTE: this branch already CONTAINS the GS001 R3-R11 branch
# (pdf-oxide/gs001-r3-runner-tests) via fast-forward — do not re-merge it.

# 2. agent-skills — pdf-lab skill hardening (comparator, backlog, auth, referee)
cd ~/workspace/experiments/agent-skills
git fetch origin claude/pdf-oxide-project-state-nl6oef
git checkout claude/pdf-oxide-project-state-nl6oef

# 3. tau — the gs001-closure-audit packaged workflow
cd ~/workspace/experiments/tau
git fetch origin claude/pdf-oxide-project-state-nl6oef
git checkout claude/pdf-oxide-project-state-nl6oef
uv run pytest tests/test_gs001_closure_audit_workflow.py
```

**Workstation session 2026-07-18 executed all of the above.** Results and
corrections to this section:

- All three repos are checked out as sibling worktrees
  (`pdf_oxide-gs001`, `agent-skills-gs001`, `tau-gs001`) so the primary
  pdf_oxide tree — which carries ~458 uncommitted entries — stays untouched.
- **tau remotes were misnamed and are now fixed.** `origin` pointed at
  `alejandro-ao/tau` (the upstream this fork will never merge back into),
  so `git fetch origin` above silently fetched the wrong repository. As of
  2026-07-18: `origin` = `grahama1970/tau`, `upstream` = `alejandro-ao/tau`.
  The command as written now works.
- Test gates all green: Rust `block_classifier` 44/44 · GS001 Python 18/18 ·
  pdf-lab 47/47 · tau 3/3. Two beat this doc's predictions — tau's suite
  runs fine on local Python 3.14, and pdf-lab reports 47 passed / 0 skipped
  (not 38/9), since the NIST extraction artifact is already discoverable.

What changed where (key files):

- **pdf_oxide** (base `cd4a6e4` + GS001 merge + 5 commits): `GOAL.md`,
  `golden_slices/gs001_nist_page28/{expected_elements_v3.draft.json,contract_decisions.json}`,
  `scripts/gs001_lock.py`,
  `scripts/pdf_lab/{terminal_ledger.py,run_page_second_pass_dag.py,repair_transaction.py}`,
  `.tau/gs001-execution-dag.json`, `ui/` (whole app, moved from
  agent-skills), `docs/HANDOFF_GS001.md`,
  `tests/{test_gs001_lock,test_terminal_ledger,test_repair_transaction}.py`,
  compile fixes in `src/extractors/{block_merger,figure_detector,section_hierarchy}.rs`.
- **agent-skills** (8 commits under `skills/pdf-lab/`):
  `lib/agentic_parts/part1-6.py` (fail-closed auth, comparator v2,
  backlog wiring, restored `TABLE_CLASS_*` constants),
  `lib/{discrepancy,regression,coverage_loop}.py`, `cli_parts/part2.py`
  (`regression-check`), `tests/` (5 new files), `ui/` reduced to a thin
  launcher (`README.md`, `run.sh`) — the app itself now lives in pdf_oxide.
- **tau** (1 commit): `src/tau_coding/workflows/{definitions,templates}/gs001-closure-audit.json`,
  `workflows/nodes/gs001_closure_audit.py`, materializer/runner/CLI
  registration in `workflows/{materialize,runner}.py` + `cli.py`,
  `tests/test_gs001_closure_audit_workflow.py`.

Environment setup after pulling: `cd pdf_oxide/ui && npm install` for the
viewer; a Python venv with `pdf_oxide pillow python-dotenv httpx pytest
typer loguru pyyaml reportlab` covers the pdf-lab and pdf_oxide Python
test suites (`pytest skills/pdf-lab/tests` should report 38 passed and,
once the NIST extraction artifact path is set via
`PDF_LAB_NIST_EXTRACTION`, 47 passed / 0 skipped).

## What this repo now contains (self-contained)

| Piece | Where | Status |
|---|---|---|
| Immutable goal charter | `GOAL.md` | Draft; pins PENDING (see Unblock list in GOAL.md) |
| Expected contract (11 rows) | `golden_slices/gs001_nist_page28/` | Rows 10-11 decided+specified; rows 1-9 pending bundle recovery |
| Contract/goal lock tool | `scripts/gs001_lock.py` | Working; fail-closed; tested |
| Page loop runner | `scripts/pdf_lab/run_page_second_pass_dag.py` | Manifest-driven; offline-deterministic path works |
| Terminal ledger (#77 contract) | `scripts/pdf_lab/terminal_ledger.py` | Tested incl. deterministic replay |
| Repair transaction (fixer leg) | `scripts/pdf_lab/repair_transaction.py` | Worktree + allowlist + REAL rollback; 6 tests |
| Execution DAG contract | `.tau/gs001-execution-dag.json` | Schema-valid vs tau.dag_contract.v1 |
| Transparency UI | `ui/` (`#pdf-lab/loop`) | Typecheck + build green; repo-relative artifact defaults |
| GS001 regression gates | `tests/test_nist_page_28_regression.py`, `cargo test block_classifier` | Rust 44/44; Python gate needs source PDF |

Companion pieces on the same branch elsewhere:
- **agent-skills `skills/pdf-lab`**: fail-closed scillm auth + preflight, strict
  comparator (`pdf-lab.comparison.v2`), `lib/discrepancy.py`
  (defect_key/observation_id), backlog emitter, `regression-check` CLI.
  38 tests pass, 9 skip awaiting the NIST extraction artifact.
- **tau**: `tau workflows run gs001-closure-audit` — deterministic judge of
  the artifacts above (validate → verdict → dry-run tickets → report).

## Immediate next steps (in order)

1. **Recover the expected contract rows 1-9.** ⚠️ **The named source is
   GONE.** `/tmp/pdf-lab-golden-slices/nist_page_28_printed_page_1/` no
   longer exists — /tmp was cleared, and a filesystem search on 2026-07-18
   found no `expected_elements_v2*.json` anywhere on the workstation. The
   human labels are lost; **relabeling is now the only lawful path.**

   Do NOT substitute
   `/tmp/embry-interrupt-codex/packages/ux-lab/public/pdf-lab-projects/nist-phase54-toc-backed/pages/page_0028/expected_elements.json`.
   It is the only surviving page-28 expected-row file and is therefore
   tempting, but its own `source` field reads "phase-54 TOC-backed
   candidate packet release_extraction_blocks.json" — it is derived from
   extractor output, which the no-inference rule disqualifies. Its
   `page.png` / `bbox_overlay.png` / `annotation_prompt.md` ARE usable as
   the rendering scaffold for a fresh human relabel.

   LESSON (policy, not just this row set): ground truth must be committed
   under `golden_slices/`, never left in scratch space. The source PDF was
   moved in-repo on 2026-07-18 for exactly this reason.

   Fill the relabeled rows into
   `golden_slices/gs001_nist_page28/expected_elements_v3.draft.json`,
   resolve the two `bbox_status: PENDING` fields on rows 10-11, set
   `contract_status: "locked"`, then:
   `python3 scripts/gs001_lock.py lock-contract --contract golden_slices/gs001_nist_page28/expected_elements_v3.draft.json`
2. ~~**Pin the source PDF**~~ — **DONE 2026-07-18** (commit `e4ee603e`).
   Committed at `golden_slices/gs001_nist_page28/source/NIST_SP_800-53r5.pdf`,
   sha256 `fc63bcd61715d0181dd8e85998b1e6201ae3515fc6626102101cab1841e11ec6`,
   recorded in both the contract and GOAL.md. The hash was verified against
   a fresh download from nvlpubs.nist.gov — not merely against the four
   local copies under `artifacts/` — so it pins the authentic publication.
   (Note: `tests/fixtures/real/*.pdf` is gitignored at `.gitignore:137`;
   that is why the PDF lives under `golden_slices/`.)

   Remaining pins: 6. `lock-goal` still fails closed, exit code 1.
   The three `*_baseline_commit` pins are deliberately NOT filled with the
   current worktree HEADs — the unblock list requires the fork-vs-upstream
   0.3.74 baseline audit first, and writing HEADs there would satisfy the
   string check while faking the audit the fail-closed design exists to force.
3. **Resolve remaining GOAL.md pins** (baseline commits, frozen packet),
   then `python3 scripts/gs001_lock.py lock-goal --goal GOAL.md` and copy
   the emitted `goal_hash` into `.tau/gs001-execution-dag.json`.
4. **Run the loop once, review-only**: write a
   `pdf_lab.runtime_manifest.v1` (see `run_page_second_pass_dag.py`
   docstring), `output_root` under `artifacts/pdf-lab/loop-runs/<run-id>`,
   run pages, then watch it in the UI (`cd ui && npm i && npm run dev` +
   server; open `#pdf-lab/loop`).
5. **Audit the artifacts with tau**:
   `tau workflows run gs001-closure-audit --run-dir <dir> --comparison-json … --backlog-json … --triage-queue-json … --expected-contract-json … --goal-md GOAL.md`
   Expect NOT_CLOSED with typed blockers until repairs land.
6. **First bounded repair**: author a patch for the top backlog entry,
   drive it through `repair_transaction.run_repair_transaction` with
   `verify_command = ["pdf-lab", "regression-check", "--baseline", …,
   "--candidate", …, "--target-class", <kind>]`; promotion of the
   verified attempt branch is the human/apply-gate's call.
7. **Command specs**: the `.tau` execution DAG references coder/reviewer
   agents; author per-node `tau` command specs (see
   `tau/experiments/goal-locked-subagents/agent-command-specs/` for the
   convention) wrapping the commands above.

## Hard rules carried over (do not relax)

- Never infer expected rows from extractor output.
- No exact-source-text or page-number rules in classifiers/presets
  (issue #76); anti-overfit inspection before promotion.
- `regression-check` PASS (targeted class strictly decreased, nothing
  worsened) is the only promotion evidence; model prose never closes.
- `PATCH_DELEGATE_BLOCKED reason=X` stays typed still-open (#77).
- Kill criterion in GOAL.md: two consecutive memorization-only repairs
  falsify the loop design — stop and redesign.

## Known environment gaps (from the cloud session)

- tau's pytest suite unexecuted there (needs Python 3.14); run
  `uv run pytest tests/test_gs001_closure_audit_workflow.py` locally.
- Live scillm second pass untested (no endpoint in the container);
  the fail-closed preflight covers the absent case.
- The 4 pre-existing failing Rust tests (toc_detector + 3 lattice) are
  the frozen known-failing baseline — do not let them grow.
