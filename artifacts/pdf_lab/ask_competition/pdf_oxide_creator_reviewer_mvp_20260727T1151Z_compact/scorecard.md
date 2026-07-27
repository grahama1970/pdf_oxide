# PDF-Oxide Creator-Reviewer MVP Competition Scorecard

Status: NEEDS_ATTENTION
Created: 2026-07-27T12:24:00Z
Ticket: https://github.com/grahama1970/agent-skills/issues/1029

## Source-Derived Step Model

1. Page evidence packet is assembled.
   - Implemented: page image, overlay image, extraction JSON, bbox metrics, receipt, and focused regression exist for NIST page 456.
   - Evidence:
     - `artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-lineage-proof-20260726T133000Z/page.png`
     - `artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-lineage-proof-20260726T133000Z/overlay.png`
     - `artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-lineage-proof-20260726T133000Z/extraction.pdf_oxide.json`
     - `artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-lineage-proof-20260726T133000Z/bbox-metrics.json`

2. Creator proposes current extraction facts.
   - Implemented: `pdf_oxide` emits one table block for page 456 with four `column_header` cells in the first row.
   - Evidence: table bbox `[0.14666667015723933, 0.11371211812953756, 0.8525490355647467, 0.9040909102468779]`, `row_count=43`, `column_count=4`, `target_leak_count=0`, `target_lineage_count=5`.

3. Reviewer emits a machine-executable verdict object.
   - Intended: reviewer output is a strict JSON defect or guard object, not prose.
   - Missing: no committed canonical schema yet defines the exact creator-reviewer object consumed by the repair planner.

4. Planner maps defect class to repair scope.
   - Intended: each defect class resolves to exact files/functions, expected invariant, allowed patch scope, and focused proof command.
   - Missing: no repair-map file currently connects reviewer defects to deterministic extractor repair targets.

5. Creator patches extractor logic.
   - Implemented for this case: page456 table-header lineage repair already exists in the active evidence worktree.
   - Evidence: `tests/test_nist_page456_control_table_headers.py` passes for the current page456 case.

6. Independent verifier replays the same page.
   - Implemented for local deterministic regression: `pytest tests/test_nist_page456_control_table_headers.py` produced `2 passed, 5 warnings in 18.26s`.
   - Missing for the Ask competition: no browser competitor produced a clean receipt-backed MVP result suitable for declaring a competition winner.

7. Accepted repair becomes reusable learning material.
   - Intended: accepted repairs should create a reusable defect class and repair pattern.
   - Missing: no canonical learning ledger entry exists for this page456 table-header class.

## Competition Result

No clean winner was selected. The Ask compile-only DAG was READY, but every live browser lane failed its receipt or provider gate.

Compile-only proof:

