# PDF Oxide Page 45 Broad-NIST Failure Triage Bundle

## Current Gate

Decide whether the page 45 rotated side-chrome bbox repair can proceed to a task-scoped commit, or whether one of the broad `test_nist_*` failures is likely caused by the current patch and must be repaired first.

Return a concise assessment with one ruling:

- `PASS_CURRENT_GATE`
- `BLOCKED_CURRENT_GATE: <one concrete blocker>`
- `REJECTED_SCOPE_EXPANSION`

## Immutable Goal Context

The active goal is to harden PDF Lab page candidates one page/checklist item at a time across the active queue, preserving visual/human evidence, focused regression proof, live model-backed second-pass evidence when relevant, deterministic audit, and task-relevant commit/push receipts.

## Patch Under Review

Files changed:

- `scripts/pdf_lab/snapshot_current_extraction.py`
- `tests/test_nist_page45_chrome_noise.py`

Patch shape:

- Add `_compact_text()`.
- Add `_rotated_side_chrome_lines_from_block()` to match a `Boilerplate` block against PyMuPDF vertical margin lines using whitespace-insensitive text plus y-axis overlap.
- In `_block_elements()`, when generic `_match_text_lines()` fails for `source_type == "Boilerplate"`, use the rotated-side-chrome match and narrow the bbox to the raw vertical line bbox.
- Add regression `test_nist_page_45_rotated_doi_chrome_bbox_stays_in_left_margin()`.

The patch intentionally keeps `source_type == "Boilerplate"` stable for page 45.

## Live Evidence

Pre-fix live one-case run:

- Artifact: `artifacts/pdf_lab/live_second_pass_one_case_20260721T0101Z/`
- Live model transport succeeded with `gpt-5.5`, parseable schema JSON under the 120s timeout.
- `review_validation.json`: ok true, 21 expected candidates and 21 seen candidates.
- Model defect: `cand:p0045:0003:side_chrome` bbox overlapped main body; model said the rotated DOI side-chrome box should be narrow left-margin text.
- `terminal_ledger.json`: `terminal_status: still_open`, `reason: patch_delegate_dry_run`.
- Top-level harness final gate: failed closed because dry-run patch left the case unresolved.

Post-fix live one-case run:

- Artifact: `artifacts/pdf_lab/live_second_pass_one_case_20260721T0623Z/`
- Command forced exactly page 45 with `--review-mode live`, `--patch-mode dry_run`, `--commit-mode dry_run`, model `gpt-5.5`, scillm timeout 120s.
- Harness terminal output: `{"out": "artifacts/pdf_lab/live_second_pass_one_case_20260721T0623Z", "selected_pages": [45], "terminal_status": "passed"}`
- `harness_final_gate.json`: `ok: true`, `terminal_status: passed`, `error: null`.
- `review_request_validation.json`: `ok: true`, `image_part_count: 2`, `text_part_count: 1`.
- `review_validation.json`: `ok: true`, `expected_count: 21`, `seen_count: 21`.
- `terminal_ledger.json`: `terminal_status: reviewed_clean`, `reason: scillm_review_validated_clean`.
- `review_response.json`: `page_status: clean`; all 21 candidate findings are `clean`.
- Repaired candidate `cand:p0045:0003:side_chrome` bbox in `candidate_presets.json`: `[0.0296323533151664, 0.28780303338561397, 0.049705879361021756, 0.7406287915778883]`.
- CDP render proof: `review_cdp_render_validation.json` has `ok: true`, two loaded page images, and expected candidate text present; screenshot: `review_cdp_screenshot.png`.

## Deterministic Focused Proof

Focused test:

```text
pytest -q tests/test_nist_page45_chrome_noise.py
2 passed, 5 warnings in 52.97s
```

Page 45 guard suite:

```text
pytest -q tests/test_nist_page45_discussion_role.py tests/test_nist_page45_discussion_text.py tests/test_nist_page45_related_controls.py tests/test_nist_page45_chrome_noise.py tests/test_nist_page45_toc_lineage.py tests/test_nist_page45_ac1_heading.py tests/test_nist_page45_quick_link.py tests/test_nist_page45_control_enhancements_none.py tests/test_nist_page45_ac1_list_markers.py
13 passed, 5 warnings in 317.93s
```

## Broad NIST Regression Attempt

Command:

```text
pytest -q $(rg --files tests | rg 'test_nist_' | sort)
```

Result:

```text
3 failed, 40 passed, 5 warnings, 7 errors in 515.37s
```

Error signature 1, repeated 7 times:

```text
Failed: expected_elements not present: /tmp/pdf-lab-golden-slices/nist_page_28_printed_page_1/expected_elements_v2.json
```

All 7 errors are setup failures from `tests/test_nist_page_28_regression.py`.

Failure signature 2, table suppression:

```text
FAILED tests/test_nist_table_duplicate_suppression.py::test_nist_style_page_1_real_extraction_suppresses_qid_table_row_duplicates
AssertionError: assert 'Access Control Policy and Procedures' in table_text
```

Failure signature 3, table suppression:

```text
FAILED tests/test_nist_table_duplicate_suppression.py::test_nist_table_suppression_removes_full_width_qid_rows_inside_table
Expected only title and table, but full-width QID row blocks remained.
```

Failure signature 4, table suppression:

```text
FAILED tests/test_nist_table_duplicate_suppression.py::test_nist_false_positive_table_is_removed_before_body_suppression
Expected first element type section_heading, got header_footer_noise.
```

## Research Context

Brave search was run as required before WebGPT:

```text
/home/graham/workspace/experiments/agent-skills/skills/brave-search/run.sh web "pytest fixture missing expected_elements_v2.json baseline golden slices regression tests" --count 3
```

Returned public pytest fixture documentation, not project-specific data:

- https://docs.pytest.org/en/stable/reference/reference.html
- https://docs.pytest.org/en/stable/deprecations.html
- https://docs.pytest.org/en/stable/how-to/fixtures.html

This suggests the missing page-28 fixture is local artifact/setup state, not a public dependency issue.

## Constraints

- Do not touch scillm internals.
- Tau owns DAG/harness internals; this repair is in pdf_oxide only.
- Do not expand into table architecture unless the current patch likely caused those failures.
- WebGPT output is advisory only. Repository evidence and local tests are controlling.
- The desired next action is a task-scoped commit if the broad failures are independent/pre-existing or environment-local.

## Question

Given the patch scope and the evidence above, are the broad NIST failures likely blockers for committing the page 45 rotated side-chrome bbox repair, or are they independent residual/environment failures that should be tracked separately while committing the page 45 repair and receipts?
