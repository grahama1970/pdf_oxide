Design the smallest executable MVP for a pdf_oxide creator-reviewer loop.

Hard acceptance bar:
The reviewer must output machine-executable defect objects, not prose. The creator/repair planner must consume those objects to target files/functions, patch an invariant, replay the same page, and accept only from local proof. Browser/model output is advisory only.

Why this exists:
pdf_oxide extraction is mostly mature. The failed process was reviewer prose -> vague issue -> human interpretation -> no deterministic repair loop. We need defect JSON -> repair class -> patch target -> proof gate.

One-case packet:
NIST SP 800-53r5 page 456 has a ruled table "TABLE C-1: ACCESS CONTROL FAMILY".
Current overlay labels: page chrome, side chrome, table caption, four column_header cells, and one table region.
Current JSON: 6 top-level blocks; table id actual:p456:table:0; table bbox [0.14666667015723933,0.11371211812953756,0.8525490355647467,0.9040909102468779]; row_count 43; column_count 4; first row role header_row.
Header cells:
1 CONTROL NUMBER, role column_header, source_ids block:5/block:6
2 CONTROL NAME CONTROL ENHANCEMENT NAME, role column_header, source_ids block:7/block:8
3 IMPLEMENTED BY, role column_header, source_ids block:9/block:10
4 ASSURANCE, role column_header, source_ids block:11
Current proof: bbox metrics table_count=1, target_leak_count=0, target_lineage_count=5; focused regression tests/test_nist_page456_control_table_headers.py had 2 passed.

Relevant code areas:
src/structure/table_extractor.rs
src/structure/spatial_table_detector.rs
src/extractors/section_hierarchy.rs
src/extractors/block_merger.rs
python/pdf_oxide/presets/applier.py

Return exactly these sections:
APPROACH
SCHEMA
REPAIR_TAXONOMY
CREATOR_REVIEWER_PROTOCOL
PATCH_PLANNER_CONTRACT
PAGE456_INSTANCE
PROOF_COMMANDS
VERIFIED_FEATURE
RISKS
BLOCKERS

Your schema must include at least:
schema_version, document_id, page_number, candidate_id, source_artifacts, subject.element_id, actual_label, expected_label, actual_bbox, expected_bbox, defect.class, failure_mode, evidence.visual, evidence.json, repair_plan.target_files, repair_plan.invariant, validation.commands, validation.stop_condition.

For PAGE456_INSTANCE, fill one valid defect/contract object for the table-header/header-cell guard. It may be a guard object with actual_label=expected_label=column_header, but it must show how the same schema would represent a regression where a header fragment leaked as body/header text.

Forbidden:
No dashboards. No UI. No broad multi-page campaign. No "ask the human to decide". No claim that this proves pdf_oxide extraction correctness.
