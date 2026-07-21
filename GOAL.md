# PDF Lab All-Candidates Hardening Goal

Last updated: 2026-07-21

## Immutable Objective

Harden all PDF Lab page candidates one page/checklist item at a time across the
active candidate queue, using a proof ladder that preserves visual/human
evidence, creates or updates the focused regression before patching, runs a
bounded scillm/OpenCode executor only when useful, performs deterministic
project-agent audit, commits and pushes task-relevant code/artifacts, and
advances only when the current candidate is proven or explicitly blocked with
receipt artifacts.

## Scope

This goal replaces the prior page46-only objective. Page46 remains completed
evidence, not the active goal boundary.

Current completed page evidence:

| Page | Issue | Receipt | Remote proof |
|------|-------|---------|--------------|
| `nist_phase54_page_0046` | `grahama1970/pdf_oxide#3` | `artifacts/pdf_lab/restart_recovery_20260721T0100Z/page46_final_audit_summary.json` | `origin/main` at `9c80e780f6716b5284d89bac196dffbc261342b7` |

The active queue is source-derived from PDF Lab artifacts, GS001 handoffs, and
current repository evidence. Do not treat a stale page-local section in an old
handoff as the goal boundary.

## Per-Candidate Proof Ladder

For each page/checklist item:

1. Select exactly one candidate from the active queue.
2. Justify the selection with concrete evidence: page id, expected region count,
   defect class, artifact paths, and why it is the next useful target.
3. Preserve the page image, overlay or visual annotation, current extraction,
   model review, and any human annotation evidence as receipt inputs.
4. Convert or attach the evidence to one page-level GitHub issue when the page
   has multiple related defects.
5. Track grouped page bugs as checklist items. Each item must name the owner:
   `pdf_oxide_core`, `nist_preset`, export/schema, UI, or external harness.
6. Fix one checklist item at a time. Do not batch unrelated defects because they
   share a page.
7. Create or update a focused regression before patching. The regression must
   point back to the page issue and source evidence.
8. Use a bounded scillm/OpenCode executor only when it is useful and the prompt
   contract is concrete. Tau owns DAG orchestration; do not modify scillm
   internals from this repository.
9. Audit the diff, test output, extraction artifact, and executor receipts.
10. Commit and push only task-relevant code and receipt artifacts after focused
    proof passes.
11. Advance only when the current checklist item is proven or explicitly blocked
    with named receipt artifacts.

## Active Next Candidate

The next candidate after page46 is page456, based on the GS001 handoff:

| Field | Value |
|-------|-------|
| Page | `page_0456` / NIST SP 800-53r5 page 456 |
| Defect class | control-table column headers |
| Observed failure | `CONTROL NUMBER`, `CONTROL NAME`, and `IMPLEMENTED` extract as headings/prose instead of table/header structure |
| Handoff evidence | `/home/graham/workspace/experiments/pdf_oxide-gs001/local/HANDOFF.md`, section 5 |
| Candidate artifacts to inspect first | `artifacts/pdf_lab/scillm_bug_report_pilot_gpt55_clusters/page_0456/` and `artifacts/pdf_lab/project_agent_hardening/page_0456_release_after_hardening.json` |
| Constraint | positional, typographic, and frequency-based fixes only; no source phrases, page numbers, or control IDs as classifier shortcuts |

Before patching page456, produce a selection receipt that names the exact page
image/current extraction/model-review artifacts used and the focused regression
that will prove the single selected checklist item.

## Blockers And Boundaries

- Criterion 6 live GitHub apply remains blocked until there is a valid approval
  receipt for mutation.
- Broad self-graded candidate totals are not proof. Independent reviewer output
  and deterministic extraction/test artifacts must be reconciled before any
  closure claim.
- Do not expand into dashboards, aggregate reports, or UI polish while a
  selected candidate lacks focused extraction proof.
- Do not mark the all-candidates goal complete until every active candidate has
  either passing receipt-backed proof or an explicit blocked receipt.

## Required Status Shape

Every campaign status must report:

- `passed`
- `failed`
- `blocked_by_systemic_failure`
- `not_run`
- active page/checklist item
- latest failure signature
- exact artifact paths for receipts
