# Handoff Report: pdf_oxide

**Timestamp**: 2026-07-21T12:31:00Z
**Active Agent**: codex

## 1. Project Overview

- **Ecosystem**: Rust parser plus Python PDF Lab harness/scripts.
- **Core Purpose**: PDF-spec-compliant extraction, with PDF Lab hardening against NIST SP 800-53r5 page candidates.
- **Immutable Goal**: Harden all PDF Lab page candidates one page/checklist item at a time until every active candidate has deterministic evidence or an explicit blocked receipt.

## 2. Current State

- Current page: page29 selected after page28 was recorded as blocked at the Tau/SciLLM orchestration boundary.
- Current clean worktree: `/tmp/pdf_oxide_next_page_20260721`, branch `codex/pdf-lab-next-page-20260721`.
- Current remote main at start of this slice: `1becfd792225911c0c681b2c6638a8dfb698f356`.
- Do not continue from the dirty detached checkout at `/home/graham/workspace/experiments/pdf_oxide` unless intentionally reconciling worktrees.

## 3. Proven Local Work

- Page28 footer/source-type repair remains local and narrow:
  - Code: `scripts/pdf_lab/snapshot_current_extraction.py`
  - Test: `tests/test_pdf_lab_snapshot_current_extraction.py`
  - Proof command: `pytest -q tests/test_pdf_lab_snapshot_current_extraction.py -k page28_footer_source_type`
  - Result: `1 passed, 15 deselected`
  - Deterministic artifact: `artifacts/pdf_lab/page28_footer_source_type_20260721/audit_summary.json`, `ok:true`
- Page28 generic `text` preset prompt-contract repair remains local and narrow:
  - Code: `scripts/pdf_lab/run_page_second_pass_dag.py`
  - Test: `tests/test_pdf_lab_page_second_pass_dag.py`
  - Proof command: `pytest -q tests/test_pdf_lab_page_second_pass_dag.py -k "text_preset_semantic_subtype_contract"`
  - Result: `1 passed, 216 deselected`

## 4. What Is Broken / Blocked

- The page28 live VLM second-pass gate is not passed.
- Latest authenticated live run:
  - Output dir: `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z`
  - `harness_final_gate.json`: `ok:false`, `terminal_status:"failed_closed"`
  - `page_cases/page_case_0001_p0028/scillm_review_error.json`: `error_type:"ReadTimeout"`, endpoint `POST /v1/chat/completions`
  - `terminal_ledger.json`: `terminal_status:"blocked_substrate"`, reason `scillm_review_call_failed`
- The previous unauthenticated retry failed because the shell lacked the active SciLLM token and fell back to a stale redacted fallback token.
  - Loading `/home/graham/workspace/experiments/scillm/.env` made `/v1/scillm/auth` succeed and transport registration succeed.
- Important boundary: do not implement new model-call chunking, DAG, retry, or transport orchestration inside pdf_oxide. The human restated that Tau owns DAG/agentic harness work and that `$tau` controls SciLLM internally.
- Page29 selection evidence exists, but page29 live second-pass is blocked before any valid model verdict:
  - Selection receipt: `artifacts/pdf_lab/next_candidate_selection_page29_20260721T1220Z/selection_receipt.json`
  - Selected case: `page_case_0001_p0029`
  - Candidate count: `13`
  - Candidate strata: `footnote`, `section_heading`, `side_chrome`, `text`, boundary geometry, high risk.
- A later page29 Tau-dispatched local wrapper attempt must be treated as invalid progress evidence:
  - Boundary receipt: `artifacts/pdf_lab/page29_tau_boundary_violation_20260721T1230Z/receipt.json`
  - Tau DAG receipt: `artifacts/pdf_lab/live_second_pass_page29_tau_dispatched_20260721T1230Z/tau/run/dag-receipt.json`, `status:"PASS"`, `mocked:false`, `live:true`.
  - PDF Lab child receipt: `artifacts/pdf_lab/live_second_pass_page29_tau_dispatched_20260721T1230Z/tau/run/command-loop/command-artifacts/command-loop-step-001/harness-command-receipt.json`, `status:"BLOCKED"`.
  - Child validation: `review_validation.json`, `ok:false`, errors `["page_orchestrator_registration_failed"]`.
  - Root error: `scillm_page_orchestrator_run_error.json`, `HTTPStatusError`, HTTP 404 for `POST /v1/scillm/opencode/transport/runs`.
  - Why invalid: although Tau dispatched the local command, the wrapper invoked the pdf_oxide harness path that called SciLLM HTTP transport. The human explicitly disallowed this; Tau must own SciLLM/DAG routing itself.

