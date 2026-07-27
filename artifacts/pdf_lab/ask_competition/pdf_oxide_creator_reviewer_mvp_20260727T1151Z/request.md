Objective:
Design the smallest executable MVP for a pdf_oxide creator-reviewer loop that turns one PDF page's visual/extraction evidence into accurate bounding boxes and labels, without relying on reviewer prose or human interpretation.

Immutable goal / acceptance bar:
For the supplied page456 case, the loop must produce a machine-executable reviewer defect object and creator repair contract that can converge on visibly correct table/header/page-chrome bounding boxes and labels. The proposal is not accepted unless it defines exact schema fields, repair taxonomy, local proof gates, and stop conditions that a project agent can run without reinterpreting English prose.

Target repo/path:
/home/graham/workspace/experiments/pdf_oxide_page456_push_20260726T130414Z

Allowed scope:
- Propose the MVP contract, schema, loop steps, and local proof commands.
- Reference existing code/tests/artifacts by path.
- Do not propose dashboards, new UI, broad orchestration, or generic project management.
- Do not treat browser/model prose as proof.
- Do not require a human to interpret reviewer prose between creator and repairer.

Shared project context:
pdf_oxide already has a mature Rust/Python extraction path and PDF Lab evidence artifacts. The blocker is the creator-reviewer contract: previous reviewer output drifted into prose observations, GitHub tickets, status reports, and manual judgment instead of producing executable defect objects that a repair agent could deterministically patch and replay.

What was failing:
- Reviewer observations were English, not normalized defect objects.
- The unit of work drifted to pages instead of reusable defect classes.
- GitHub issues sometimes became the terminal artifact instead of a closed repair loop.
- Creator output was structured enough to inspect, but reviewer output was not structured enough to repair.
- The repair agent had to reinterpret prose such as "looks like a table heading" rather than consume fields like actual_label, expected_label, repair_class, target_files, invariant, and validation_gate.

One-case evidence packet:
- page image: artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-lineage-proof-20260726T133000Z/page.png
- overlay image: artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-lineage-proof-20260726T133000Z/overlay.png
- extraction JSON: artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-lineage-proof-20260726T133000Z/extraction.pdf_oxide.json
- bbox metrics: artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-lineage-proof-20260726T133000Z/bbox-metrics.json
- receipt: artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-lineage-proof-20260726T133000Z/receipt.json
- focused regression: tests/test_nist_page456_control_table_headers.py

Project-agent visual readback:
- page.png is a 918x1188 PNG of NIST SP 800-53r5 page 456.
- The page contains a large ruled table titled "TABLE C-1: ACCESS CONTROL FAMILY".
- The overlay labels top header/footer noise, side chrome, bottom footer, the table caption, four column-header cells, and one large table region.
- The visible table has four columns: CONTROL NUMBER, CONTROL NAME / CONTROL ENHANCEMENT NAME, IMPLEMENTED BY, ASSURANCE.

Current extraction facts:
- extraction JSON has 6 top-level blocks.
- table block id: actual:p456:table:0
- table bbox: [0.14666667015723933, 0.11371211812953756, 0.8525490355647467, 0.9040909102468779]
- table row_count: 43
- table column_count: 4
- first row role: header_row
- first row source_ids: actual:p456:block:5 through actual:p456:block:11
- header cells:
  - text: CONTROL NUMBER; role: column_header; source_ids: actual:p456:block:5, actual:p456:block:6; bbox_source: pdf_drawing_grid
  - text: CONTROL NAME CONTROL ENHANCEMENT NAME; role: column_header; source_ids: actual:p456:block:7, actual:p456:block:8; bbox_source: pdf_drawing_grid
  - text: IMPLEMENTED BY; role: column_header; source_ids: actual:p456:block:9, actual:p456:block:10; bbox_source: pdf_drawing_grid
  - text: ASSURANCE; role: column_header; source_ids: actual:p456:block:11; bbox_source: pdf_drawing_grid
- bbox metrics: table_count=1, target_leak_count=0, target_lineage_count=5
- regression stdout: 2 passed, 5 warnings in 18.26s

Focused code/test context:
- tests/test_nist_page456_control_table_headers.py asserts that header text does not leak as standalone non-table blocks and that each header cell has role=column_header, bbox_source=pdf_drawing_grid, expected source_ids, and approximate bbox.
- Related extraction/preset paths:
  - src/structure/table_extractor.rs
  - src/structure/spatial_table_detector.rs
  - src/extractors/section_hierarchy.rs
  - src/extractors/block_merger.rs
  - python/pdf_oxide/presets/applier.py
  - python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json

