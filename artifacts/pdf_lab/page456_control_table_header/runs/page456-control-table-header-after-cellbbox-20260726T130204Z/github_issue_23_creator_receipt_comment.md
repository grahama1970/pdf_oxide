Creator receipt for P456-TH-01:

Fresh run id:

`page456-control-table-header-after-cellbbox-20260726T130204Z`

Receipt:

`artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-after-cellbbox-20260726T130204Z/receipt.json`

Fresh artifacts from the same run:

- Page image: `artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-after-cellbbox-20260726T130204Z/page.png`
- Extraction JSON: `artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-after-cellbbox-20260726T130204Z/extraction.pdf_oxide.json`
- Overlay image: `artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-after-cellbbox-20260726T130204Z/overlay.png`
- Bbox metrics: `artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-after-cellbbox-20260726T130204Z/bbox-metrics.json`
- Regression output: `artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-after-cellbbox-20260726T130204Z/regression.stdout.txt`

What changed:

- `scripts/pdf_lab/snapshot_current_extraction.py` now enriches the first row of lattice table payloads with header-row and column-header cell bboxes derived from PDF drawing-grid rectangles.
- `scripts/pdf_lab/run_next30_agentic_second_pass.py` now renders nested table cells that carry bboxes.
- `tests/test_nist_page456_control_table_headers.py` now asserts the four header-cell labels, roles, bbox source, and normalized bbox tolerances.
- `scripts/pdf_lab/refresh_page456_control_table_header_bundle.py` creates the fresh one-run evidence bundle.

Focused proof:

```text
PYTHONPATH=python pytest -q tests/test_pdf_lab_snapshot_current_extraction.py tests/test_nist_table_duplicate_suppression.py tests/test_nist_page456_control_table_headers.py
26 passed, 5 warnings
```

Receipt highlights:

```json
{
  "page_image_dimensions": [918, 1188],
  "regression_exit_code": 0,
  "target_leak_count": 0,
  "table_count": 1
}
```

Fresh extraction header row now includes:

```json
{
  "role": "header_row",
  "bbox_source": "pdf_drawing_grid",
  "cells": [
    {"text": "CONTROL NUMBER", "role": "column_header", "bbox_source": "pdf_drawing_grid"},
    {"text": "CONTROL NAME CONTROL ENHANCEMENT NAME", "role": "column_header", "bbox_source": "pdf_drawing_grid"},
    {"text": "IMPLEMENTED BY", "role": "column_header", "bbox_source": "pdf_drawing_grid"},
    {"text": "ASSURANCE", "role": "column_header", "bbox_source": "pdf_drawing_grid"}
  ]
}
```

Reviewer note:

The fresh overlay visibly shows the four blue `column_header` cell boxes over the top table row. This is still a page456 slice, not completion of the all-candidates hardening goal.
