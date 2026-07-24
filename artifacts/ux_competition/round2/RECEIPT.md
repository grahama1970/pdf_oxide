# UX competition winner iteration round 2 — live-data receipt

## Source and engine

- Source PDF: `/mnt/storage12tb/extractor_corpus/inbox/arxiv/1512.03385v1.pdf`
- Pages: `12`
- PDF SHA-256: `1e0651b6810ecba34a3dbc5b5b0209226f889004607c1f203540a48d64e5a93a`
- Engine commit at wheel build/extraction: `b1973e240aaf8f9811cb2affd59af6572a3d8b7d`
- Wheel SHA-256: `1ff4591d8844baf33452990294b721bfb8b368feb6fc107463b5792088967c19`

Exact wheel build and reinstall commands:

```bash
.venv/bin/maturin build --release --interpreter .venv/bin/python
uv pip install --python .venv/bin/python --force-reinstall --no-deps \
  target/wheels/pdf_oxide-0.3.14-cp38-abi3-manylinux_2_34_x86_64.whl
```

Build result:

```text
Finished `release` profile [optimized] target(s) in 1m 43s
Built wheel for abi3 Python ≥ 3.8 to
target/wheels/pdf_oxide-0.3.14-cp38-abi3-manylinux_2_34_x86_64.whl
```

The installed native module resolved to:

```text
.venv/lib/python3.14/site-packages/pdf_oxide/pdf_oxide.abi3.so
```

## Extraction

Exact extraction command:

```bash
.venv/bin/python -m pdf_oxide.pipeline \
  /mnt/storage12tb/extractor_corpus/inbox/arxiv/1512.03385v1.pdf \
  --output-dir artifacts/ux_competition/round2/live \
  --no-arango
```

Extraction result:

```text
Page images: 12 in artifacts/ux_competition/round2/live/page_images
Flattened: 178 chunks
Output: artifacts/ux_competition/round2/live/extracted.json
Annotation call: artifacts/ux_competition/round2/live/annotation_call.json
Done in 5.0s
Extracted 14 tables, 7 figures, 178 chunks in 5.0s
```

The live receipt records 172 blocks, 21 sections, 17 annotation items, and
`{"low_confidence": 17}` as the real reason counts. The emitted section-tree
adapter is `live/section_tree.json`; source metadata and all 12 content-addressed
PNG files are retained under `live/`.

## Live Playwright

Command:

```bash
cd ui
npm run test:e2e:live
```

Result:

```text
Subtest: round 2 drives queue, calibration, retrieval, and missing-image
fail-closed against current-engine artifacts
ok 1
tests 1
pass 1
fail 0
duration_ms 2883.210874
```

The spec copies the real engine output to an isolated mount, validates the
calibration PNG bytes and hash through `server/index.ts`, drives the queue,
calibration, and retrieval routes, then deletes the retrieval element's exact
content-addressed PNG from that mount. A fresh browser context must render
`data-testid="page-image-error"` and no `<img>` before the fail-closed screenshot
is accepted.

Screenshots:

| Receipt | Bytes | SHA-256 |
| --- | ---: | --- |
| `queue.png` | 358603 | `1d84ecfdd76c482d26f9d0ff36dbfb453dc88be0b8aec904bb92b5eb2d513bbb` |
| `calibrate.png` | 387058 | `79e7e5f72149446f7e9c648e34dc3fb2fc6234731b026f3ab2601600e69aad6d` |
| `retrieval.png` | 850737 | `479a823e8294dbba5ccded0b73a5ac619d2fc33a7d118168676bc01c7175fc45` |
| `fail-closed.png` | 198585 | `635bd9cbcb8d7d96a5559a0076d4d8d965aeaefdcc72108470c6924198835298` |

## Deterministic checks

```text
$ npx tsc --noEmit
exit 0

$ npx vitest run
Test Files  4 passed (4)
Tests       15 passed (15)

$ npm run test:e2e
tests 1
pass 1
fail 0
```

Evidence classification:

- mocked: no
- live: yes
- exercised: rebuilt wheel, real 12-page corpus PDF, engine extraction,
  content-addressed page images, exact annotation call, section tree, UI server
  hash validation, all three winning routes, persisted calibration label, and
  missing-image fail-closed behavior
- remains unverified: no claim is made about extraction semantic correctness
  beyond the exact real artifacts visibly rendered in these receipts
