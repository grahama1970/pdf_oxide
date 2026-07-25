# Char-parity bbox localization receipt

Date: 2026-07-25
Verdict: PASS

## Evidence classification

- mocked: no for final verification
- live: yes
- exercised: release wheel build and installation; fresh extraction of all four
  source PDFs with the installed wheel; real `pdftotext` page accounting;
  annotation-call emission; queue-manifest rebuild; contract validation; and
  the deterministic 108-fixture extraction suite
- wiring-only: three focused localization unit cases monkeypatch `pdftotext`
  to exercise block, table-coordinate, and multi-block-union behavior; these
  cases are not the final localization evidence
- remains unverified: no browser-rendering claim is made; the arXiv overlay is
  verified by the same deterministic coordinate projection used by the UI

## Result

All 54 `char_parity_deficit` items carry a positive PDF-space xywh bbox:

```json
{
  "bbox_count": 54,
  "total": 54,
  "localization": {
    "block": 43,
    "blocks": 11,
    "page": 0
  },
  "unlocalized": 0
}
```

There are no unlocalized items to list and no page-level fallbacks.

The queue counts are unchanged:

```json
{
  "total": 2161,
  "char_parity_deficit": 54,
  "reviewer_flagged": 5,
  "low_confidence": 2102
}
```

## arXiv page-4 table proof

Regenerated call:
`artifacts/pdf-lab/annotation-calls/1512.03385v1/annotation_call.json`

The page-4 item is:

```json
{
  "page": 4,
  "bbox": [
    125.97423553466797,
    570.7474975585938,
    370.73443084716797,
    151.12286376953125
  ],
  "localization": "block"
}
```

The matching architectures table is table
`9a68352287179fcfc758e8941eb08c2f` in
`/home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls/1512.03385v1/extracted.json`.
Its extractor bbox is top-left xyxy:

```json
[125.97423553466797, 70.129638671875, 496.70866638183594, 221.25250244140625]
```

The PDF page is `612 × 792`. Projecting the emitted bottom-left xywh bbox into
top-left xyxy yields:

```json
[125.97423553466797, 70.129638671875, 496.70866638183594, 221.25250244140625]
```

The projected annotation and extracted Table 1 geometry are exactly equal.
The table text begins `layer name`, contains the 18/34/50/101/152-layer
architecture columns and the missing x-glyph controls, so this is the requested
architectures-table region.

## Wheel and regeneration

Commands:

```text
.venv/bin/maturin build --release
uv pip install --python .venv/bin/python --force-reinstall target/wheels/pdf_oxide-0.3.14-cp38-abi3-manylinux_2_34_x86_64.whl
.venv/bin/python scripts/regenerate_annotation_calls.py --documents 1512.03385v1 NASA_SP-2016-6105 NIST.SP.800-53Ar5 NIST_SP_800-53r5
cd ui && npx tsx scripts/build-before-main-artifacts.ts
```

Results:

```text
Finished `release` profile [optimized]
Built wheel: target/wheels/pdf_oxide-0.3.14-cp38-abi3-manylinux_2_34_x86_64.whl
Installed pdf-oxide==0.3.14 from that wheel
1512.03385v1: 23 items
NASA_SP-2016-6105: 604 items
NIST.SP.800-53Ar5: 315 items
NIST_SP_800-53r5: 1219 items
```

Final source hashes from `annotation_queue_manifest_v1.json`:

```json
{
  "annotation-calls/1512.03385v1/annotation_call.json": "5fe3461409952d41683241bad7b24ff659f7d7d73ee0ceb957764e83976fe152",
  "annotation-calls/NASA_SP-2016-6105/annotation_call.json": "906c6b598e882df72b096009df53707342cdd3c1809831ce1a443aacdda5173e",
  "annotation-calls/NIST_SP_800-53r5/annotation_call.json": "b5c67871b1fbd4867955bdc13c165aa8cc1eeb5fcfa3b00d1512143d751312c5",
  "annotation-calls/NIST.SP.800-53Ar5/annotation_call.json": "bdca6e5d32b440fde7ab1420fded85fa00a364560e4906c4684ce0ef107724d7"
}
```

## Gates

108-fixture suite:

```text
.venv/bin/python -m pytest tests/test_rollback_fixtures.py -q
........................................................................ [ 66%]
....................................                                     [100%]
108 passed in 418.13s (0:06:58)
```

Focused installed-wheel contract tests:

