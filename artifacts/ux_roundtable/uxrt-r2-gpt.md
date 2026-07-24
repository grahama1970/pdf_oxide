Worked for 1m 35s
Position
YAML
decision: HOLD_MAIN
disagreement_resolution:
  D1_bulk:
    decision: "No semantic bulk correction."
    rationale: "Only 59 items are actionable before calibration."
    permitted_later: "Staged status-only batches with predicate preview, count confirmation, audit receipt, and undo token."
  D2_retrieval_shape:
    decision: "One canonical answer with grouped multi-evidence."
    prohibited: "Ranked alternative answers."
    rendering: "One original page image per page, numbered overlays for all supporting elements."
  D3_page_images:
    decision: "Deterministic render-on-first-retrieval; no requirement to persist all 1,593 pages before UI merge."
    invariant: "The renderer must materialize and hash-verify every cited image before releasing an answer."
    failure: "Render failure returns HTTP 422 with answer:null; no fallback URL."
dissent_to_operator: []
Before main
Order	Slice	Owner	Concrete artifact	Machine-checkable acceptance
1	UXR2-BM01 Durable calibration events	codex-loop	contracts/calibration_label_event_v1.schema.json; append-only store; POST/GET /api/pdf-lab/calibration/events; deterministic labels_v1.jsonl projection	Duplicate idempotency key adds zero rows; unknown/stale item_sha returns 409; undo appends revision_of; projection contains exactly one active label per item
2	UXR2-BM02 Calibration resume/undo/skip	ui-loop	CalibrateRoute.tsx session upgrade; keyboard map; server cursor plus local cache	Stop after item 50, clear browser state, reload, and resume at the first unresolved item; undo restores the prior label; skip writes no label; mis-tap correction does not inflate completed count; confidence remains absent from DOM
3	UXR2-BM03 Annotation decision ledger	codex-loop	contracts/annotation_decision_event_v1.schema.json; /api/pdf-lab/annotation-decisions; separate JSONL ledger	Calibration labels and queue decisions never share a store; stale call hash rejected; retries are idempotent; amendments require valid revision_of; corrected bounds/type validate against the controlled vocabulary
4	UXR2-BM04 Calibrated queue partition	project-agent-script	annotation_queue_manifest_v1.json with actionable, held, policy hash and source hashes	Current fixture produces exactly actionable=59, held=2102; default API/UI returns only 59; held items cannot become actionable without a passing signed calibration report
5	UXR2-BM05 Cross-view workflow store	ui-loop	Shared queue/calibration state provider; adjudication badges; auto-advance and reload restoration	Saving a decision updates queue badges without full reload; reload preserves filter, cursor and status; virtualized list remains below 60 item rows; missing-image items disable all adjudication controls
6	UXR2-BM06 Human-throughput receipt	ui-loop	ux_timing_event_v1.jsonl; deterministic 50-item Playwright workload	Zero dropped or duplicate writes; accept/defer workload reaches at least 120 items/hour; mixed workload containing 20% type/bounds corrections reaches at least 60 items/hour; results identify fixture hash and UI commit
7	UXR2-BM07 Manifest, image and bbox integrity	codex-loop	document_mount_manifest_v1.schema.json; page_image_manifest_v1.schema.json; bbox_space_v1.schema.json	Artifact mounts require valid hashes; two PDFs with the same page index never cross-resolve; PNG binds PDF SHA, page, crop box, rotation, DPI, dimensions and renderer version; rotated/cropped overlay goldens differ by at most 2 px
8	UXR2-BM08 Retrieval contract freeze	codex-loop	retrieval_answer_v1.schema.json with one answer, evidence_groups[], exact section binding and nullable vector_provenance	Schema rejects evidence without a verified page image, provenance, element ID or exact section binding; multiple elements on one page reference one image; ranked alternative-answer arrays are invalid; vector_provenance:null is valid before indexing
Before any >=95% accuracy claim
Order	Slice	Owner	Concrete artifact	Machine-checkable acceptance
9	UXR2-AC01 Pre-registered acceptance policy	human	Signed accuracy_denominator_v1.json and calibration_acceptance_policy_v1.json	Signature verifies; policy fixes population, strata, inclusion probabilities, accuracy dimensions, confidence method, ECE ceiling, precision floor and minimum coverage before labels are evaluated
10	UXR2-AC02 Weighted calibration report	project-agent-script	calibration_report_v1.json; reliability plot; queue-unlock receipt	Uses stratum population and inclusion weights; emits ECE and bootstrap interval plus per-bin precision intervals; suppresses underpowered per-document/type claims; duplicate/superseded labels are excluded; low-confidence queue unlocks only when signed policy passes
11	UXR2-AC03 External accuracy evaluation	project-agent-script	accuracy_report_v1.json with source-independent witnesses and false-negative audit	Report references the signed denominator; evaluates text, element existence, type, bounds, order and hierarchy separately; includes missed-element sampling; >=95% is emitted only when the policy-defined lower confidence bound is at least 0.95
12	UXR2-AC04 Claim enablement	human	Signed accuracy_claim_acceptance_v1.json	Signature names exact engine, UI, calibration and evaluation hashes; without it, API and UI expose measured values only and never the >=95% claim
Integration
Order	Slice	Owner	Concrete artifact	Machine-checkable acceptance
13	UXR2-INT01 Unified /extractor API	codex-loop	skills/extractor/contracts/openapi.v1.yaml; extract/job/tree/annotation/query/page-image endpoints	UI and CLI call the same API; fixture-mode artifact URLs are not used in production; identical query input yields the same canonical response hash from UI and agent client
14	UXR2-INT02 On-demand original-page renderer	codex-loop	Content-addressed render service and page_render_receipt_v1.json	Cold-cache retrieval synchronously creates and verifies the cited image before returning HTTP 200; renderer failure returns 422 with answer:null; tampered cached PNG is rejected or regenerated; no fabricated or document-agnostic fallback exists
15	UXR2-INT03 Corpus renderability audit	project-agent-script	page_renderability_audit_v1.json covering all 1,593 pages	Every page renders from the hash-pinned source PDF and validates its manifest; temporary unflagged renders may be deleted after verification; result must be 1593/1593 PASS before production retrieval is enabled
16	UXR2-INT04 Multimodal Qdrant materialization	codex-loop	Named text/image vectors; deterministic point IDs; qdrant_index_receipt_v1.json	No point is written without exact section binding and verified image SHA; tables/figures receive visual vectors; model and preprocessing hashes populate formerly nullable vector_provenance; repeated indexing produces identical IDs
17	UXR2-INT05 Grouped evidence UX	ui-loop	Section/tree query surface and RetrievalEvidenceView.tsx upgrade	A section query may return N supporting elements but exactly one answer; evidence is grouped by page; each page image appears once with numbered overlays; table evidence renders cells; figure evidence renders crop and caption; no answer renders before every cited image loads
18	UXR2-INT06 Sparta thin shim	project-agent-script	experiments/sparta/extractor_skill/client.py; Explorer adapter	Sparta performs no direct PDF extraction, image synthesis or Qdrant query; golden section/table/figure queries match /extractor response hash, point IDs, element IDs, section IDs and image SHAs; 422 propagates without replacement evidence
19	UXR2-INT07 End-to-end operator acceptance	human	Signed extractor_goal_acceptance_v1.json	Names exact repository SHAs and replay receipts for one section, one table and one figure query; each replay contains Qdrant provenance, hierarchy path, element/bbox trace and original page image; all fail-closed negative tests pass
Evidence
YAML
bulk:
  actionable_now: 59
  held_uncalibrated: 2102
  conclusion: "Semantic bulk correction has no pre-main justification."

