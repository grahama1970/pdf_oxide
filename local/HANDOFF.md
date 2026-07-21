# Handoff Report: pdf_oxide

**Timestamp**: 2026-07-21T13:55:00Z
**Active Agent**: codex

## 1. Project Overview

- **Ecosystem**: Rust parser plus Python PDF Lab harness/scripts.
- **Core Purpose**: PDF-spec-compliant extraction, with PDF Lab hardening against NIST SP 800-53r5 page candidates.
- **Immutable Goal**: Harden all PDF Lab page candidates one page/checklist item at a time until every active candidate has deterministic evidence or an explicit blocked receipt.

## 2. Verified Current State

- Main repo URL: `https://github.com/grahama1970/pdf_oxide`
- Remote main verification command: `git ls-remote origin refs/heads/main`
- Verified remote main before DOI-chrome census commit: `d5b1454728fc011d02078ab982f3d884f64a99ac`
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

## 5. Important Correction To Claude Report

- The claimed commits `61050fc5`, `897ca8b6`, `a41ea818`, and `8bff1849` were not on `origin/main` when checked; `origin/main` was `1becfd79`.
- The code repair `61050fc5` applied cleanly to current main and is now on main as `44410852`.
- The old evidence commits did not cleanly cherry-pick:
  - `897ca8b6` conflicted on an old `artifacts/pdf-lab` directory rename split.
  - It also conflicted because `scripts/pdf_lab/cross_page_regression.py` and `scripts/pdf_lab/promotion_gate.py` are deleted on current main.
- I did not resurrect deleted scripts or force old evidence commits onto current main.

## 6. Boundaries

- `pdf_oxide` project agents must not call SciLLM or OpenCode directly.
- Tau owns model transport, OpenCode/SciLLM execution, DAG orchestration, retries, merge semantics, and receipts.
- `pdf_oxide` may prepare deterministic page evidence and Tau DAG contract inputs.
- Tau issue remains the model-route boundary:
  - `https://github.com/grahama1970/tau/issues/120`

## 7. Remaining Candidate Classes

Use the same one-candidate proof ladder, without direct SciLLM calls from `pdf_oxide`:

1. Fresh reviewer-selected current-extraction candidate.
2. Any candidate whose model/executor review is required must go through Tau DAG contracts, not direct SciLLM/OpenCode calls from this repo.
3. Criterion 6 live GitHub apply remains blocked until a valid approval receipt for mutation exists.

Before patching the next item, produce a selection receipt with source page image/current extraction/model-review artifacts and the focused regression that will prove that one checklist item.
