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
| `page_0456` | control-table column headers | `artifacts/pdf_lab/page456_control_table_headers_20260721/selection_receipt.json` | `origin/main` at `007d572acade8a962b1c033a742eae35648d1a26` |
| `page_0034` | sidebar chrome contamination | `artifacts/pdf_lab/page34_candidate_audit_20260721/audit_summary.json` | `origin/main` at `2f04e0582722f380bbf01da416d68b0c0418100d` |
| `page_0034` | body lines as headings | `artifacts/pdf_lab/page34_candidate_audit_20260721/audit_summary.json` | `origin/main` after page34 body-heading audit push |
| `page_0045` | AC-1 lower-alpha list markers | `artifacts/pdf_lab/page45_cd_list_marker_audit_20260721/audit_summary.json` | `origin/main` at `4f6e35b3447191ff9c6ec485d4f4eeb72158381c` |
| `page_0421` | glossary term-definition materialization | `artifacts/pdf_lab/page421_glossary_term_audit_20260721/audit_summary.json` | `origin/main` after page421 push |
| `page_0104` | AU-12 page-break field label | `artifacts/pdf_lab/page104_candidate_audit_20260721/audit_summary.json` | `origin/main` after page104 push |
| `page_0035` | control families table/caption/footnote | `artifacts/pdf_lab/page35_candidate_audit_20260721/audit_summary.json` | `origin/main` after page35 push |
| `page_0045` | Control Enhancements None field/value typing | `artifacts/pdf_lab/page45_control_enhancements_none_20260721/audit_summary.json` | `origin/main` after page45 Control Enhancements push |
| `page_0045` | quick-link summary-table section link | `artifacts/pdf_lab/page45_quick_link_20260721/audit_summary.json` | `origin/main` after page45 quick-link push |
| `page_0045` | AC-1 heading normalization | `artifacts/pdf_lab/page45_ac1_heading_20260721/audit_summary.json` | `origin/main` after page45 AC-1 heading push |
| `page_0045` | AC-1 body/list TOC lineage | `artifacts/pdf_lab/page45_toc_lineage_20260721/audit_summary.json` | `origin/main` after page45 TOC-lineage push |
| `page_0045` | running header/footer/rotated DOI chrome noise | `artifacts/pdf_lab/page45_chrome_noise_20260721/audit_summary.json` | `origin/main` after page45 chrome-noise push |
| `page_0045` | Related Controls semantic role | `artifacts/pdf_lab/page45_related_controls_20260721/audit_summary.json` | `origin/main` after page45 related-controls push |
| `page_0045` | Discussion text line-order repair | `artifacts/pdf_lab/page45_discussion_text_20260721/audit_summary.json` | `origin/main` after page45 discussion-text push |
| `page_0045` | Discussion semantic role | `artifacts/pdf_lab/page45_discussion_role_20260721/audit_summary.json` | `origin/main` after page45 discussion-role push |
| `page_0104` | Control Enhancements standalone label subtitle typing | `artifacts/pdf_lab/page104_control_enhancements_subtitle_20260721/audit_summary.json` | `origin/main` after page104 Control Enhancements subtitle push |
| `page_0421` | GS001 standalone glossary citation reference residual reconciliation | `artifacts/pdf_lab/page421_reference_residual_reconciliation_20260721/audit_summary.json` | `origin/main` after page421 citation residual reconciliation push |
| `page_0036` | Figure 1 intro sentence typed as body prose | `artifacts/pdf_lab/page36_figure_intro_sentence_20260721/audit_summary.json` | `origin/main` after page36 figure-intro push |
| `page_0036` | Figure 1 embedded AU-4 heading typed as figure content | `artifacts/pdf_lab/page36_figure_control_heading_20260721/audit_summary.json` | `origin/main` after page36 figure-heading push |
| `page_0036` | Figure 1 remaining diagram-region content typed as figure content | `artifacts/pdf_lab/page36_figure_region_content_20260721/audit_summary.json` | `origin/main` after page36 figure-region push |
| `page_0045` | GS001 stale AC-2 page residual reconciliation | `artifacts/pdf_lab/page45_residual_current_20260721/audit_summary.json` | `origin/main` after page45 residual reconciliation push |
| `page_0045` | live second-pass rotated DOI side-chrome bbox narrowing | `artifacts/pdf_lab/page45_rotated_doi_bbox_20260721/audit_summary.json` | `origin/main` after page45 rotated DOI bbox push |
| `nist_style_fixture_page_0001_and_nist_page_0157` | table duplicate suppression and false-positive table handling | `artifacts/pdf_lab/nist_table_duplicate_suppression_20260721/audit_summary.json` | `origin/main` after NIST table duplicate suppression push |
| `page_0028` | golden-slice recovered fixture path and review-runner dependencies | `artifacts/pdf_lab/page28_golden_slice_fixture_20260721/audit_summary.json` | `origin/main` after page28 fixture/dependency push |
| `page_0042` | live second-pass Appendix C prose preset and callout-title classification | `artifacts/pdf_lab/page42_callout_title_20260721/audit_summary.json` | `origin/main` after page42 callout-title push |
| `page_0015` | live second-pass frontmatter reference/TOC/side-chrome review | `artifacts/pdf_lab/page15_frontmatter_review_20260721/audit_summary.json` | `origin/main` after page15 reviewed-clean evidence push |
| `page_0076` | live second-pass field-label spacing/roles and prompt raw-metadata isolation | `artifacts/pdf_lab/page76_label_spacing_prompt_20260721/audit_summary.json` | `origin/main` after page76 label-spacing/prompt push |
| `page_0001` | live second-pass frontmatter title/DOI chrome review | `artifacts/pdf_lab/page1_frontmatter_title_review_20260721/audit_summary.json` | `origin/main` after page1 reviewed-clean evidence push |
| `page_0002` | live second-pass frontmatter attribution/list and boilerplate preset review | `artifacts/pdf_lab/page2_frontmatter_attribution_20260721/audit_summary.json` | `origin/main` after page2 frontmatter-attribution push |
| `page_0003` | live second-pass frontmatter acknowledgments/reviewers and SciLLM auth default review | `artifacts/pdf_lab/page3_transport_auth_20260721/audit_summary.json` | `origin/main` after page3 transport-auth push |
| `page_0004` | live second-pass frontmatter authority/publication details review | `artifacts/pdf_lab/page4_frontmatter_review_20260721/audit_summary.json` | `origin/main` after page4 reviewed-clean evidence push |
| `page_0005` | live second-pass frontmatter abstract/keywords review | `artifacts/pdf_lab/page5_frontmatter_review_20260721/audit_summary.json` | `origin/main` after page5 reviewed-clean evidence push |
| `page_0006` | live second-pass Historical Contributions frontmatter heading review | `artifacts/pdf_lab/page6_historical_contributions_20260721/audit_summary.json` | `origin/main` after page6 Historical Contributions heading push |
| `page_0007` | live second-pass frontmatter revision/detail review | `artifacts/pdf_lab/page7_frontmatter_review_20260721/audit_summary.json` | `origin/main` after page7 reviewed-clean evidence push |
| `page_0008` | live second-pass frontmatter text/side-chrome review | `artifacts/pdf_lab/page8_frontmatter_review_20260721/audit_summary.json` | `origin/main` after page8 reviewed-clean evidence push |
| `page_0009` | live second-pass clean suggested_fix_surface prompt-contract review | `artifacts/pdf_lab/page9_clean_suggested_fix_surface_20260721/audit_summary.json` | `origin/main` after page9 prompt-contract push |
| `page_0010` | live second-pass frontmatter text/side-chrome review | `artifacts/pdf_lab/page10_frontmatter_review_20260721/audit_summary.json` | `origin/main` after page10 reviewed-clean evidence push |
| `page_0011` | live second-pass frontmatter text/side-chrome review | `artifacts/pdf_lab/page11_frontmatter_review_20260721/audit_summary.json` | `origin/main` after page11 reviewed-clean evidence push |
| `page_0012` | live second-pass frontmatter heading/text/side-chrome review | `artifacts/pdf_lab/page12_frontmatter_review_20260721/audit_summary.json` | `origin/main` after page12 reviewed-clean evidence push |
| `page_0013` | live second-pass Use of Examples paragraph merge review | `artifacts/pdf_lab/page13_use_examples_paragraph_20260721/audit_summary.json` | `origin/main` after page13 paragraph-merge push |
| `page_0014` | live second-pass Federal Records Management frontmatter review blocked by repeated SciLLM ReadTimeout | `artifacts/pdf_lab/page14_scillm_readtimeout_20260721/audit_summary.json` | `origin/main` at `42d035760394f101830b5615e67a33b6761526f9` |
| `page_0016` | live second-pass Planning Note review blocked by repeated SciLLM local-text ReadTimeout after DPI/payload reduction | `artifacts/pdf_lab/page16_scillm_readtimeout_20260721/audit_summary.json` | `origin/main` at `0a679d7ffb01a1e3a27c5f3d648bbcac735c3f28` |
| `page_0017` | live second-pass VLM-backed review with page-orchestrator registration, six candidate findings validated clean | `artifacts/pdf_lab/page17_vlm_free2_clean_20260721/audit_summary.json` | `origin/main` at `d49078fe96eaca152eab34428d6a15b314f66f16` |
| `page_0018` | live second-pass VLM-backed review with page-orchestrator registration, sixteen candidate findings validated clean | `artifacts/pdf_lab/page18_vlm_free2_clean_20260721/audit_summary.json` | `origin/main` at `a1a85a9d4a3470e3f5a1a93ffab70e9f450d0594` |
| `page_0019` | live second-pass VLM-backed review with page-orchestrator registration, seven candidate findings validated clean | `artifacts/pdf_lab/page19_vlm_free2_clean_20260721/audit_summary.json` | `origin/main` at `2dc73e8072331db45942fa5eaa8e3d923770bb72` |
| `page_0020` | live second-pass VLM-backed review with page-orchestrator registration, five candidate findings validated clean | `artifacts/pdf_lab/page20_vlm_free2_clean_20260721/audit_summary.json` | `origin/main` at `c67907e3f430a26aec65d3d0c17ff2b80b035f04` |
| `page_0021` | live second-pass VLM-backed review with page-orchestrator registration, five candidate findings validated clean | `artifacts/pdf_lab/page21_vlm_free2_clean_20260721/audit_summary.json` | `origin/main` at `4e2316f5732d86267b38973df24b02b1b5d4b635` |
| `page_0022` | live second-pass VLM-backed review with page-orchestrator registration, five candidate findings validated clean | `artifacts/pdf_lab/page22_vlm_free2_clean_20260721/audit_summary.json` | `origin/main` at `3349f61ea547e50a3845c2206e8561570f7484ae` |
| `page_0023` | live second-pass VLM-backed review with page-orchestrator registration, five candidate findings validated clean | `artifacts/pdf_lab/page23_vlm_free2_clean_20260721/audit_summary.json` | `origin/main` at `3b36d4780607df54ad7c73dabf96e29ada80ae90` |

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

