# Immutable Goal: PDF-EXTRACTION-GS001-TAU-V1

Status: Draft (not yet locked — see Goal-lock pins)
Owner: Human (graham@grahama.co)

## Goal

From clean, SHA-pinned checkouts of the audited `pdf_oxide` and
`agent-skills` baselines, using a SHA-256-pinned NIST SP 800-53r5 source
PDF and a human-approved, SHA-pinned GS001 expected-element contract
(11 rows), Tau shall execute one complete extraction-repair lifecycle:
release-mode extraction, deterministic comparison, bounded agentic
second-pass adjudication, typed owner routing, deduplicated and
apply-gated defect-ticket creation, one bounded repair transaction,
focused and frozen-set regression testing, structured code review, clean
rerun, deterministic comparison, and visual closure publication.

Humans decide what must happen. Agents propose and execute bounded work.
Tau decides what counts as admissible progress.

## Hypothesis under test (with kill criterion)

Treating extraction discrepancies as fingerprinted bug reports repaired
by a bounded coder/reviewer loop is a NOVEL, UNPROVEN approach. It is a
hypothesis, not an assumption. Kill criterion: if two consecutive
accepted repairs pass the target fixture only through memorization
(page-specific or exact-source-text rules caught by the anti-overfit
inspection, or fixture-pass with holdout regression), STOP: the repair
loop design is falsified in its current form and must be redesigned
before any further batch work.

## Completion criteria

1. Repository integrity: clean checkouts at the pinned commits; no
   uncommitted local-only repair on the critical path; upstream
   compatibility decision documented (PyPI pdf_oxide 0.3.74 vs fork).
2. Expected-contract integrity: the two GS001 human decisions are
   resolved (DONE 2026-07-18 — both are expected elements; see
   golden_slices/gs001_nist_page28/contract_decisions.json), rows 1-9
   recovered from the human-labeled bundle, contract locked and hashed
   by scripts/gs001_lock.py.
3. Extraction closure on GS001: 11/11 expected elements uniquely
   matched; 0 missing; 0 ambiguous; 0 unwaived extras; every accepted
   extra carries a signed waiver (comparator schema pdf-lab.comparison.v2
   strict verdict).
4. No overfitting: no exact source phrase, page-number condition, or
   NIST control-ID copied into classifier code; static anti-overfit
   inspection of every accepted patch; document-family preset rules must
   be positional/typographic/frequency-based.
5. Regression proof: focused fixture passes; frozen NIST packet does not
   regress (pdf_lab.regression_verdict.v1 PASS with targeted class
   strictly decreased); known-failing baseline (toc_detector
   test_extract_simple_toc + 3 lattice tests) does not grow.
6. Ticket lifecycle proof: every discrepancy carries a stable
   defect_key; ticket projection is dry-run first and apply-gated;
   repeated observations update the existing ticket; the issue closes
   only on accepted commit + clean rerun receipt.
7. Tau proof: the pipeline runs as a tau workflow with valid receipts,
   goal-hash continuity across every node and rerun, bounded attempts,
   and an explicit terminal reason; failed repairs roll back to the git
   preimage (a rollback receipt, not a status label).
8. Closure proof: machine-readable closure record + visual closure page
   + terminal receipt, replayable from a clean checkout.
9. Truth reconciliation: issues agent-skills#70-#72 re-verified against
   the landed baseline (#70's fix landed 2026-07-18; #71/#72 pending
   verification); the two draft PR #1s merged, superseded, or closed.
10. Human acceptance — the one criterion no machine may establish.

## Non-goals

- No full-document NIST claim and no 20-page-packet claim (that is the
  successor goal PDF-EXTRACTION-NIST20-GENERALIZATION-V1).
- No broad PDF-family generalization, no scanned/OCR scope.
- No automatic merging to upstream pdf_oxide.
- No parallel multi-defect repair swarm: one active repair, one bounded
  retry.
- No requirement that a nondeterministic reviewer return zero findings;
  the second-pass model is a discovery and routing mechanism, never the
  closure oracle.
- No silent change to the expected contract; changes route through a
  human goal-change packet only.

## Critical-path rule

Every task must name the completion criterion it advances. Work that
advances none is a side quest and must not be performed under this goal.

## Goal-lock pins

The goal_hash is computed by `scripts/gs001_lock.py lock-goal` over this
file only when every pin below is resolved (fail closed — no pin may
contain the word PENDING).

- `goal_id`: PDF-EXTRACTION-GS001-TAU-V1
- `goal_version`: 1
- `pdf_oxide_baseline_commit`: PENDING_BASELINE_AUDIT
- `agent_skills_baseline_commit`: PENDING_BASELINE_AUDIT
- `tau_baseline_commit`: PENDING_BASELINE_AUDIT
- `source_pdf_sha256`: fc63bcd61715d0181dd8e85998b1e6201ae3515fc6626102101cab1841e11ec6
- `expected_contract_sha256`: PENDING_ROW_RECOVERY
- `preset_ledger_sha256`: PENDING_BASELINE_AUDIT
- `frozen_regression_set`: PENDING_PACKET_RECOVERY (stratified NIST pages 20 468 401 415 483 34 31 32 33 23)
- `max_repair_attempts`: 2

## Unblock list (what resolves the pins)

1. ~~`source_pdf_sha256`~~ — RESOLVED 2026-07-18. Committed as corpus
   fixture at `golden_slices/gs001_nist_page28/source/NIST_SP_800-53r5.pdf`;
   sha256 verified byte-identical to the live download from
   nvlpubs.nist.gov, not merely to the local artifact copies.
2. `expected_contract_sha256` — BLOCKED. The original human-labeled bundle
   (/tmp/pdf-lab-golden-slices/nist_page_28_printed_page_1/) is GONE — /tmp
   was cleared and no copy exists on the workstation. Rows 1-9 must be
   relabeled by a human against the rendered page; then `lock-contract`.
   Do NOT source rows from
   /tmp/embry-interrupt-codex/.../nist-phase54-toc-backed/pages/page_0028/
   — that packet is derived from `release_extraction_blocks.json`
   (extractor output) and is disqualified by the no-inference rule.
3. Baseline commits — complete the integration-branch baseline audit
   (fork vs upstream 0.3.74) and pin the three repo SHAs.
4. `frozen_regression_set` — recover or regenerate the ten-page review
   packet referenced by agent-skills issues #70-#73.