## 5. Drift Rollback

- A chunked-review implementation was started in `scripts/pdf_lab/run_page_second_pass_dag.py` and `tests/test_pdf_lab_page_second_pass_dag.py`.
- It was rolled back after the human called out drift.
- Confirmation command:
  - `rg -n "chunked_review|chunk_review|review_chunk|LIVE_REVIEW_CANDIDATE_CHUNK_SIZE" scripts/pdf_lab/run_page_second_pass_dag.py tests/test_pdf_lab_page_second_pass_dag.py || true`
  - Result: no matches.

## 6. Unblock Attempts Already Used

- `$brave-search`:
  - `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z/unblock/brave_vlm_timeout_context.json`
- `$ask`/Tau/WebGPT:
  - `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z/unblock/ask_webgpt_timeout/ask-tau-webgpt-review-bundle-pdf-oxide-p-b2aec86c58fd/node-artifacts/handler-webgpt/response.md`
  - WebGPT recommended splitting large page review calls, but that recommendation conflicts with the project boundary unless Tau owns the implementation.
- `$ask`/Tau/WebGPT+WebClaude approach review:
  - `artifacts/pdf_lab/page28_tau_boundary_review_20260721/ask_webgpt_webclaude/ask-tau-pdf-oxide-page28-tau-boundary-re-4091ef18dd69/tau-receipts/dag-receipt.json`
  - `handler-webgpt/response.md` recommended exactly one direct retry before a Tau ticket.
  - `handler-webclaude/response.md` recommended filing a Tau ticket immediately.
  - Reconciled decision: file Tau ticket now, because the human explicitly stated Tau owns DAG/model orchestration and the agent had already drifted by starting a pdf_oxide-side chunking implementation.
- Tau ticket:
  - `https://github.com/grahama1970/tau/issues/120`
  - Title: `Own PDF Lab page28 large live-review timeout orchestration`
- Memory-first hook:
  - `/home/graham/.codex/hook-logs/memory-first-20260721T114729Z.json`

## 7. Next Steps

1. Stop all pdf_oxide-side live SciLLM/model transport attempts. Do not call `/v1/chat/completions` or `/v1/scillm/opencode/transport/*` from pdf_oxide wrappers or harness retries.
2. Treat page29 as selected but blocked on Tau-native model transport. Deterministic non-model extraction/artifact preparation may continue only if it does not invoke SciLLM.
3. Tau-owned ticket for page28 live review timeout has been filed:
   - Issue: `https://github.com/grahama1970/tau/issues/120`
   - Problem: pdf_oxide existing harness directly posts a 39k-char, 18-candidate, 2-image review payload to SciLLM and timed out at 120s.
   - Required owner: Tau should provide or own the DAG/model-review transport strategy, including chunking/retries/merge semantics if that is the chosen route.
   - Acceptance: one page28 live gate produces `review_response.json`, `review_validation.json` with all 18 expected IDs seen exactly once, terminal ledger `reviewed_clean` or a valid non-clean receipt, and `harness_final_gate.json` not blocked.
4. File or update a Tau issue for the page29/route-shape blocker if issue #120 does not already cover the missing Tau-native PDF Lab second-pass transport contract.
5. Do not claim a Tau creator-reviewer loop unless Tau receipts contain creator/reviewer topology and node artifacts. The page28 artifact was a WebGPT/WebClaude roundtable review, not a creator-reviewer repair loop.
6. Do not commit the page29 wrapper as an accepted workflow. If committing this handoff, stage only `local/HANDOFF.md`, `artifacts/pdf_lab/page29_tau_boundary_violation_20260721T1230Z/receipt.json`, and non-model page29 selection evidence.

## 8. Key Files

- `GOAL.md`
- `scripts/pdf_lab/snapshot_current_extraction.py`
- `scripts/pdf_lab/run_page_second_pass_dag.py`
- `scripts/pdf_lab/run_second_pass_harness.py`
- `tests/test_pdf_lab_snapshot_current_extraction.py`
- `tests/test_pdf_lab_page_second_pass_dag.py`
- `artifacts/pdf_lab/page28_footer_source_type_20260721/audit_summary.json`
- `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z/page_cases/page_case_0001_p0028/scillm_review_error.json`
- `artifacts/pdf_lab/next_candidate_selection_page29_20260721T1220Z/selection_receipt.json`
- `artifacts/pdf_lab/page29_tau_boundary_violation_20260721T1230Z/receipt.json`
