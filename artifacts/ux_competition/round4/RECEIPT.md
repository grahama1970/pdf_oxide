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
