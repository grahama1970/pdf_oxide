Position

REPAIR_CURRENT_GATE: split the single page-28 review into deterministic candidate chunks and merge them before running the unchanged full-page validator.

In scripts/pdf_lab/run_page_second_pass_dag.py, partition the 18 candidates in stable order into three chunks of at most six. Each live call should receive only that chunk’s candidate presets and corresponding extracted blocks, while retaining both page images. Preserve review_request.json as the parent manifest, write per-chunk request/receipt/response/validation artifacts, then create canonical merged review_response.json and run the existing validate_review_response across all 18 findings.

Evidence

The current implementation embeds the complete candidate preset set, complete sanitized page JSON, and both images into one request. It then performs one blocking POST /v1/chat/completions under the single timeout.

Authentication and orchestration succeeded; the failure occurred while awaiting that large one-shot review. Increasing the timeout would relax the 120-second gate, and changing models would change the proven substrate rather than reduce this request’s bounded workload.

The validator must remain unchanged: it correctly rejects inconsistent page and candidate statuses.

Uncertainties

Chunking must not allow partial success. Any missing, duplicated, invalid, or timed-out chunk must preserve blocked_substrate or human_needed; only a complete 18-ID merge may produce reviewed_clean.

The deterministic merge should derive the final page status from the combined candidate findings and then pass the existing validator, rather than trusting conflicting chunk-level page statuses.

Blockers

None for the local repair.

File boundaries:

scripts/pdf_lab/run_page_second_pass_dag.py

tests/test_pdf_lab_page_second_pass_dag.py

Focused proof:

Bash
pytest -q tests/test_pdf_lab_page_second_pass_dag.py -k "chunked_review"

The tests should prove stable partitioning, exact-once coverage of all candidate IDs, fail-closed behavior for any failed chunk, deterministic merge, and successful validation of an all-clean merged response. The existing test module already directly exercises request construction and prompt contracts.

Live acceptance artifact:

artifacts/pdf_lab/live_second_pass_page28_vlm_free2_chunked_orchestrator_live_<UTC>/harness_final_gate.json

It must report ok: true; its page terminal ledger must report reviewed_clean; and the bundle must contain the chunk manifest, every chunk request/response/validation, merged review_response.json, and review_validation.json with all 18 candidate IDs seen exactly once.

<<<WEBGPT_DONE:20260721T114222Z:34f5e7d3>>>_
