# UX Competition Scorecard — Round 1

Judged against `BRIEF.md` as submitted, before synthesis repairs. Scores are
0–10. “Completeness” includes whether the supplied files typecheck and test
together, not whether the idea could be repaired.

## Scores

| Entry | Contract fidelity | Completeness | Verification-first UX | Test realism | Total / 40 |
|---|---:|---:|---:|---:|---:|
| codex | 7.0 | 5.0 | 6.0 | 6.0 | 24.0 |
| claude | 8.0 | 6.5 | 8.5 | 7.5 | 30.5 |
| **gpt** | **8.5** | **8.0** | **9.5** | **9.0** | **35.0** |
| kimi | 4.0 | 3.0 | 5.0 | 3.0 | 15.0 |

## Evidence by entry

### codex

Contract strengths are concentrated in calibration and artifact integrity. The
server hashes the exact sample row — `"item_sha: createHash('sha256').update(line).digest('hex')"`
(`codex-entry.diff:192-207`) — and independently verifies PNG bytes and the
canonical content-addressed filename (`codex-entry.diff:234-260`). Label writes
reject unknown item hashes (`codex-entry.diff:324-338`). The section adapter
requires the v2 schema and checks unique IDs/order/provenance
(`codex-entry.diff:714-765`).

The annotation adapter is not exact: it accepts any non-empty `kind` and
`reason`, while making optional brief fields mandatory — `"typeof item.kind !==
'string'"`, `"typeof item.reason !== 'string'"`, and `"text_excerpt is required"`
(`codex-entry.diff:491-504`). Most importantly, the submitted diff implements
the calibrate slice but contains no `RetrievalEvidenceView` and no virtualized
annotation queue. Its live Playwright case is unusually strong for the part it
does cover: it checks visible source image, confidence leakage, exact overlay
percentages, exact label keys, persisted SHA, and a corrupted-manifest 422
(`codex-entry.diff:1314-1362`). It cannot satisfy the full acceptance set because
there is no missing-retrieval-page-image hard-fail or 2,161-item queue test.

Rationale: Codex is the best live calibration/backend graft, but it is a partial
S2 implementation rather than the complete S2/S9 competition deliverable.

### claude

Claude mirrors the annotation fields and carries every top-level call field into
the presentation model: `"pdfSha256"`, `"engineCommit"`, `"accuracyBasis"`, and
`"accuracyValue"` (`claude/entry.md:441-481`). Its section adapter exposes cycle
and total-order checks (`claude/entry.md:537-570`), and its confidence component
never prints the numeric value: `"withheld from the DOM entirely"` and
`data-confidence-hidden="true"` (`claude/entry.md:606-623`).

The strongest UX detail is its explicit source-image contract error:
`data-testid="page-image-error"` with the text `"this result cannot be verified
and must be treated as a failure"` (`claude/entry.md:640-679`). It also separates
pure image geometry from display scaling (`claude/entry.md:271-308`) and uses a
dependency-free windowed list (`claude/entry.md:737-797`). Tests check the three
retrieval surfaces, explicit missing-image error, exact overlay pixels,
confidence non-disclosure, and positive/negative label rows
(`claude/entry.md:1482-1508`, `1518-1532`, `1555-1580`, `1589-1608`).

Completeness is lower because the artifact is a monolithic response rather than
an applied tree, supplies no route/App integration or dependency manifest, and
its page-image adapter accepts unvalidated short identifiers
(`claude/entry.md:499-523`; its own fixture uses `"deadbeef"` at line 1479).
The missing-image test proves a loud component state but does not call a
contract assertion with `toThrow`.

Rationale: Claude has the best forensic presentation primitives outside the
winner, but requires integration work and stronger content-address validation.

### gpt — WINNER

GPT is the most exact and complete base. Annotation kinds and reasons are closed
sets, and invalid values throw (`gpt/ui/src/adapters/annotationCall.ts:57-64`,
`148-160`). Its section-tree adapter consumes every v2 field and checks
parent/child reciprocity, acyclicity, depth, unique `doc_order`, single-PDF
provenance, and block ownership
(`gpt/ui/src/adapters/sectionTree.ts:112-245`). Page images must be PNGs named
exactly `<sha256>.png`, and explicit SHA/filename disagreement throws
(`gpt/ui/src/adapters/pageImageRefs.ts:67-133`).

The retrieval normalizer refuses answers with no evidence, invalid PDF SHA,
missing element IDs, missing source images, or missing section paths
(`gpt/ui/src/components/retrieval/RetrievalEvidenceView.tsx:89-145`). The rendered
view includes the required breadcrumb and provenance test IDs and maps every
source image through the shared overlay
(`gpt/ui/src/components/retrieval/RetrievalEvidenceView.tsx:169-220`). The queue
actually windows its rows (`start`, `end`, and `visible = rows.slice(...)`) and
provides document/reason/kind/search filters
(`gpt/ui/src/components/annotation/AnnotationQueueRoute.tsx:78-105`,
`168-205`).

