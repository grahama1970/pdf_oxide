# Handoff Report: pdf_oxide

**Timestamp**: 2026-07-30T07:43:19-04:00  
**Active Agent**: Codex  
**Repository**: `/home/graham/workspace/experiments/pdf_oxide`  
**Current Branch At Handoff**: `codex/issue-22-live-figure-content-20260728`  
**Remote Target Requested**: `origin/main` (`git@github.com:grahama1970/pdf_oxide.git`)  
**Immutable Goal**: NOT_MET

## 1. Project Overview

`pdf_oxide` is a Rust-first PDF parser/extractor with Python bindings, CLI,
WASM support, and PDF Lab evidence tooling. The project documentation describes
it as a fast PDF toolkit for text extraction, image extraction, markdown
conversion, creation, and editing. Project instructions also establish
`docs/spec/pdf.md` as the authoritative PDF 1.7 reference for extraction
semantics.

The active PDF Lab objective is not a single page fix. `GOAL.md` defines the
current immutable objective as all-candidates hardening: select one active page
candidate/checklist item at a time, preserve visual and extraction evidence,
create or update a focused regression before patching, prove the slice with
deterministic artifacts, commit and push task-relevant code/artifacts, then
advance only when the current candidate is proven or explicitly blocked.

## 2. Current State: Documentation vs Code Alignment

The codebase contains both the mature extraction implementation and the newer
PDF Lab creator-reviewer evidence path. Recent work has focused on NIST SP
800-53r5 extraction hardening, especially current evidence materialization,
creator-reviewer defect schemas, preset repairs, semantic role labels, and
focused regressions.

Doc/code alignment is partially good:

- `pyproject.toml` now requires `pymupdf>=1.24.11` as a normal dependency.
- `scripts/pdf_lab/snapshot_current_extraction.py` fails loudly when PyMuPDF is
  unavailable.
- The current creator-reviewer evidence path has executable schema validation
  and focused tests.
- PDF Lab proof receipts exist for the latest narrow hardening slices.

The major alignment gap is process architecture, not basic PDF parsing. The
review loop still needs to keep producing machine-executable defect objects
instead of drifting into English observations, broad review runs, dashboard
work, or GitHub issue churn that does not immediately create a failing local
check.

The active branch and `origin/main` are divergent. `origin/main` is at
`a0ae2e4dc9698eafb0880ac551bcc265ac955e15`, while this worktree is at
`bb03ba5f9b4c2fc9765aa6e9d64ce2c88a7f86aa`. Do not force-push or broad-reset.

## 3. What Is Working Well

- Focused creator-reviewer hardening slices have recent local proof receipts.
- The latest page100 focused proof receipt exists at
  `artifacts/pdf_lab/creator_reviewer_page100_field_paragraph_semantic_roles_20260729T2205Z/receipt.json`.
- Recent receipts also exist for:
  - `artifacts/pdf_lab/creator_reviewer_page23_table_hyphen_wrap_spacing_20260729T2145Z/receipt.json`
  - `artifacts/pdf_lab/creator_reviewer_page20_table_hyphen_wrap_spacing_20260729T2135Z/receipt.json`
  - `artifacts/pdf_lab/creator_reviewer_page382_field_split_child_bbox_20260729T2125Z/receipt.json`
  - `artifacts/pdf_lab/creator_reviewer_page399_field_split_child_bbox_20260729T2115Z/receipt.json`
- Fresh reconciliation evidence exists at
  `artifacts/pdf_lab/current_candidate_reconciliation_after_page23_20260729T2200Z/deterministic_reconciliation_report.json`.
- That reconciliation reported `resolved_by_current_extraction: 47` and
  `unverified: 299`, so the queue is active and not closed.
- PyMuPDF requirement and fail-loud behavior are present in project dependency
  metadata and snapshot extraction code.

Recent hardening commits on the active branch:

