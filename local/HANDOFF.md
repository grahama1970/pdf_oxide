# Handoff Report: pdf_oxide

**Timestamp**: 2026-07-21T14:11:32Z
**Active Agent**: codex

## 1. Project Overview

- **Ecosystem**: Rust parser plus Python PDF Lab harness/scripts.
- **Core Purpose**: PDF-spec-compliant extraction, with PDF Lab hardening against NIST SP 800-53r5 page candidates.
- **Immutable Goal**: Harden all PDF Lab page candidates one page/checklist item at a time until every active candidate has deterministic evidence or an explicit blocked receipt.

## 2. Verified Current State

- Main repo URL: `https://github.com/grahama1970/pdf_oxide`
- Remote main verification command: `git ls-remote origin refs/heads/main`
- Verified remote main before page41 Tau-boundary receipt commit: `b801350ac2b703497508bacae5df541ea3f6e9e1`
- Clean integration worktree: `/tmp/pdf_oxide_integrate_gs001_20260721`
- Integration branch: `codex/integrate-gs001-reconciler-20260721`
- Do not continue from the dirty detached checkout at `/home/graham/workspace/experiments/pdf_oxide` unless intentionally reconciling worktrees.

## 3. What Landed On Main

- `44410852` cherry-picks source repair `61050fc5`.
- `042126b1` records the current-main integration proof and updates `GOAL.md` so model/executor work is Tau-owned only.
- `25808d30` rechecks page34 body-lines-as-headings after GS001 integration.
- `d5b14547` rechecks page45 AC-1 list-threshold markers after GS001 integration.
- Repair scope:
  - `src/extractors/table_block_reconciler.rs`
  - `src/extractors/document_extractor.rs`
  - `src/extractors/mod.rs`
  - `src/python.rs`
  - `python/pdf_oxide/pipeline_extract.py`
  - `python/pdf_oxide/pipeline_types.py`

## 4. Proof

- Focused Python regression:
  - Command: `uv run pytest -q tests/test_nist_table_duplicate_suppression.py tests/test_nist_page_28_regression.py`
  - Result: `15 passed in 2.03s`
  - `mocked:false`, `live:false`
- Rust library baseline:
  - Command: `cargo test --lib`
  - Result: `4543 passed, 1 failed, 8 ignored`
  - Only failure: `pipeline::converters::toc_detector::tests::test_extract_simple_toc`
  - This matches the pinned pre-existing TOC failure class, though the observed pass count is `4543`, not Claude's reported `4530`.
- Integration receipt:
  - `artifacts/pdf_lab/gs001_reconciler_main_integration_20260721/integration_receipt.json`
- Post-GS001 page34/page45/DOI checks:
  - `uv run pytest -q tests/test_nist_page45_chrome_noise.py tests/test_nist_page45_ac1_list_markers.py tests/test_nist_page34_sidebar_chrome.py tests/test_nist_page_28_regression.py`
  - Result: `11 passed in 7.13s`
  - `artifacts/pdf_lab/page34_recheck_after_gs001_20260721/audit_summary.json`
  - `artifacts/pdf_lab/page45_list_threshold_recheck_after_gs001_20260721/audit_summary.json`
  - `artifacts/pdf_lab/doi_chrome_census_20260721/audit_summary.json`
- Full DOI/publication side-chrome census:
  - Command: `uv run python scripts/pdf_lab/build_pdf_element_candidate_manifest.py --pdf /mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf --out artifacts/pdf_lab/doi_chrome_census_20260721/candidate_manifest_full_plain_uv_after_dev_pymupdf.json --ledger python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json --apply-mode release --page-timeout-s 30 --debug-log artifacts/pdf_lab/doi_chrome_census_20260721/candidate_manifest_full_plain_uv_after_dev_pymupdf_debug.log --progress-path artifacts/pdf_lab/doi_chrome_census_20260721/candidate_census_progress_full_plain_uv_after_dev_pymupdf.json`
  - Result: `candidate_count=9333`, `page_count=492`, `extracted_page_count=492`, `census_failure_count=0`
  - DOI/publication anchor analysis: `576` anchor records on `492` pages, `0` non-side-chrome records, `0` combined rotated DOI records outside the left-margin bbox contract.
- Page41 current-candidate prep:
  - Selection artifacts:
    - `artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/stale_selection_rejection.json`
    - `artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/candidate_manifest_unreviewed_pages.json`
    - `artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/sampled_page_cases_unreviewed.json`
  - Selection result: initial page 15 sample rejected as stale because `GOAL.md` already records page15 reviewed-clean; filtered manifest has `453` pages and `8897` candidates; selected page 41 with `13` candidates.
  - Prep command:
    - `uv run python scripts/pdf_lab/run_page_second_pass_dag.py --pdf /mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf --manifest artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/candidate_manifest_unreviewed_pages.json --sampled-cases artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/sampled_page_cases_unreviewed.json --out artifacts/pdf_lab/live_second_pass_page41_tau_prep_20260721 --page 41 --ledger python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json --apply-mode release --review-mode dry_run --scillm-preflight-mode dry_run --patch-mode dry_run --patch-backend scillm_orchestrator --page-orchestrator-mode dry_run --caller-skill pdf-lab --model vlm-free2 --page-extract-timeout-s 30`
  - Prep result: `case_dir=artifacts/pdf_lab/live_second_pass_page41_tau_prep_20260721/page_case_0001_p0041`, `terminal_status=still_open` because the model review was deliberately not executed from pdf_oxide.
  - Deterministic validation:
    - `review_request_validation.ok=true`, `image_part_count=2`, `text_part_count=1`, request SHA `0d5241ec3e224f72291138120f888832baf054a7dc4e4564183cc6f0725eff43`
    - `review_bundle_validation.ok=true`
    - `scillm_orchestrator_page_submission_validation.ok=true`, DAG spec SHA `d555159d2a6dcc10c20a6eef494aa312fa48291cbdb133f6bd2e07c272fbfa23`

