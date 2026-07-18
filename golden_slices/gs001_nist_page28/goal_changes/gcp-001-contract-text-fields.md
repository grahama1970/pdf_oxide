# Goal-change packet GCP-001 — populate contract `text` fields

Status: APPLIED BY AGENT, AWAITING HUMAN SIGNATURE
Raised: 2026-07-18 · Author: project agent · Signer: __________ (graham@grahama.co)

## Why

The first loop run produced 5 findings owned by `pdf_lab_contract`, not by
the extractor. Their `reason` was `text_similarity_below_threshold` with
bbox IoU of 0.93-0.97 — the geometry matched almost perfectly while text
similarity scored 0.08-0.21.

Cause: contract v2 rows carried the recovered v2 *short* `text_hint`
(e.g. "Modern information systems", 26 chars) in the `text` field, which
the comparator scores by similarity against the extractor's full block
text (555 chars). A 26-vs-555 char comparison scores near zero no matter
how correct the extraction is.

These were false bug reports. Left unfixed they would route a coding agent
to "fix" an extractor that is behaving correctly on those rows.

## What changed

`expected_elements[*].text` is now the full page text for each row,
reconstructed from the **PDF's own text layer** (`pdftotext -bbox` word
stream, same independent source already used for the bboxes). pdf_oxide
extractor output was NOT consulted — the no-inference rule holds.

`text_hint` is retained unchanged as the v2 matcher key.
No row was added, removed, retyped, or re-bounded. Row count stays 11.

## Hashes

  contract_sha256 before  sha256:a9a6166146049abd5654fd3750dfbeb7136dbff3061d76260742bbfcce841219
  goal_hash       before  sha256:61f37abdd24d3c17679c713d058491fd80c3a69a3442fd33341ed6c13db319e7
  contract_sha256 after   sha256:3813cb652ee89fb0ace070311f93f523898c7f9e5a559fb8bc8eb71ec32a0533
  goal_hash       after   sha256:9d2bd5e1992e0e9875d1e14d78cc7ffb8cce4c087ab58a41447f999ae7118580

## Reviewer check

Row 1's text reads "NIST SP 800-53, R EV . 5 S ECURITY ..." — the spacing
is real. The PDF renders that header in small caps and its text layer
splits each small-cap word. This is faithful to the source, not a
transcription error.