retrieval:
  required_shape: "one answer, potentially many supporting elements"
  trust_rule: "multiple evidence objects may compare; competing answer candidates may not"

page_images:
  current_persisted_scope: "flagged pages"
  remaining_pages: 947
  resolution: "verify renderability corpus-wide; persist lazily on first retrieval"
  hard_invariant: "no verified image, no answer"

calibration:
  sample_size: 100
  limitation: "insufficient for arbitrary per-document and per-type precision claims"
  required_control: "pre-registered weighted analysis and signed claim threshold"
Uncertainties
ID	Required resolution
UXR2-U01	Operator may revise the mixed-task throughput floor after observing the first signed 50-item pilot; the measured result must remain recorded even if the threshold changes
UXR2-U02	The 100-item sample may leave sparse quintile/document cells; the report must merge or suppress them rather than report unstable precision
UXR2-U03	Cold render latency is not yet characterized; record p50/p95 by document family without weakening the no-image/no-answer rule
UXR2-U04	OCR/scanned-family rendering and extraction remain separate qualification work; page-image success alone must not count as extraction accuracy
Blockers
YAML
main_merge_blockers:
  - UXR2-BM01
  - UXR2-BM02
  - UXR2-BM03
  - UXR2-BM04
  - UXR2-BM05
  - UXR2-BM06
  - UXR2-BM07
  - UXR2-BM08

accuracy_claim_blockers:
  - UXR2-AC01
  - UXR2-AC02
  - UXR2-AC03
  - UXR2-AC04

agent_goal_blockers:
  - UXR2-INT01
  - UXR2-INT02
  - UXR2-INT03
  - UXR2-INT04
  - UXR2-INT05

final_sparta_blockers:
  - UXR2-INT06
  - UXR2-INT07

DISSENT-TO-OPERATOR: none

ROUNDTABLE_UXR2_COMPLETE

ChatGPT is AI and can make mistakes. Check important info.

Extra High