Baseline defect schema to improve or keep:
```json
{
  "schema_version": "pdf_oxide.defect.v1",
  "document_id": "nist_sp_800_53r5",
  "page_number": 456,
  "candidate_id": "page456-table-header-contract",
  "source_artifacts": {
    "page_image": "page.png",
    "overlay_image": "overlay.png",
    "extraction_json": "extraction.pdf_oxide.json",
    "bbox_metrics": "bbox-metrics.json"
  },
  "subject": {
    "element_id": "actual:p456:table:0/raw/rows/0/cells/0",
    "actual_label": "column_header",
    "expected_label": "column_header",
    "actual_bbox": [0.1466667, 0.1137121, 0.2338235, 0.1871212],
    "expected_bbox": [0.1466667, 0.1137121, 0.2338235, 0.1871212]
  },
  "defect": {
    "class": "TABLE_HEADER_ALIGNMENT",
    "failure_mode": "contract_guard_or_regression_target",
    "severity": "blocks_extraction_quality_if_regressed",
    "genericity": "reusable_rule",
    "confidence": 0.99
  },
  "evidence": {
    "visual": [
      "header cell sits in ruled table header row",
      "cell boundaries align to table grid",
      "neighboring row has normal data cells"
    ],
    "json": [
      "row role is header_row",
      "cell role is column_header",
      "bbox_source is pdf_drawing_grid",
      "source_ids preserve source text lineage"
    ],
    "code_hint": [
      "table/grid evidence must outrank section-heading promotion for table header fragments"
    ]
  },
  "repair_plan": {
    "target_files": [
      "src/structure/table_extractor.rs",
      "src/structure/spatial_table_detector.rs",
      "python/pdf_oxide/presets/applier.py"
    ],
    "invariant": "ruled-grid header cells must remain table cell geometry with column_header labels and must not leak as standalone body/header blocks",
    "focused_regression": "tests/test_nist_page456_control_table_headers.py"
  },
  "validation": {
    "same_page_replay_required": true,
    "overlay_must_match": true,
    "json_label_must_equal": "column_header",
    "source_lineage_required": true,
    "independent_review_required": true
  }
}
```

Expected candidate output:
Return only the following sections, with concrete content:

APPROACH:
The smallest MVP architecture for creator -> reviewer -> repair planner -> patch -> replay -> independent validation.

SCHEMA:
A JSON schema or JSON object shape for the reviewer defect object. It must eliminate prose reinterpretation.

REPAIR_TAXONOMY:
A compact taxonomy of reusable pdf_oxide defect classes. Include this page's class.

CREATOR_REVIEWER_PROTOCOL:
Exact messages/artifacts passed between creator and reviewer. Include pass/fail verdict shape.

PATCH_PLANNER_CONTRACT:
How a defect object maps to candidate files/functions, an invariant, and proof commands.

PAGE456_INSTANCE:
A filled example for this page456 table-header/header-cell case.

PROOF_COMMANDS:
Exact local commands the project agent should run to prove the MVP contract on this case.

VERIFIED_FEATURE:
List only features that the project agent can check locally from the supplied artifacts and repo.

RISKS:
Concrete failure modes and how the MVP fails closed.

BLOCKERS:
Only missing credential, missing artifact, or missing human decision. Do not put ordinary implementation work here.

Forbidden claims:
- Do not claim the immutable pdf_oxide hardening goal is complete.
- Do not claim browser/model output proves extraction correctness.
- Do not propose dashboards or UI.
- Do not propose a broad multi-page campaign before the one-case MVP works.
- Do not ask the human to interpret prose reviewer observations.

Judging criteria:
- executable defect schema quality
- ability to produce accurate bbox and labels from page image + overlay + JSON
- minimal creator-reviewer MVP
- deterministic local proof gates
- clear repair taxonomy
- fail-closed behavior
- compatibility with Tau as orchestrator and pdf_oxide as extraction logic

Stop condition:
The competition phase stops after each competitor returns one MVP proposal or a transport failure receipt. The project agent then scores only receipt-backed, locally checkable features and either picks a winner or reports NEEDS_ATTENTION.