## 5. Current Blocker

- Page41 is explicitly blocked at the Tau boundary, not by a new pdf_oxide extraction failure.
- Block receipt:
  - `artifacts/pdf_lab/page41_tau_boundary_blocked_20260721/audit_summary.json`
- Tau issue:
  - `https://github.com/grahama1970/tau/issues/122`
  - Title: `Add Tau-owned generic SciLLM chat/VLM review executor for PDF Lab page cases`
- Boundary:
  - `pdf_oxide` produced valid page evidence and Tau contract inputs.
  - No discovered Tau-owned generic chat/VLM executor consumes `review_request.json` and emits `review_response.json` plus provider-live receipt.
  - Do not bypass this by calling SciLLM/OpenCode directly from `pdf_oxide`.
- External unblock attempts:
  - Brave Search artifact: `artifacts/pdf_lab/live_second_pass_page41_tau_prep_20260721/webgpt_unblock_brave_search.json`
  - WebGPT browser-oracle doctor artifact: `artifacts/pdf_lab/page41_tau_boundary_blocked_20260721/webgpt_browser_oracle_doctor.json`; readiness `needs_attention`, issue `tab_stale_manual_binding`, tab `837359458`
  - Ask/Tau WebClaude run dir: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_page41_tau_boundary/pdf-lab-page41-tau-boundary-webclaude-20260721`
  - Ask/Tau DAG receipt: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_page41_tau_boundary/pdf-lab-page41-tau-boundary-webclaude-20260721/tau-receipts/dag-receipt.json`; status `BLOCKED`, verdict `COMMAND_FAILED`, `mocked=false`, `live=true`, `provider_live=false`
  - WebClaude node receipt: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_page41_tau_boundary/pdf-lab-page41-tau-boundary-webclaude-20260721/node-artifacts/handler-webclaude/node-receipt.json`; timeout waiting for sentinel `<<<CLAUDE_DONE:20260721T140332Z:a9b9f1df>>>`, `raw_chars=50093`, `clean_chars=0`, `raw_contains_sentinel=false`

## 6. Campaign Status

| Field | Value |
|-------|-------|
| `passed` | `0` for the active page41 live-review item |
| `failed` | `0` |
| `blocked_by_systemic_failure` | `0` |
| `explicitly_blocked` | `1` |
| `not_run` | `452` unreviewed pages remaining in the filtered current manifest |
| Active page/checklist item | `page_0041` live second-pass model-backed page review through Tau |
| Latest failure signature | No Tau-owned generic SciLLM chat/VLM review executor found; WebGPT tab stale; Ask/Tau WebClaude sentinel timeout |

## 7. Important Correction To Claude Report

- The claimed commits `61050fc5`, `897ca8b6`, `a41ea818`, and `8bff1849` were not on `origin/main` when checked; `origin/main` was `1becfd79`.
- The code repair `61050fc5` applied cleanly to current main and is now on main as `44410852`.
- The old evidence commits did not cleanly cherry-pick:
  - `897ca8b6` conflicted on an old `artifacts/pdf-lab` directory rename split.
  - It also conflicted because `scripts/pdf_lab/cross_page_regression.py` and `scripts/pdf_lab/promotion_gate.py` are deleted on current main.
- I did not resurrect deleted scripts or force old evidence commits onto current main.

## 8. Boundaries

- `pdf_oxide` project agents must not call SciLLM or OpenCode directly.
- Tau owns model transport, OpenCode/SciLLM execution, DAG orchestration, retries, merge semantics, and receipts.
- `pdf_oxide` may prepare deterministic page evidence and Tau DAG contract inputs.
- Tau issues remain the model-route boundary:
  - `https://github.com/grahama1970/tau/issues/120`
  - `https://github.com/grahama1970/tau/issues/122`

## 9. Remaining Candidate Classes

Use the same one-candidate proof ladder, without direct SciLLM calls from `pdf_oxide`:

1. Resume page41 only after Tau issue `#122` is resolved or another Tau-owned generic chat/VLM executor receipt is available.
2. Any candidate whose model/executor review is required must go through Tau DAG contracts, not direct SciLLM/OpenCode calls from this repo.
3. Criterion 6 live GitHub apply remains blocked until a valid approval receipt for mutation exists.

Before patching the next item, rerun page41 live review through the Tau-owned executor and require `validation_result.status != blocked`, then decide whether the page41 candidates are clean, need a focused regression, or need a page-local patch.
