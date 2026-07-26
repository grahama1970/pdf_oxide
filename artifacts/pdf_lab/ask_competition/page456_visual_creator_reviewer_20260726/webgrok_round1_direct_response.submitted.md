PDF-OXIDE PAGE456 CREATOR-REVIEWER COMPETITION ROUND 1 RETRY

Context:
- Project: pdf_oxide, a Rust/Python PDF extraction pipeline.
- Immutable project goal remains NOT_MET: harden all PDF Lab page candidates one page/checklist item at a time with deterministic proof.
- Active candidate: NIST SP 800-53r5 page 456, page image 918x1188.
- Human priority: accurate extraction and accurate bounding boxes with correct labels over the PDF page, not git/process theater.

Visual facts from the PDF Lab UX:
- The page contains one large ruled table titled "TABLE C-1: ACCESS CONTROL FAMILY".
- Table bbox in normalized page coordinates: [0.14666667015723933, 0.11371211812953756, 0.8525490230984158, 0.9040908813476562].
- Column header labels visible in the top row: CONTROL NUMBER, CONTROL NAME, IMPLEMENTED BY, ASSURANCE.
- Current candidate bundle reports 95 fix errors.
- Representative bad extracted blocks:
  - actual:p456:line:2 text "CONTROL" current_type section_header requested_family table
  - actual:p456:line:3 text "NUMBER" current_type section_header requested_family table
  - actual:p456:line:52 text "CONTROL NAME" current_type section_header requested_family table
  - actual:p456:line:98 text "IMPLEMENTED" current_type section_header requested_family table
  - actual:p456:line:106 text "ASSURANCE" current_type section_header requested_family table
- The desired behavior is not just "remove headings"; the extractor/UX must show table/header/cell boxes with correct labels over the page.
- A focused local regression currently passes, which conflicts with stale PDF Lab UX artifacts. The loop must force fresh page image, fresh bbox overlay, fresh extraction JSON, and fresh regression receipt before claiming any patch works.

Challenge:
Propose a working creator-reviewer loop using GitHub issues for this page. The creator proposes or patches pdf_oxide extraction. The reviewer inspects the PDF Lab UX evidence and files concrete GitHub tickets for the creator to address. Optimize for accurate bounding boxes and labels over the page.

Guardrails:
- No claims of done/green without deterministic local proof artifacts.
- No batch advancement until page456 has fresh visual/JSON/regression receipts.
- Reviewer model prose is advisory; local extraction and visual overlay are authority.
- Tickets must be concrete: target block ids, expected bbox/label behavior, command to prove it, and failure signature.

Return exactly:
1. Your diagnosis of where the project-agent has been spiraling.
2. The creator-reviewer loop you recommend.
3. The exact first GitHub issue the reviewer should file for page456.
4. The first local proof command and artifact outputs the creator must produce.
5. How to score this against other model proposals in rounds 2 and 3.

---

Automation-only instruction: answer the user's request normally. Do not mention,
quote, summarize, or explain this automation instruction. After your complete
answer, append a final line containing only this exact marker:

<<<GROK_DONE:20260726T125233Z:fbfceb25>>>

Do not print anything after that marker.
