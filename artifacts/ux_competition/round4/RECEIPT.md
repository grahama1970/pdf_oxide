# UX Competition Round 4R Receipt

## Operational result

- Bare `/`, `#pdf-lab/annotations`, and unknown hashes open the auto-discovered annotation queue.
- The queue mounts all four annotation calls: **2,161 true items**.
- `#pdf-lab/calibrate` auto-loads the discovered sample and its sibling page-image index; the cold walk received HTTP **201** from `POST /api/pdf-lab/calibration/labels`.
- `#pdf-lab/evidence` auto-loads the discovered retrieval result. The UI supports a result picker when discovery returns more than one.
- Legacy views are available only under `#pdf-lab/legacy/*` and are absent from the primary navigation.
- Relative page-image hrefs resolve from the served location of each `page_images_v1.json`; a live relative image request returned HTTP **200**.

## Mounts response sample

Full response: `mounts-response.json`

```json
{
  "artifacts_root": "/home/graham/workspace/experiments/pdf_oxide-p34-coder/artifacts/pdf-lab",
  "annotation_calls": [
    {"doc_id": "NASA_SP-2016-6105", "item_count": 604},
    {"doc_id": "NIST_SP_800-53r5", "item_count": 1219},
    {"doc_id": "NIST.SP.800-53Ar5", "item_count": 315},
    {"doc_id": "1512.03385v1", "item_count": 23}
  ],
  "page_image_indexes": [
    {"document_ids": ["NASA_SP-2016-6105"], "page_count": 195},
    {"document_ids": ["NIST.SP.800-53Ar5"], "page_count": 88},
    {"document_ids": ["NIST_SP_800-53r5"], "page_count": 363}
  ],
  "retrieval_results": [
    {"url": "/artifacts/pdf-lab/round4_retrieval_result.json"}
  ],
  "calibration_samples": [
    {
      "url": "/artifacts/pdf-lab/calibration/sample_v1.jsonl",
      "page_image_index_url": "/artifacts/pdf-lab/calibration/page_images_v1.json",
      "labels_endpoint": "/api/pdf-lab/calibration/labels"
    }
  ]
}
```

## Bounded page-image generation

Command:

```bash
.venv/bin/python scripts/render_annotation_page_images.py
```

Renderer: engine wheel binary
`.venv/lib/python3.14/site-packages/pdf_oxide/pdf_oxide.abi3.so`,
SHA-256 `9ce6c511e9d03f2354d2393570eee9e86cf793557b24340488378ffd6da073b7`,
96 DPI PNG.

| Document | Annotation items | Unique referenced pages | PNGs | Source PDF SHA-256 |
|---|---:|---:|---:|---|
| NIST_SP_800-53r5 | 1,219 | 363 | 363 | `fc63bcd61715d0181dd8e85998b1e6201ae3515fc6626102101cab1841e11ec6` |
| NIST.SP.800-53Ar5 | 315 | 88 | 88 | `75665570048b969ad465a4f4f1db425ce505c374951c2c64e462949c6b21be47` |
| NASA_SP-2016-6105 | 604 | 195 | 195 | `b8e28d127226e12fa758cd90ecdd1d0831fe4db20647d0fb5c1fc6e40f4c9657` |

Total: **646/646 referenced pages rendered**. Nothing remains for the three
requested corpora. Full generation receipt: `page-image-generation.json`.
Every emitted href is relative, for example
`page_images/55bd6d7ea7f87a3aca6158f2f3af28060b66c5757cda8492b2cab8170d68904f.png`.

## Verification commands

```text
$ cd ui && npm run typecheck
> tsc --noEmit
PASS

$ cd ui && npm test
Test Files  4 passed (4)
Tests       17 passed (17)

$ cd ui && npm run test:e2e:round4
vite build: PASS (1,780 modules transformed)
round 4 cold walk discovers the front-door artifact mounts: PASS
tests 1, pass 1, fail 0

$ .venv/bin/python -m py_compile scripts/render_annotation_page_images.py
PASS

$ git diff --check
PASS
```

Cold-walk screenshots:

- `bare-root.png` — populated 2,161-item queue with a mounted original page
- `calibrate.png` — discovered sample, rendered page, bounding box, persisted label
- `annotations.png` — explicit annotations route with true total
- `evidence.png` — traceable result with section path, provenance, and mounted page

The test explicitly rejects visible `Unexpected token`, `JSON.parse`,
`SyntaxError`, and `is not valid JSON` surfaces on every walked route.

## Evidence scope

- mocked: **no**
- live: **yes**
- exercised: the real Express mounts scanner, built React application, local
  artifact HTTP serving, native wheel page renderer, page-image URL resolution,
  calibration label write, and fresh Playwright browser context
- remains unverified: no requested Round 4R behavior remains unverified

