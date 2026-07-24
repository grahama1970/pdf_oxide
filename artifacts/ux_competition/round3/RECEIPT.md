# UX competition winner iteration round 3 — full-corpus scale and label write-through

Verdict: **PASS**

## Exact live mounts

The Playwright spec read the four existing evidence-repository artifacts
directly, copied their bytes into one isolated server mount, and asserted that
each mounted file's SHA-256 matched its source. No annotation-call fixture was
substituted.

Final run mount root:

```text
/tmp/pdf-oxide-round3-live-mount-rTDt8K
```

The isolated mount was removed after the successful run.

| Document/filter ID | Evidence-repo source | Served mount | SHA-256 |
| --- | --- | --- | --- |
| `NIST_SP_800-53r5` | `/home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/NIST_SP_800-53r5/annotation_call.json` | `/tmp/pdf-oxide-round3-live-mount-rTDt8K/annotation-calls/NIST_SP_800-53r5/annotation_call.json` | `b074093d80d515f1d1f2318f69393bc85b886d1cd3ff9144b7f79c92b811b491` |
| `NIST.SP.800-53Ar5` | `/home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/NIST.SP.800-53Ar5/annotation_call.json` | `/tmp/pdf-oxide-round3-live-mount-rTDt8K/annotation-calls/NIST.SP.800-53Ar5/annotation_call.json` | `21e8ebc715f9b3ca53d51a5aaf0b2ed2665aeed384e620b8b95c56238b78d059` |
| `1512.03385v1` | `/home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/1512.03385v1/annotation_call.json` | `/tmp/pdf-oxide-round3-live-mount-rTDt8K/annotation-calls/1512.03385v1/annotation_call.json` | `b19b7167f6147ca03fb954ec77557d9f021c473da54385733b4d5fca7a84c05f` |
| `NASA_SP-2016-6105` | `/home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/NASA_SP-2016-6105/annotation_call.json` | `/tmp/pdf-oxide-round3-live-mount-rTDt8K/annotation-calls/NASA_SP-2016-6105/annotation_call.json` | `5853b2c9c33c134d0dad0b216ef9b7c5f994080a0b78abc9a28ccb4605d22069` |

The server exposed those files together at:

```text
/artifacts/pdf-lab/annotation-calls/NIST_SP_800-53r5/annotation_call.json
/artifacts/pdf-lab/annotation-calls/NIST.SP.800-53Ar5/annotation_call.json
/artifacts/pdf-lab/annotation-calls/1512.03385v1/annotation_call.json
/artifacts/pdf-lab/annotation-calls/NASA_SP-2016-6105/annotation_call.json
```

## Queue counts and scale proof

| Document | Low confidence | Char parity deficit | Reviewer flagged | Total |
| --- | ---: | ---: | ---: | ---: |
| NIST 800-53r5 | 1,219 | 0 | 0 | 1,219 |
| NIST 800-53Ar5 | 268 | 47 | 0 | 315 |
| arXiv `1512.03385v1` | 17 | 1 | 5 | 23 |
| NASA SEH | 598 | 6 | 0 | 604 |
| **Queue total** | **2,102** | **54** | **5** | **2,161** |

Playwright asserted:

- the unfiltered route rendered `2,161 engine-raised items` and `2,161 visible`;
- each document option narrowed the queue to exactly 1,219, 315, 23, and 604;
- the cross-document `char_parity_deficit` filter narrowed to 54;
- NIST 800-53Ar5 plus `char_parity_deficit` narrowed to 47;
- after scrolling the 2,161-item list to the bottom, only 16
  `[data-testid="annotation-row"]` elements existed (asserted `< 60`);
- no confidence-value attribute existed and the live precision value
  `0.44999998807907104` was absent from rendered HTML.

The virtualization assertion is in
`ui/tests/round3-live.playwright.test.mjs` and ran in the successful live test.

The scale pass exposed and repaired two queue defects:

1. virtual scroll now resets when filters replace the row set;
2. page-image lookup is document-strict, with only a matching PDF SHA-256
   allowed as fallback. Visual inspection caught and removed a cross-document
   same-page-number leak that had shown an arXiv page for a NIST row.

## Live label write-through

The test copied the round-2 arXiv live mount, selected two real block items from
its `annotation_call.json`, used their real content-addressed page images, and
served them through `/api/pdf-lab/calibration/sample`.

Before the first click, `calibration/labels_v1.jsonl` had zero rows. Clicking
**Correct** added exactly one row. Its `item_sha` matched the first SHA returned
by the server sample response, its keys were exactly `{item_sha,label,ts}`, and
the timestamp round-tripped through ISO parsing.

Afterward, filling corrected type `figure` and clicking **Wrong type** added
exactly one more row. Its `item_sha` matched the second served item; its keys
were exactly `{item_sha,label,corrected_type,ts}`; `corrected_type` was
non-empty; and its timestamp was valid ISO.

Exact resulting `labels_v1.jsonl`:

