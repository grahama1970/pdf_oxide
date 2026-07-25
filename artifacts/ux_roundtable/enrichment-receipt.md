# Annotation-call enrichment + text-correction ledger receipt

Date: 2026-07-25
Result: PASS

## Evidence classification

- mocked: no for final verification
- live: yes
- exercised: release Rust/Python wheel compilation and installation; fresh extraction
  of all four source PDFs with the installed wheel; page-scoped `pdftotext`
  oracle accounting; four annotation-call writes; queue-manifest rebuild; the
  108-fixture extraction suite; a real HTTP POST/GET decision round trip against
  the spawned UI server and a temporary JSONL ledger.
- wiring-only check: one focused Python unit test monkeypatches `pdftotext` to
  exercise a controlled two-character derivation. It is not used as final
  evidence; the regenerated artifacts below use the real `pdftotext` binary.
- remains unverified: no browser rendering claim is made by this data-layer
  change.

## Release wheel

Commands:

```text
.venv/bin/maturin build --release
uv pip install --python .venv/bin/python --force-reinstall target/wheels/pdf_oxide-0.3.14-cp38-abi3-manylinux_2_34_x86_64.whl
.venv/bin/python -c 'import pdf_oxide; print(pdf_oxide.__version__); print(pdf_oxide.__file__)'
```

Result:

```text
Finished `release` profile [optimized] target(s) in 2m 02s
Built wheel: target/wheels/pdf_oxide-0.3.14-cp38-abi3-manylinux_2_34_x86_64.whl
Installed pdf-oxide==0.3.14 from that wheel
0.3.14
.venv/lib/python3.14/site-packages/pdf_oxide/__init__.py
```

## Fresh regeneration

The final artifacts were generated from fresh engine extraction, not from
cached or pinned extraction JSON:

```text
.venv/bin/python scripts/regenerate_annotation_calls.py --documents 1512.03385v1 > /tmp/pdf-oxide-enrichment-regeneration-arxiv.log 2>&1
.venv/bin/python scripts/regenerate_annotation_calls.py --documents NASA_SP-2016-6105 NIST.SP.800-53Ar5 NIST_SP_800-53r5 > /tmp/pdf-oxide-enrichment-regeneration.log 2>&1
cd ui && npx tsx scripts/build-before-main-artifacts.ts
```

Regenerated call item totals:

```text
1512.03385v1:       23
NASA_SP-2016-6105: 604
NIST.SP.800-53Ar5: 315
NIST_SP_800-53r5: 1219
```

Final queue manifest:

```json
{
  "total": 2161,
  "char_parity_deficit": 54,
  "reviewer_flagged": 5,
  "low_confidence": 2102
}
```

Independent contract validator:

```text
uv run --with jsonschema python scripts/validate_before_main_contracts.py
{"priority_counts":{"char_parity_deficit":54,"low_confidence":2102,"reviewer_flagged":5,"total":2161},"queue_items":2161,"schemas_checked":8,"status":"PASS"}
```

## Sample enriched arXiv page-4 item

Source:
`artifacts/pdf-lab/annotation-calls/1512.03385v1/annotation_call.json`

The call carries:

```json
{
  "engine_name": "pdf-oxide",
  "engine_version": "0.3.14",
  "engine_commit": "d6d4af7993248a88a0f002e1e2f76eef3385dc7d"
}
```

The page-4 parity item carries:

```json
{
  "page": 4,
  "kind": "region",
  "reason": "char_parity_deficit",
  "missing_chars": 16,
  "text_excerpt": "Table 1. Architectures for ImageNet. Building blocks are shown in brackets ...",
  "oracle_excerpt": "layer name output size\nconv1\n112×112\n...\n\u0014\n\u0015\n\u0014\n\u0015\n...",
  "missing_text": "\u0014\u0015\u0014\u0015\u0014\u0015\u0014\u0015\u0014\u0015\u0014\u0015\u0014\u0015\u0014\u0015"
}
```

The item is region-scoped, so no block bbox is asserted. The schema preserves
and validates a bbox when the emitting deficit is block-localized.

## Gates

108-fixture engine suite:

```text
.venv/bin/python -m pytest tests/test_rollback_fixtures.py -q
........................................................................ [ 66%]
....................................                                     [100%]
108 passed in 403.85s (0:06:43)
```

Focused Python contract tests:

```text
.venv/bin/python -m pytest tests/test_annotation_call.py -q
10 passed in 0.16s
```

TypeScript and UI/API tests:

```text
cd ui && npm run typecheck
tsc --noEmit
PASS

cd ui && npm test
Test Files  6 passed (6)
Tests      25 passed (25)
```

The Vitest total includes
`persists corrected_text through POST and returns it through GET`, which starts
the real server, writes `corrected_text` through
`POST /api/pdf-lab/annotation-decisions`, and reads it back through GET.
