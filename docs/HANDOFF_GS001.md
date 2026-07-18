# GS001 Handoff — for the local project agent

Updated: 2026-07-18. Branch: `claude/pdf-oxide-project-state-nl6oef`
(pdf_oxide, agent-skills, tau — all three carry this branch).

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

1. **Recover the expected contract rows 1-9.** Source: the original
   human-labeled bundle at
   `/tmp/pdf-lab-golden-slices/nist_page_28_printed_page_1/` on the
   operator workstation (`expected_elements_v2.json` + labeled PNG), or
   relabel against the rendered page. Fill them into
   `golden_slices/gs001_nist_page28/expected_elements_v3.draft.json`,
   resolve the two `bbox_status: PENDING` fields on rows 10-11, set
   `contract_status: "locked"`, then:
   `python3 scripts/gs001_lock.py lock-contract --contract golden_slices/gs001_nist_page28/expected_elements_v3.draft.json`
2. **Pin the source PDF**: place `NIST_SP_800-53r5.pdf`, record its
   sha256 in the contract's `source_pdf.sha256` and GOAL.md pins.
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
