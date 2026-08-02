# Tau Roundtable Join

- topology: `sequential`
- handlers: `1`

## webgpt

- status: `PASS`
- response: `/tmp/pdf_oxide_page45b_1784600709/artifacts/pdf_lab/live_second_pass_page28_vlm_free2_repaired_orchestrator_live_20260721T1128Z/unblock/ask_webgpt_schema/pdf-oxide-page28-schema-webgpt/node-artifacts/handler-webgpt/response.md`

Position

REPAIR_CURRENT_GATE: clarify the review-request contract for generic text candidates. Update scripts/pdf_lab/run_page_second_pass_dag.py; do not change candidate inference and do not weaken validate_review_response.

Add one explicit rule to both build_candidate_presets and build_review_request:

preset_type: text is a broad review stratum, not an expected exact semantic type. A more specific extracted subtype such as section_subtitle is clean-compatible when its text, bbox, and visual role are accurate. Do not report a defect solely because the subtype is more specific than text.

Evidence

The extraction is already correct: the model itself says section_subtitle is more semantically appropriate, while simultaneously returning status: defect. That is a review-contract contradiction, not evidence for changing the extractor or NIST preset.

The current candidate question asks whether evidence agrees with the candidate’s “preset type,” which can imply exact equality. The prompt then tells the model to express a different semantic type through status/rationale/suggested_fix_surface, without distinguishing broad candidate strata from expected semantic labels.

The validator is behaving correctly and must remain unchanged: page_status: clean is invalid when any finding is non-clean.

The focused test boundary is already tests/test_pdf_lab_page_second_pass_dag.py, which directly tests build_candidate_presets and the generated review prompt.

Focused proof command:

Bash
pytest -q tests/test_pdf_lab_page_second_pass_dag.py \
  -k "text_preset_semantic_subtype_contract"

The new regression should assert that the generated prompt and candidate question state that:

text is a generic review stratum;

section_subtitle may be clean-compatible;

subtype specificity alone is not a defect;

page-level and candidate-level status consistency remains mandatory.

Then rerun exactly one repaired page-28 live review and preserve:

artifacts/pdf_lab/page28_text_preset_contract_
