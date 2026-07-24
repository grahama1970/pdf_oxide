# UX Solution Review Roundtable — Converged Manifest (2026-07-24)

Panel: webgpt (GPT-5.6 Sol Extra High), webclaude (Opus 4.8 High), webkimi (K2.6 Instant High).
2 rounds, identical context per round; between-round research: industry bbox-verification
throughput 50-150 items/hr under strict QC (datavlab.ai, taskmonk); calibrated-triage /
selective-prediction literature (arXiv 2606.15910) — deferral queues require calibrated confidence.
Transport: direct browser submission (surf drift); all six responses SHA-256-verified on extraction.

## Unanimous verdict
HOLD main promotion until the before-main slices land. The built UX is contract-faithful
(all three seats) but machine-correctness receipts do not cover human workflow or several
trust-surface contracts.

## Disagreements resolved (unanimous by round 2)
- D1 BULK: prohibit semantic bulk pre-main. Actionable queue is only 59 items
  (54 char_parity + 5 reviewer_flagged); the 2,102 low_confidence items are HELD until a
  signed calibration report passes (selective-prediction grounding). Staged mechanical
  bulk (count-confirmed, undoable, audit predicate) may return later where throughput matters.
- D2 RETRIEVAL SHAPE: grouped multi-evidence — exactly one answer; evidence grouped by page;
  each page image rendered once with numbered overlays; tables render cells; ranked
  alternative-answer lists are schema-invalid.
- D3 PAGE IMAGES: no 947-page pre-render. On-demand render-on-first-retrieval,
  content-addressed on first serve, hash-verified before HTTP 200, 422 on render failure;
  pre-warm the 59 actionable pages; full 1,593-page renderability audit gates production
  retrieval (before embeddings), not main.

## Canonical slice manifest
Adopt GPT round-2 as canonical (uxrt-r2-gpt.md): UXR2-BM01..BM08 before main
(durable calibration events; calibrate resume/undo/skip; separate annotation-decision
ledger; calibrated queue partition actionable=59/held=2102; cross-view store; human-throughput
receipt >=120/hr accept-defer and >=60/hr mixed; manifest/image/bbox integrity schemas;
retrieval contract freeze with nullable vector_provenance), UXR2-AC01..AC04 before any
accuracy claim (signed denominator + acceptance policy; weighted calibration report;
external accuracy evaluation with lower-CI>=0.95 rule; signed claim enablement),
UXR2-INT01..INT07 for integration (/extractor unified API; on-demand renderer;
1593/1593 renderability audit; multimodal qdrant with deterministic point IDs;
grouped-evidence UX; sparta thin shim; end-to-end operator acceptance).
Claude round-2 (M-slices) and Kimi round-2 (P/A-slices) concur on all before-main items.

## Surviving dissent (to operator)
1. claude: set throughput acceptance >=90/hr correction-adjusted rather than a flat 120/hr
   (largely addressed by GPT BM06's dual-tier 120/60 bar — confirm which stands).
2. kimi: cross-page section queries may need a comparison-grid variant later (defer to
   Sparta shim iteration); confirm numbered-overlay legibility above ~5 elements/page
   (zoom/pagination if exceeded).
3. gpt: none.