Its acceptance test is the only submission that directly asserts the mandatory
hard failure: `"expect(() => assertRetrievalEvidence(...)).toThrow(/missing
original page image/)"` (`gpt/ui/src/components/verification/VerificationUx.test.tsx:50-53`).
The same file asserts required retrieval test IDs, overlay percentages,
confidence absence, exact label keys, and fewer than 40 rendered rows for a
2,161-item call (`.../VerificationUx.test.tsx:41-115`).

Two original-entry defects prevent perfect scores. Calibration declared
`quintile: string` and tested `"q2"` rather than the live integer stratum
(`gpt/ui/src/adapters/calibration.ts:5-15`;
`gpt/ui/src/components/verification/VerificationUx.test.tsx:66-76`). It also
fed mature extraction/retrieval boxes through an xywh normalizer
(`gpt/ui/src/components/retrieval/RetrievalEvidenceView.tsx:109-117`) instead of
isolating the existing xyxy presentation contract. Finally, the supplied
Vitest setup did not clean the DOM between cases and the strict TypeScript run
rejected an untyped zero-argument mock; both were found by running the entry,
not inferred from style.

Rationale: GPT wins because it supplies the full routed workflow, strongest
fail-closed retrieval behavior, strictest adapters, real 2,161-item
virtualization, and the only test suite that directly encodes every acceptance
clause. Its defects were local synthesis repairs, not missing product slices.

### kimi

Kimi renders the requested breadcrumb and provenance fields
(`kimi/entry.md:1044-1077`) and attempts a virtual queue. However, missing page
images are silently fabricated from a fallback URL
(`kimi/entry.md:1033-1037`, `1091-1094`), directly violating “missing page image
= test failure.” Its test named “fails” merely asserts that no image exists and
therefore passes the missing state (`kimi/entry.md:1431-1436`).

Confidence is not opaque: tests deliberately find `"Confidence:"` text under
the hidden attribute (`kimi/entry.md:1481-1486`), and the calibration test
requires the secret value `"0.65"` to remain in DOM text
(`kimi/entry.md:1579-1587`). Label tests check only truthiness rather than exact
keys or the closed schema (`kimi/entry.md:1546-1567`), and the bbox test checks
that text contains `"0.1"` rather than verifying projected coordinates
(`kimi/entry.md:1570-1577`). The artifact also repeats large source sections and
includes visible drafting commentary such as `"I added a fallback URL"` and
`"If they need config, they can add it"` (`kimi/entry.md:1646-1667`), so it is
not a clean drop-in tree.

Rationale: Kimi contains recognizable screens, but its fallback, DOM leakage,
weak assertions, and duplicated response artifact fail the core verification
contract.

## Best-parts inventory and synthesis decision

| Source | Specific part | Decision |
|---|---|---|
| gpt | `adapters/annotationCall.ts`, `sectionTree.ts`, generic content-addressed `pageImageRefs.ts` | Winning adapter base; retained and repaired at the mature xyxy boundary. |
| gpt | `RetrievalEvidenceView`, `AnnotationQueueRoute`, `CalibrateRoute`, `NormalizedPageOverlay`, `VerificationUx.css` | Winning routed UI base. |
| gpt | `VerificationUx.test.tsx` plus the three adapter tests | Winning acceptance-test base; repaired test cleanup/types and expanded live-coordinate checks. |
| codex | `server/index.ts::verifiedCalibrationPageImages` and strict labels endpoint | **Grafted/retained**: byte hash, canonical image identity, exact label fields, known-item SHA validation. |
| codex | `tests/calibrate.playwright.test.mjs` | **Grafted/expanded**: live server/browser calibration proof now also exercises retrieval, missing-image fail-closed behavior, and writes two screenshots. |
| claude | `PageImageOverlay` fail-loud `data-testid="page-image-error"` state | **Grafted** into the winner’s retrieval contract-failure surface and asserted in Vitest/Playwright. |
| claude | Pure coordinate-space separation idea from `lib/geometry.ts` | **Grafted in contract-specific form**: annotation xywh stays isolated while calibration/retrieval xyxy is converted by dedicated adapter code before shared overlay rendering. |
| kimi | Fallback page URL, sr-only confidence text, response/test structure | Not grafted; these are contract violations or incomplete proof. |

## Declared winner

**WINNER: gpt (GPT-5.6 Sol Extra High).**

The landed synthesis uses GPT’s complete verification-first route set and
acceptance suite, keeps Codex’s stronger live artifact/label integrity layer,
and adopts Claude’s explicit source-image failure marker and coordinate-boundary
discipline. It does not carry forward Kimi’s fallback image or confidence text.