- `bb03ba5f` Label page100 field paragraphs semantically
- `c1e6a1b5` Prove page23 table hyphen wrap spacing
- `c8592591` Fix page20 table hyphen wrap spacing
- `07d393ee` Prove page382 field split child bboxes
- `60547491` Align page399 field split child bboxes
- `eddb8780` Harden page157 false table suppression
- `9f44c6d5` Prove page186 list hyphen wrap spacing
- `e3217f6b` Repair page235 body hyphen wrap spacing

## 4. What Is Currently Broken Or Pending

The immutable all-candidates hardening goal is still **not met**. A sequence of
successful slices is not completion of the goal.

Known pending or broken areas:

- The latest deterministic reconciliation still lists 299 unverified historical
  findings.
- Historical queue entries are stale unless re-materialized against current
  extraction. Do not pick a stale text-only finding without fresh local
  evidence.
- `$ask` competition/provider work was unreliable in prior attempts and should
  not be used as proof for PDF extraction correctness.
- A real GitHub issue was filed for the agent-skills PDF Lab verifier mismatch:
  `https://github.com/grahama1970/agent-skills/issues/1118`.
- Full-repository tests were not run for this handoff-only step.
- The current worktree has many unrelated modified and untracked files. Treat
  them as user/other-agent work; do not clean, reset, stash, or stage broadly.

No current focused PDF extraction regression failure is selected in this
handoff. The next agent should select one from fresh current evidence.

## 5. Recommended Next Steps

1. Resume the immutable PDF Lab hardening queue from fresh evidence, not from
   old handoff prose or stale issue summaries.
2. Start with
   `artifacts/pdf_lab/current_candidate_reconciliation_after_page23_20260729T2200Z/deterministic_reconciliation_report.json`
   and its `current_evidence` directory.
3. Select exactly one current unresolved candidate and write down:
   page id, block id or region id, actual label/bbox, expected label/bbox,
   defect class, evidence paths, and why it is the next useful target.
4. Create or update the executable defect fixture before patching. The defect
   object should be machine-readable, not prose-only.
5. Patch only the extractor or preset code needed for that single defect class.
6. Re-materialize current evidence for the selected page/checklist item.
7. Run the narrow deterministic checks for the touched files and the focused
   regression.
8. Commit and push only relevant code, tests, and proof receipts.
9. Use `$ask` or browser oracles only when genuinely blocked or confused, with a
   concrete bundle. Do not use an Ask response as closure proof.

The best next local artifact is a single failing executable defect object for
one current unresolved candidate. The defect schema should normalize at least:

- `document_id`
- `page_id`
- `source_pdf`
- `candidate_id`
- `actual.label`
- `actual.bbox`
- `expected.label`
- `expected.bbox`
- `evidence.page_image`
- `evidence.annotated_image`
- `evidence.current_extraction_json`
- `defect_class`
- `repair_owner`
- `genericity`
- `confidence`
- `validator`

## 6. Project Context Required For Success

Read these files before continuing PDF Lab hardening:

- `GOAL.md`
- `AGENTS.md`
- `README.md`
- `scripts/pdf_lab/materialize_historical_finding_current_evidence.py`
- `scripts/pdf_lab/reconcile_historical_findings_deterministic.py`
- `scripts/pdf_lab/snapshot_current_extraction.py`
- `scripts/pdf_lab/validate_creator_reviewer_defects.py`
- `schemas/pdf_lab/creator_reviewer_defects.schema.json`
- `python/pdf_oxide/presets/applier.py`
- `python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json`
- `tests/test_pdf_lab_creator_reviewer_defects.py`
- `tests/test_pdf_lab_snapshot_current_extraction.py`

Operational constraints:

- Do not claim the immutable goal is complete from a partial page receipt.
- Do not run broad Ask competitions as a substitute for local proof.
- Do not prioritize Git cleanup over extraction correctness.
- Do not use linguistic shortcuts or page-specific text phrases for extractor
  classification.
- Do not revert or clean unrelated dirty worktree paths.
- Preserve visual/human evidence, current extraction JSON, and receipt artifacts
  for every candidate slice.

The immediate project state is therefore: the current branch has several
focused hardening slices with receipts, `origin/main` is divergent, and the next
PDF task is to select the next current unresolved extraction candidate and
create the first failing executable defect check for it.
