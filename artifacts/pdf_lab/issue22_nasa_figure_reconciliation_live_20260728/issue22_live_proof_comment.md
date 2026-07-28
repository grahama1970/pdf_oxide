Issue #22 live checkout proof: NASA figure-internal labels are grouped under the Figure element.

Receipt: `artifacts/pdf_lab/issue22_nasa_figure_reconciliation_live_20260728/receipt.json`
Snapshot JSON: `artifacts/pdf_lab/issue22_nasa_figure_reconciliation_live_20260728/snapshot.json`

Mocked: no
Live: yes

Commands:
- `uv run maturin develop` -> exit 0
- `uv run pytest tests/test_nasa_page18_figure_internal_content.py -q` -> 2 passed in 3.24s
- `cargo test --features python figure_detector --lib` -> 4 passed; 0 failed; 4543 filtered out
- `uv run pytest tests/test_nist_page456_control_table_headers.py -q` -> 2 passed in 2.77s
- `uv run python scripts/pdf_lab/snapshot_current_extraction.py --pdf '<NASA>' --out artifacts/pdf_lab/issue22_nasa_figure_reconciliation_live_20260728/snapshot.json --max-pages 18 --apply-mode release` -> exit 0

Extractor predicate:
- figure_count_for_caption: 1
- figure_bbox: [84.719970703125, 316.67999267578125, 448.08355712890625, 384.0447998046875]
- content_block_count: 15
- missing_internal_labels: []
- leaked_internal_labels: []
- remaining_page_block_count: 6

PDF Lab snapshot predicate:
- figure_count_for_caption: 1
- normalized figure_bbox: [0.13843132467830882, 0.11524647414082229, 0.8705939997255413, 0.6001515243992661]
- missing_internal_labels: []
- leaked_internal_label_block_ids: []
- page_block_count: 7