```text
.venv/bin/python -m pytest tests/test_annotation_call.py -q
13 passed in 0.18s
```

Lint:

```text
.venv/bin/ruff check python/pdf_oxide/annotation_call.py tests/test_annotation_call.py
All checks passed!
```

Independent artifact-contract validator:

```text
uv run --with jsonschema python scripts/validate_before_main_contracts.py
{"priority_counts":{"char_parity_deficit":54,"low_confidence":2102,"reviewer_flagged":5,"total":2161},"queue_items":2161,"schemas_checked":8,"status":"PASS"}
```

## Per-item localization

| # | Document | Page | Localization | Bbox (PDF bottom-left xywh) |
|---:|---|---:|---|---|
| 1 | `1512.03385v1` | 4 | `block` | `[125.97423553466797,570.7474975585938,370.73443084716797,151.12286376953125]` |
| 2 | `NASA_SP-2016-6105` | 17 | `blocks` | `[84.719970703125,593.9398803710938,448.08355712890625,43.703125]` |
| 3 | `NASA_SP-2016-6105` | 29 | `blocks` | `[72.0,72.0,518.3151092529297,679.260009765625]` |
| 4 | `NASA_SP-2016-6105` | 30 | `block` | `[72.0,72.0,301.5,679.260009765625]` |
| 5 | `NASA_SP-2016-6105` | 33 | `block` | `[98.58000183105469,150.63999938964844,253.62717956542969,532.7800140380859]` |
| 6 | `NASA_SP-2016-6105` | 292 | `blocks` | `[89.15985107421875,72.0,439.5001220703125,695.280029296875]` |
| 7 | `NASA_SP-2016-6105` | 295 | `blocks` | `[97.08000183105469,72.0,479.15985107421875,228.0]` |
| 8 | `NIST.SP.800-53Ar5` | 23 | `block` | `[90.78813934326172,307.6169738769531,430.32166290283203,277.9708557128906]` |
| 9 | `NIST.SP.800-53Ar5` | 24 | `block` | `[90.84127807617188,323.732177734375,430.1773376464844,302.03717041015625]` |
| 10 | `NIST.SP.800-53Ar5` | 25 | `blocks` | `[90.78450775146484,202.888916015625,430.4455337524414,370.71185302734375]` |
| 11 | `NIST.SP.800-53Ar5` | 26 | `blocks` | `[90.0,270.78790283203125,433.803466796875,485.57208251953125]` |
| 12 | `NIST.SP.800-53Ar5` | 27 | `block` | `[90.78630828857422,119.60530090332031,430.47217559814453,480.1864471435547]` |
| 13 | `NIST.SP.800-53Ar5` | 28 | `block` | `[90.84126281738281,288.03961181640625,430.17723083496094,210.7510986328125]` |
| 14 | `NIST.SP.800-53Ar5` | 46 | `blocks` | `[72.0,244.59449768066406,714.5736410522461,331.76548767089844]` |
| 15 | `NIST.SP.800-53Ar5` | 116 | `block` | `[90.0,465.6499938964844,432.0,254.35000610351562]` |
| 16 | `NIST.SP.800-53Ar5` | 192 | `block` | `[90.0,139.54995727539062,432.0,580.4500427246094]` |
| 17 | `NIST.SP.800-53Ar5` | 210 | `block` | `[90.0,499.54998779296875,432.0,220.45001220703125]` |
| 18 | `NIST.SP.800-53Ar5` | 221 | `block` | `[90.0,597.5499877929688,432.0,122.45001220703125]` |
| 19 | `NIST.SP.800-53Ar5` | 224 | `block` | `[90.0,617.9500122070312,432.0,102.04998779296875]` |
| 20 | `NIST.SP.800-53Ar5` | 261 | `block` | `[89.75,648.1499633789062,467.37001647949216,73.85003662109375]` |
| 21 | `NIST.SP.800-53Ar5` | 305 | `block` | `[90.0,148.60000610351562,432.0,455.5500183105469]` |
| 22 | `NIST.SP.800-53Ar5` | 306 | `blocks` | `[90.0,445.5,432.0,274.5]` |
| 23 | `NIST.SP.800-53Ar5` | 310 | `block` | `[90.0,371.25,432.0,348.75]` |
| 24 | `NIST.SP.800-53Ar5` | 378 | `block` | `[90.0,499.54998779296875,432.0,220.45001220703125]` |
| 25 | `NIST.SP.800-53Ar5` | 386 | `block` | `[90.0,468.54998779296875,432.0,251.45001220703125]` |
| 26 | `NIST.SP.800-53Ar5` | 400 | `block` | `[90.0,477.6499938964844,432.0,242.35000610351562]` |
| 27 | `NIST.SP.800-53Ar5` | 401 | `block` | `[90.0,374.1499938964844,432.0,345.8500061035156]` |
| 28 | `NIST.SP.800-53Ar5` | 402 | `block` | `[90.0,465.6499938964844,432.0,254.35000610351562]` |
| 29 | `NIST.SP.800-53Ar5` | 403 | `block` | `[90.0,142.44998168945312,432.0,577.5500183105469]` |
| 30 | `NIST.SP.800-53Ar5` | 422 | `blocks` | `[90.0,111.45000457763672,432.0006103515625,645.7500076293945]` |
| 31 | `NIST.SP.800-53Ar5` | 423 | `block` | `[90.0,346.4499816894531,432.0,373.5500183105469]` |
| 32 | `NIST.SP.800-53Ar5` | 425 | `block` | `[90.0,560.75,432.0,159.25]` |
| 33 | `NIST.SP.800-53Ar5` | 429 | `block` | `[90.0,540.3499755859375,432.0,179.6500244140625]` |
| 34 | `NIST.SP.800-53Ar5` | 431 | `block` | `[90.0,617.9500122070312,432.0,102.04998779296875]` |
| 35 | `NIST.SP.800-53Ar5` | 433 | `block` | `[90.0,550.5499877929688,432.0,169.45001220703125]` |
| 36 | `NIST.SP.800-53Ar5` | 448 | `block` | `[90.0,440.8499755859375,432.0,279.1500244140625]` |
| 37 | `NIST.SP.800-53Ar5` | 501 | `block` | `[90.0,535.6500244140625,432.0,184.3499755859375]` |
| 38 | `NIST.SP.800-53Ar5` | 512 | `block` | `[90.0,185.14999389648438,432.0,534.8500061035156]` |
| 39 | `NIST.SP.800-53Ar5` | 519 | `block` | `[90.0,430.3499755859375,432.0,289.6500244140625]` |
| 40 | `NIST.SP.800-53Ar5` | 552 | `block` | `[90.0,538.5499877929688,432.0,181.45001220703125]` |
| 41 | `NIST.SP.800-53Ar5` | 564 | `blocks` | `[29.149999618530273,247.56300354003906,492.8500003814697,472.43699645996094]` |
| 42 | `NIST.SP.800-53Ar5` | 566 | `block` | `[90.0,217.60000610351562,432.0,276.54998779296875]` |
| 43 | `NIST.SP.800-53Ar5` | 592 | `block` | `[90.0,560.75,432.0,159.25]` |
| 44 | `NIST.SP.800-53Ar5` | 601 | `block` | `[90.0,509.75,432.0,210.25]` |
| 45 | `NIST.SP.800-53Ar5` | 602 | `block` | `[89.75,660.1499633789062,467.37001647949216,61.85003662109375]` |
| 46 | `NIST.SP.800-53Ar5` | 610 | `block` | `[90.0,384.3499755859375,432.0,335.6500244140625]` |
| 47 | `NIST.SP.800-53Ar5` | 611 | `block` | `[90.0,375.6499938964844,432.0,344.3500061035156]` |
| 48 | `NIST.SP.800-53Ar5` | 613 | `block` | `[90.0,482.04998779296875,432.0,237.95001220703125]` |
| 49 | `NIST.SP.800-53Ar5` | 654 | `block` | `[90.0,385.8499755859375,432.0,334.1500244140625]` |
| 50 | `NIST.SP.800-53Ar5` | 655 | `block` | `[90.0,595.75,432.0,124.25]` |
| 51 | `NIST.SP.800-53Ar5` | 664 | `block` | `[90.0,336.25,432.0,383.75]` |
| 52 | `NIST.SP.800-53Ar5` | 665 | `block` | `[90.0,518.1500244140625,432.0,201.8499755859375]` |
| 53 | `NIST.SP.800-53Ar5` | 668 | `block` | `[90.0,493.3499755859375,432.0,226.6500244140625]` |
| 54 | `NIST.SP.800-53Ar5` | 730 | `blocks` | `[114.86229705810547,236.6846923828125,594.3813400268555,230.1466064453125]` |