- Run directory: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_20260727T1151Z/pdf-oxide-creator-reviewer-mvp-page456-20260727T1151Z`
- Status: `READY`
- Proves: DAG contract emitted before execution.
- Does not prove: provider calls, semantic quality, browser handler success, or a semantically correct winner.

Live lane outcomes:

| Lane | Result | Usable Content | Blocking Receipt |
| --- | --- | --- | --- |
| WebGPT | Degraded content lead | Yes, response text and sentinel were captured | `browser_tab_identity_mismatch`; `controlled_tab_id=null` while `requested_tab_id=837362426` |
| WebGemini | Degraded secondary content | Partial; response contains task-relevant schema ideas but duplicate/stale sentinel risk | `prompt_too_large_or_stalled` |
| WebClaude | Failed | No | `browser_tab_read_timeout` |
| WebKimi | Failed | No | `browser_provider_rate_limited` |

Primary degraded WebGPT artifact:

- `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_20260727T1151Z_single/pdf-oxide-creator-reviewer-mvp-page456-single-webgpt-fresh-20260727T1151Z/node-artifacts/handler-webgpt/response.md`

Primary degraded Gemini artifact:

- `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_20260727T1151Z_compact_live/pdf-oxide-creator-reviewer-mvp-page456-compact-20260727T1151Z/node-artifacts/handler-webgemini/response.raw.md`

## Best Harvested MVP Direction

Use the degraded WebGPT answer as the leading design candidate, but not as proof:

1. Add a local one-page command-line loop with explicit states:
   - `replay`
   - `review`
   - `plan`
   - `repair`
   - `verify`
   - `self-test`

2. Add one canonical schema for both failures and satisfied guards.

3. Add a repair map from defect class to exact extractor files/functions and proof commands.

4. Add a first guard object for page456 table-header lineage exclusivity.

5. Validate the object locally with JSON Schema plus the existing focused pytest.

## Ideal Defect/Guard Schema

The schema should produce one executable object per candidate. It should support both `defect` and `guard` records so the loop can prove an observed issue is now satisfied.

Minimum required fields:

```json
{
  "schema_version": "pdf_oxide.creator_reviewer.defect.v1",
  "record_type": "guard",
  "state": "satisfied",
  "document_id": "nist-sp-800-53r5",
  "page_number": 456,
  "candidate_id": "page456-control-table-header",
  "source_artifacts": [
    {
      "kind": "page_image",
      "path": "artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-lineage-proof-20260726T133000Z/page.png",
      "sha256": null
    },
    {
      "kind": "overlay_image",
      "path": "artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-lineage-proof-20260726T133000Z/overlay.png",
      "sha256": null
    },
    {
      "kind": "extraction_json",
      "path": "artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-lineage-proof-20260726T133000Z/extraction.pdf_oxide.json",
      "sha256": null
    }
  ],
  "subject": {
    "element_id": "actual:p456:table:0",
    "source_ids": [
      "actual:p456:block:5",
      "actual:p456:block:6",
      "actual:p456:block:7",
      "actual:p456:block:8",
      "actual:p456:block:9",
      "actual:p456:block:10",
      "actual:p456:block:11"
    ],
    "bbox": [
      0.14666667015723933,
      0.11371211812953756,
      0.8525490355647467,
      0.9040909102468779
    ]
  },
  "actual_label": "table",
  "expected_label": "table",
  "defect_class": "TABLE_HEADER_LINEAGE_EXCLUSIVITY",
  "failure_mode": "none",
  "evidence": {
    "visual_assertion": "The page contains a ruled four-column table headed CONTROL NUMBER, CONTROL NAME / CONTROL ENHANCEMENT NAME, IMPLEMENTED BY, ASSURANCE.",
    "json_assertion": "The first row has four cells with role=column_header and bbox_source=pdf_drawing_grid.",
    "negative_assertion": "Header fragments do not leak as standalone heading or paragraph blocks.",
    "confidence": 0.99
  },
  "repair_plan": {
    "repair_class": "TABLE_HEADER_LINEAGE_EXCLUSIVITY",
    "target_files": [
      "tests/test_nist_page456_control_table_headers.py"
    ],
    "target_functions": [],
    "allowed_patch_scope": "table-header detection, grid-backed header cell lineage, and focused regression only",
    "proof_commands": [
      "pytest tests/test_nist_page456_control_table_headers.py"
    ]
  },
  "validation": {
    "status": "pass",
    "commands": [
      {
        "cmd": "pytest tests/test_nist_page456_control_table_headers.py",
        "result": "2 passed, 5 warnings in 18.26s",
        "mocked": false,
        "live": false
      }
    ]
  }
}
```

Required schema constraints:

- `additionalProperties: false` at every object level.
- `record_type` is one of `defect`, `guard`.
- `state` is one of `open`, `satisfied`, `blocked_by_systemic_failure`.
- `actual_label` and `expected_label` use a closed vocabulary shared with extractor output.
- `bbox` fields are normalized page coordinates and must have exactly four numbers.
- `defect_class` uses a closed repair taxonomy.
- `repair_plan.target_files` and `repair_plan.proof_commands` are mandatory before patching.
- `validation.commands[*].mocked` and `validation.commands[*].live` are mandatory so fixture-only proof cannot be inflated.

## Recommended Next Action

Do not run another browser competition until agent-skills issue 1029 is repaired or the human explicitly accepts degraded browser outputs as advisory design material.

The next deterministic pdf-oxide action is to implement the local one-case schema and validator from the harvested WebGPT/Gemini ideas, then validate the page456 guard object with JSON Schema plus `pytest tests/test_nist_page456_control_table_headers.py`.
