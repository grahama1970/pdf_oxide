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
| `page_0456` | control-table column headers | `artifacts/pdf_lab/page456_control_table_headers_20260721/audit_summary.json` | `origin/main` at `007d572acade8a962b1c033a742eae35648d1a26` |
| `page_0034` | sidebar chrome contamination | `artifacts/pdf_lab/page34_candidate_audit_20260721/audit_summary.json` | `origin/main` at `2f04e0582722f380bbf01da416d68b0c0418100d` |
| `page_0045` | AC-1 lower-alpha list markers | `artifacts/pdf_lab/page45_cd_list_marker_audit_20260721/audit_summary.json` | `origin/main` at `4f6e35b3447191ff9c6ec485d4f4eeb72158381c` |
| `page_0421` | glossary term-definition materialization | `artifacts/pdf_lab/page421_glossary_term_audit_20260721/audit_summary.json` | `origin/main` after page421 push |
| `page_0104` | AU-12 page-break field label | `artifacts/pdf_lab/page104_candidate_audit_20260721/audit_summary.json` | `origin/main` after page104 push |
| `page_0035` | control families table/caption/footnote | `artifacts/pdf_lab/page35_candidate_audit_20260721/audit_summary.json` | `origin/main` after page35 push |
| `page_0045` | Control Enhancements None field/value typing | `artifacts/pdf_lab/page45_control_enhancements_none_20260721/audit_summary.json` | `origin/main` after page45 Control Enhancements push |

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

The next candidate after the page45 Control Enhancements None item is another
remaining page45 checklist item, based on the GS001 handoff's independent
reviewer measurement:

| Field | Value |
|-------|-------|
| Page | `page_0045` / NIST SP 800-53r5 page 45 |
| Defect class | to be selected from the remaining independent reviewer findings |
| Observed failure | GS001 handoff reports page45 reduced from 29 findings to 20 remaining findings; the AC-1 lower-alpha marker and Control Enhancements None checklist items now have focused receipts |
| Handoff evidence | `/home/graham/workspace/experiments/pdf_oxide-gs001/local/HANDOFF.md`, measured-position table |
| Candidate artifacts to inspect first | current `artifacts/pdf_lab/` page45 receipts, release snapshot, model-review receipts, and live current extraction |
| Constraint | select one checklist item only; preserve visual/current extraction evidence before patching |

Before patching the next page45 item, produce a selection receipt that names the
exact page image/current extraction/model-review artifacts used and the focused
regression that will prove the single selected checklist item.

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