## Evidence round 6

### Retrieval auto-discovery repair

The bare `#pdf-lab/evidence` route now receives a complete, co-located evidence
mount. Artifact pairing selects a candidate in the retrieval result's own
directory first, then falls back to matching PDF SHA-256 and explicit document
identity. The mounts contract now carries both sibling URLs, and `App.tsx`
passes the discovered section tree to the retrieval route:

```json
{
  "url": "/artifacts/pdf-lab/round2-live/retrieval_result.json",
  "page_image_index_url": "/artifacts/pdf-lab/round2-live/page_images_v1.json",
  "section_tree_url": "/artifacts/pdf-lab/round2-live/section_tree.json"
}
```

Focused unit coverage exercises co-location priority over an earlier
same-document/same-PDF candidate and both PDF/document fallback paths:

```text
$ cd ui && npm test
Test Files  5 passed (5)
Tests       19 passed (19)
```

The extended cold walk requires the discovered retrieval mount to contain the
co-located page-image index and section tree, then requires the bare evidence
route to show `Traceable answer`, load the original-page image, and contain
neither `Retrieval evidence withheld` nor `Retrieval evidence needs attention`.

```text
$ cd ui && node --test tests/round4-cold-walk.test.mjs
tests 1, pass 1, fail 0
```

Full output: `../round6/cold-walk.txt`.
Visual proof: `../round6/evidence-bare.png`. The inspected screenshot shows the
traceable answer, `Deep Residual Learning for Image Recognition` section path,
source element `6b42bcd6dc407b7e9542a7dde41d2b8a`, original page 0, and its visible
bounding-box overlay.

### Live read-backs after rebuild on port 3013

`GET /api/pdf-lab/mounts` returned:

```json
{
  "annotation_calls": 4,
  "annotation_items": 2161,
  "page_image_indexes": 5,
  "retrieval_results": 1,
  "calibration_samples": 1
}
```

The live cold walk also read back:

- bare `/`: the populated **2,161-item** queue with an original page and bbox;
- bare `#pdf-lab/calibrate`: blinded adjudication with a mounted page image;
- bare `#pdf-lab/evidence`: the discovered traceable result, section tree,
  original page, and bbox, without the fail-closed withheld state.

The active round2-live calibration directory was exposed at the server's
existing `/calibration` fixture path so the unchanged cold-walk label-write
assertion could receive its expected HTTP **201**.

### Round 5 primary-source re-verification

Commit `8047af0e3a03173312ff13fee97818e38653ade4` was read directly.
At that commit, the five component sources contain:

| Component | interactive | `data-qid` | `data-qs-action` | `useRegisterAction` |
|---|---:|---:|---:|---:|
| `App.tsx` | 1 | 1 | 1 | 1 |
| `AnnotationQueueRoute.tsx` | 6 | 6 | 6 | 6 |
| `CalibrateRoute.tsx` | 8 | 8 | 8 | 8 |
| `RetrievalEvidenceView.tsx` | 1 | 1 | 1 | 1 |
| `NormalizedPageOverlay.tsx` | 1 | 1 | 1 | 1 |

Every counted control has a `title`; every component imports and calls
`useRegisterAction`. `RetrievalEvidenceView.tsx` retains five
`data-testid` occurrences. The committed Round 5 manifest contains **134**
literal `data-qid` references; all **33** recursively enumerated `selector`
fields begin with `[data-qid=`, with **0** non-qid selector fields.

### Round 4R primary-source re-verification

Commit `de3011990e56d5e95147f166f3b640e55ac21b13` was read directly:

- commit stat: **677 files**, **42,068 insertions**, 92 deletions;
- mounts endpoint: `ui/server/index.ts:213`;
- relative href resolution: `ui/src/adapters/pageImageRefs.ts:64-66`,
  using `new URL(filename, normalizedBase)` for absolute bases and `new URL`
  against the normalized local base for relative paths;
- cold-walk raw parse rejection: `ui/tests/round4-cold-walk.test.mjs:15`,
  asserted by `assertNoRawParseError`;
- bounded renderer: `scripts/render_annotation_page_images.py`, **307 lines**;
- NASA receipt: **195/195** referenced pages rendered from source SHA-256
  `b8e28d127226e12fa758cd90ecdd1d0831fe4db20647d0fb5c1fc6e40f4c9657`.

### Round 6 proof scope

- mocked: **no**
- live: **yes**
- exercised: co-located and identity-fallback artifact pairing, mounts JSON
  parsing, production build, the real Express scanner on `:3013`, real mounted
  Round 2 artifacts, full browser cold walk, original image load, visible bbox,
  and screenshot inspection
- remains unverified: none of the requested Round 6 bugfix or Rounds 5/4R
  evidence-closure checks
