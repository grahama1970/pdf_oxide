Issue #22 progress from clean worktree `/home/graham/workspace/experiments/pdf_oxide-issue22-codex-20260728`.

Repair branch: `codex/issue-22-nasa-figure-reconciliation`

Local/remote repair commit: `65af6cf9` (`Reconcile NASA figure-internal labels`).

Focused proof receipt:
`artifacts/pdf_lab/issue22_nasa_figure_reconciliation_20260728/receipt.json`

Focused predicate on NASA_SP-2016-6105 page index 17 / printed page 18:

- Figure 2.0-1 count for caption: 1
- Absorbed figure `content_blocks`: 14
- Missing expected internal labels: []
- Leaked expected internal labels in non-caption page blocks: []
- Remaining non-figure page blocks after reconciliation: 5
- Figure bbox: `[84.719970703125, 316.67999267578125, 448.08355712890625, 384.0447998046875]`

Commands run:

- `uv run pytest tests/test_nasa_page18_figure_internal_content.py -q` -> `2 passed in 8.90s`
- `cargo test --features python figure_detector --lib` -> `9 passed; 0 failed; 4594 filtered out`

Residual integration blocker:

- `uv run pytest tests/test_nist_page456_control_table_headers.py -q` failed in this clean `origin/main` worktree with a page456 table-header `source_ids` baseline mismatch.
- I am not closing this issue from this worktree until that integration mismatch is reconciled against the live project state.
