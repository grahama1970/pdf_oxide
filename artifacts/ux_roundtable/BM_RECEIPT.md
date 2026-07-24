# UX Roundtable Before-Main Receipt

Verdict: **PASS**

Operator correction applied: all 2,161 flagged items are servable in the priority order `char_parity_deficit` (54), `reviewer_flagged` (5), then `low_confidence` (2,102). No queue item is held behind calibration, a report, or a signature.

Implementation commit: `282a2c4d204674d8f08101ac57af4d4768db05bf`

## Slice evidence

- BM01/BM02: calibration events are append-only at `artifacts/pdf-lab/calibration/events_v1.jsonl`; `labels_v1.jsonl` is the deterministic active-label projection. Live Playwright labeled 50 items, cleared browser state, resumed at item 51, restored a prior label through undo, and confirmed skip wrote nothing.
- BM03: annotation decisions use the separate `artifacts/pdf-lab/annotation_decisions_v1.jsonl` store. The API test exercised stale call/item rejection, idempotent retry, controlled corrected types/bounds, and `revision_of` amendments.
- BM04′/BM05: `annotation_queue_manifest_v1.json` contains all 2,161 items with source hashes and exact priority counts. Queue decisions update badges immediately; filters, cursor, and status survive reload; the list is virtualized; missing-image controls are disabled.
- BM06: `BM06_THROUGHPUT_RECEIPT.json` and `ux_timing_event_v1.jsonl` contain two deterministic 50-item browser workloads. Accept/defer measured 41,025.11 items/hour; mixed with 10 corrections measured 34,794.14 items/hour. There were 100 decision writes, 100 timing writes, zero dropped writes, and zero duplicate event IDs.
- BM07: mount, page-image, and bbox schemas validate. The live API rendered and content-addressed a mounted page, hash-verified repeat serving, and returned 422 for an unknown mount. Crop/rotation overlay fixtures for 0/90/180/270 degrees are within two rendered pixels.
- BM08: `retrieval_answer_v1.schema.json` accepts one answer with page-grouped evidence, nullable vector provenance, verified content-addressed page images, and numbered overlays. Ranked answer arrays and unverified page images are rejected. The cold walk rendered each evidence-group page image once through the hash-verifying retrieval endpoint.

## Proof commands

- `python3 scripts/validate_before_main_contracts.py` → 8 schemas, 2,161 queue items, 1 evidence group, PASS.
- `npm --prefix ui run typecheck` → PASS.
- `npm --prefix ui test -- --run` → 5 files, 21 tests, PASS.
- `node --test ui/tests/before-main-api.test.mjs` → PASS.
- `node --test ui/tests/bm02-calibration-resume.playwright.test.mjs` → PASS.
- `BM06_UI_COMMIT=282a2c4d204674d8f08101ac57af4d4768db05bf node --test ui/tests/bm06-throughput.playwright.test.mjs` → both tiers PASS.
- `node --test ui/tests/round4-cold-walk.test.mjs` → PASS.
- `rg -n -i "calibration[_ -]?report|signed[_ -]?denominator|passing[_ -]?signed|claim[_ -]?certif|held[_ -]?reasons|low[_ -]?confidence[_ -]?unlock" ui/server ui/src contracts ui/scripts scripts/validate_before_main_contracts.py ui/tests` → zero matches.

## Evidence scope

mocked: no

live: yes

Actually exercised: local API server, append-only filesystem stores, mounted real PDFs, Poppler page rendering, content/hash verification, Chromium workflows, reload/local-storage behavior, 100 decision and timing writes, schema rejection cases, TypeScript compilation, and React rendering tests.

Unverified: external production deployment and concurrent multi-process append locking are outside this before-main implementation round.