The next candidate after the page28 fixture/dependency item must come from a
fresh current-extraction reviewer/candidate selection pass:

| Field | Value |
|-------|-------|
| Page | fresh reviewer-selected page |
| Defect class | to be selected from fresh current-extraction/model-review evidence |
| Observed failure | Current broad NIST deterministic suite passes after page45 rotated DOI bbox, NIST table duplicate suppression, and page28 fixture/dependency repairs; stale handoff rows for page456/page34/page45 have focused receipts or no-patch reconciliations |
| Handoff evidence | `/home/graham/workspace/experiments/pdf_oxide-gs001/local/HANDOFF.md`, measured-position table |
| Candidate artifacts to inspect first | current `artifacts/pdf_lab/` receipts, release snapshots, model-review receipts, and a fresh current-extraction reviewer pass |
| Constraint | select one checklist item only; preserve visual/current extraction evidence before patching |

Before patching the next item, produce a selection receipt that names the
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
- The `local-text` second-pass review family is not the production review path.
  It can prove minimal text transport and now returns parseable JSON, but the
  local `qwen2.5:0.5b` backend does not reliably satisfy the full page-review
  schema. Use the live VLM-backed SciLLM route, currently `vlm-free2`, for the
  next one-page review gate unless a later receipt proves a better local model.

## Required Status Shape

Every campaign status must report:

- `passed`
- `failed`
- `blocked_by_systemic_failure`
- `not_run`
- active page/checklist item
- latest failure signature
- exact artifact paths for receipts
