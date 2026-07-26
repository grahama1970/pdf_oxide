# PDF Lab Visual Creator-Reviewer Challenge: page_0456

You are one external participant in a competition to unblock the pdf_oxide project. Do not give generic process advice. Work from the attached/page evidence and produce a concrete creator-reviewer loop for improving PDF extraction.

## Immutable Goal
Harden all PDF Lab page candidates one page/checklist item at a time. For the current page/checklist item, preserve visual evidence, create or update the focused regression before patching, fix extraction behavior, run deterministic audit, and advance only when the current candidate is proven or explicitly blocked with receipt artifacts.

The immutable goal is NOT complete. This competition is only to choose a better creator-reviewer loop for the active case.

## Challenge
The active candidate is NIST SP 800-53r5 page_0456. The goal is accurate bounding boxes with correct labels over the page, tied to actual pdf_oxide extraction JSON. A reviewer should inspect the page image/overlay/JSON and file actionable GitHub issue checklist items for the creator to address. The creator then fixes pdf_oxide extraction and the reviewer rechecks both rendered overlay and JSON.

## UX Evidence Files
The attached bundle or referenced evidence contains:
- page.png: the actual PDF page image.
- bbox_overlay.png: current bbox/label overlay.
- candidate_bundle.json: page/candidate metadata, fix_error_requests, copied file paths.
- release_extraction_blocks.json: current extracted table/block JSON for the page.
- test_nist_page456_control_table_headers.py: focused regression.
- test_output.txt: latest focused regression output.
- ux_issue_summary.json: compact summary of visible/current fix requests.

## Where the agent has been spiralling
1. Treating Git hygiene and stale artifact management as the project goal.
2. Comparing abstract workflows instead of making bounding boxes and labels accurate on the PDF page.
3. Letting stale artifacts and old handoffs compete with fresh extraction receipts.
4. Treating model/reviewer prose as proof instead of visual overlay + JSON + regression evidence.
5. Failing to keep reviewer tickets tied to exact page regions, block ids, current labels, expected labels, and proof commands.

## Guardrails
- Do not propose source phrase, page number, or control-ID classifier shortcuts.
- Do not claim page456 or the immutable goal is complete.
- Do not propose dashboards or Git hygiene as the solution.
- A reviewer ticket must name exact evidence: bbox/region, block id if available, current label, expected label, extraction symptom, owner, and proof command.
- Accurate bbox/label overlay is a required human-facing proof surface, not decoration.
- If stale artifacts disagree with fresh extraction, require a freshness receipt before patching.

## Known Local Evidence
- The visual page is one large ruled table titled TABLE C-1: ACCESS CONTROL FAMILY, with control-number, control-name, implemented-by, and assurance columns.
- The overlay shows table coverage plus many line-level boxes inside the table; these can be duplicate child/cell boxes or wrong standalone labels depending on current extraction.
- A focused local test currently passes: `PYTHONPATH=python pytest -q tests/test_nist_page456_control_table_headers.py` -> `1 passed`.
- Older artifacts still show duplicate standalone leaks such as CONTROL, NUMBER, CONTROL NAME, IMPLEMENTED, BY, ASSURANCE, and row cells outside table structure.
- Therefore the loop must reconcile visual overlay, fresh extraction JSON, and stale artifacts before deciding whether to patch page456 or advance to the next checklist item.

## Required Output
Return exactly these sections:
1. POSITION
2. SOURCE_DERIVED_STEP_MODEL: numbered steps, each labeled IMPLEMENTED, INTENDED, or MISSING.
3. BBOX_LABEL_REVIEW_MODEL: how the reviewer uses page.png, bbox_overlay.png, and JSON to decide whether a bbox/label is correct.
4. CREATOR_REVIEWER_GITHUB_LOOP: numbered workflow where reviewer files issue/checklist items and creator fixes them.
5. ISSUE_ITEM_SCHEMA: fields required for each reviewer-filed ticket/checklist item.
6. FIRST_IMPLEMENTATION_SLICE: smallest next local action in pdf_oxide.
7. VERIFIED_FEATURES: only locally checkable features; prefix each with VERIFIED_FEATURE:.
8. PROOF_COMMANDS: exact commands/artifacts the project agent can run/check.
9. RISKS_AND_ANTI_SPIRAL_GUARDS.
10. ROUND2_REQUEST: what evidence you need next.

Your answer should optimize for extracting PDFs correctly, not for a nice process diagram.

---

Completion contract for browser automation:

At the very end of your final answer, print exactly:

<<<WEBGPT_DONE:20260726T123031Z:5d77143a>>>

Do not print anything after that marker.