```jsonl
{"item_sha":"951e03dc706069b804b93797f06af4adf4840c64aaa647a2bd61a921123a37d8","label":"correct","ts":"2026-07-24T15:08:23.150Z"}
{"item_sha":"9dc2f9efb688e0a82cb4f47da5c31aaa8b725060e266287907941fed97147091","label":"wrong_type","corrected_type":"figure","ts":"2026-07-24T15:08:23.247Z"}
```

The post-write screenshot visibly shows `2/2`, `0 remaining`, the saved
wrong-type status, and the corrected type.

## Commands and outputs

```text
$ cd ui && npx tsc --noEmit
exit 0

$ cd ui && npx vitest run
Test Files  4 passed (4)
Tests       16 passed (16)

$ cd ui && npm run test:e2e:live
tests 1
pass 1
fail 0

$ cd ui && npm run test:e2e:round3
queue total: 2161
per-doc counts: {"NIST_SP_800-53r5":1219,"NIST.SP.800-53Ar5":315,"1512.03385v1":23,"NASA_SP-2016-6105":604}
reason counts: {"char_parity_deficit":54}
virtual rows while scrolled: 16
tests 1
pass 1
fail 0
duration_ms 2447.881764
```

## Screenshot and label artifacts

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `queue-full.png` | 260,677 | `0a981e2ad7144caf90980ad4790a5c88bb2cbbaaeea29e7aeeef4ebb0abb8cec` |
| `queue-filtered.png` | 246,065 | `fab11dd917f2cfbffeda587e2535cc0aa29d222dbc02fbb8d368f400d61d300f` |
| `calibrate-labeled.png` | 498,123 | `b85a7885521a2156551a942179aaec3b2251373b593b0e92a8039fe798037957` |
| `labels_v1.jsonl` | 289 | `2665faec3976ef1070c4d0f2f5ad7995d091c13ee6a75919fc0ff714615742ad` |

Evidence classification:

- mocked: no
- live: yes
- exercised: four evidence-repo annotation calls, exact-source checksum
  mounts, 2,161-item queue, all per-document filters, a reason filter,
  virtualization while scrolled, confidence blinding, two real arXiv
  calibration items, server-side JSONL append, schema checks, and visible
  post-label progress
- remains unverified: this UX proof does not claim that the engine's extraction
  classifications are semantically correct

## Evidence round 3b

Fresh read-back of the four real evidence-repository files:

```text
$ sha256sum /home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/1512.03385v1/annotation_call.json /home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/NASA_SP-2016-6105/annotation_call.json /home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/NIST.SP.800-53Ar5/annotation_call.json /home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/NIST_SP_800-53r5/annotation_call.json
b19b7167f6147ca03fb954ec77557d9f021c473da54385733b4d5fca7a84c05f  /home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/1512.03385v1/annotation_call.json
5853b2c9c33c134d0dad0b216ef9b7c5f994080a0b78abc9a28ccb4605d22069  /home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/NASA_SP-2016-6105/annotation_call.json
21e8ebc715f9b3ca53d51a5aaf0b2ed2665aeed384e620b8b95c56238b78d059  /home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/NIST.SP.800-53Ar5/annotation_call.json
b074093d80d515f1d1f2318f69393bc85b886d1cd3ff9144b7f79c92b811b491  /home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/NIST_SP_800-53r5/annotation_call.json
```

The file contents independently read back as:

| Document | Items | Reason breakdown |
| --- | ---: | --- |
| `1512.03385v1` | 23 | `low_confidence`: 17; `char_parity_deficit`: 1; `reviewer_flagged`: 5 |
| `NASA_SP-2016-6105` | 604 | `low_confidence`: 598; `char_parity_deficit`: 6 |
| `NIST.SP.800-53Ar5` | 315 | `low_confidence`: 268; `char_parity_deficit`: 47 |
| `NIST_SP_800-53r5` | 1,219 | `low_confidence`: 1,219 |

These totals reconcile to 2,161 items: 2,102 `low_confidence`, 54
`char_parity_deficit`, and 5 `reviewer_flagged`.

The round-2 arXiv count of 17 was the `low_confidence` subset of the same
23-item `1512.03385v1` annotation call. The complete reason breakdown is
17 + 1 + 5 = 23. This is not a scope change: the source file is unchanged at
SHA-256
`b19b7167f6147ca03fb954ec77557d9f021c473da54385733b4d5fca7a84c05f`.

Verbatim `artifacts/ux_competition/round3/labels_v1.jsonl`:

```jsonl
{"item_sha":"951e03dc706069b804b93797f06af4adf4840c64aaa647a2bd61a921123a37d8","label":"correct","ts":"2026-07-24T15:08:23.150Z"}
{"item_sha":"9dc2f9efb688e0a82cb4f47da5c31aaa8b725060e266287907941fed97147091","label":"wrong_type","corrected_type":"figure","ts":"2026-07-24T15:08:23.247Z"}
```
