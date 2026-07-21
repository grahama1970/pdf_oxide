# Handoff Report: pdf_oxide

**Timestamp**: 2026-07-21T13:50:00Z
**Active Agent**: codex

## 1. Project Overview

- **Ecosystem**: Rust parser plus Python PDF Lab harness/scripts.
- **Core Purpose**: PDF-spec-compliant extraction, with PDF Lab hardening against NIST SP 800-53r5 page candidates.
- **Immutable Goal**: Harden all PDF Lab page candidates one page/checklist item at a time until every active candidate has deterministic evidence or an explicit blocked receipt.

## 2. Verified Current State

- Main repo URL: `https://github.com/grahama1970/pdf_oxide`
- Remote main verification command: `git ls-remote origin refs/heads/main`
- Verified remote main: `042126b10fff3de1832ed64b4236443018148b04`
- Clean integration worktree: `/tmp/pdf_oxide_integrate_gs001_20260721`
- Integration branch: `codex/integrate-gs001-reconciler-20260721`
- Do not continue from the dirty detached checkout at `/home/graham/workspace/experiments/pdf_oxide` unless intentionally reconciling worktrees.

## 3. What Landed On Main

- `44410852` cherry-picks source repair `61050fc5`.
- `042126b1` records the current-main integration proof and updates `GOAL.md` so model/executor work is Tau-owned only.
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

1. Page 34 body-lines-as-headings.
2. Page 45 list threshold.
3. DOI-chrome leftovers.

Before patching the next item, produce a selection receipt with source page image/current extraction/model-review artifacts and the focused regression that will prove that one checklist item.
