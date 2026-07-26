# PDF Lab Visual Creator-Reviewer Challenge: page_0456 (degraded text lane)

You are one external participant in a competition to unblock pdf_oxide. The ideal task uses binary UX files, but the current multi-provider ask/surf path cannot attach them. Work from the exact visual/evidence summary below and produce a concrete creator-reviewer loop for accurate bounding boxes and labels over the page.

## Immutable Goal
Harden all PDF Lab page candidates one page/checklist item at a time. Preserve visual evidence, create or update focused regression before patching, fix extraction behavior, run deterministic audit, and advance only when the current candidate is proven or explicitly blocked with receipt artifacts.

## Actual Goal Of This Challenge
Accurate bounding boxes with correct labels over the PDF page. GitHub issues are only the reviewer-to-creator mechanism.

## UX Evidence Files In Local Bundle
- page.png: actual page image, 918x1188. It shows one large ruled table titled TABLE C-1: ACCESS CONTROL FAMILY.
- bbox_overlay.png: overlay, 918x1188. It shows one large table bbox plus many cyan line-level boxes inside the table and cyan boxes around running headers/footer/chrome.
- candidate_bundle.json: page=456, fix_error_count=95, first fix requests include line ids actual:p456:line:2 CONTROL, line:52 CONTROL NAME, line:106 ASSURANCE, line:98 IMPLEMENTED, line:3 NUMBER. Current type section_header; requested family table.
- release_extraction_blocks.json: includes table block actual:p456:table:0 with bbox [0.14666667015723933,0.11371211812953756,0.8525490230984158,0.9040908813476562]. Also includes standalone blocks inside the table such as CONTROL, CONTROL NAME, ASSURANCE, IMPLEMENTED, NUMBER, BY, AC-1, Policy and Procedures, O.
- focused regression: tests/test_nist_page456_control_table_headers.py currently passes with `1 passed`, asserting exactly one table and no standalone leaked header/cell blocks for current extraction.

## Where The Agent Has Been Spiralling
1. Treating Git hygiene and stale artifact management as the project goal.
2. Comparing abstract workflows instead of making bounding boxes and labels accurate on the PDF page.
3. Letting stale artifacts and old handoffs compete with fresh extraction receipts.
4. Treating reviewer prose as proof instead of visual overlay plus JSON plus regression evidence.
5. Failing to make reviewer tickets exact enough for a creator: bbox, block id, current label, expected label, owner, proof command.

## Guardrails
- Do not propose source phrase, page number, or control-ID classifier shortcuts.
- Do not claim page456 or the immutable goal is complete.
- Do not propose dashboards or Git hygiene as the solution.
- Reviewer tickets must name bbox/region, block id, current label, expected label, extraction symptom, owner, and proof command.
- If stale overlay/JSON disagree with fresh extraction, require a freshness receipt before patching.

## Required Output
Return exactly these sections:
1. POSITION
2. SOURCE_DERIVED_STEP_MODEL: numbered steps, each labeled IMPLEMENTED, INTENDED, or MISSING.
3. BBOX_LABEL_REVIEW_MODEL: how reviewer decides whether a bbox/label is correct.
4. CREATOR_REVIEWER_GITHUB_LOOP: numbered workflow where reviewer files issue/checklist items and creator fixes them.
5. ISSUE_ITEM_SCHEMA: fields for each reviewer-filed ticket/checklist item.
6. FIRST_IMPLEMENTATION_SLICE: smallest next local action in pdf_oxide.
7. VERIFIED_FEATURES: prefix locally checkable features with VERIFIED_FEATURE:.
8. PROOF_COMMANDS: exact commands/artifacts the project agent can run/check.
9. RISKS_AND_ANTI_SPIRAL_GUARDS.
10. ROUND2_REQUEST: what evidence you need next.
