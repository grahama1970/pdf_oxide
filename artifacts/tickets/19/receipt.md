# Ticket #19 proof receipt

## Parent HEAD RED proof

- Parent HEAD: `89f8d7b2`
- Wheel build: `uv run --with 'maturin>=1.0,<2.0' maturin develop --release`
- Test command: `.venv/bin/python -m pytest -q tests/test_issue_19_table_dimensions.py`
- mocked: no
- live: yes
- exercised: the pinned `1512.03385v1.pdf` fixture, the parent Rust engine wheel, and system `pdftotext`

```text
F                                                                        [100%]
=================================== FAILURES ===================================
__________ test_table_1_preserves_dimension_glyphs_shown_by_pdftotext __________

>       assert not missing, (
            f"Table 1 is missing {sum(missing.values())} dimension glyphs: "
            f"{dict(missing)!r}; expected={dict(expected)!r}, actual={dict(actual)!r}"
        )
E       AssertionError: Table 1 is missing 16 dimension glyphs: {'\x14': 8, '\x15': 8}; expected={'\x14': 8, '\x15': 8}, actual={}
E       assert not Counter({'\x14': 8, '\x15': 8})

tests/test_issue_19_table_dimensions.py:63: AssertionError
=========================== short test summary info ============================
FAILED tests/test_issue_19_table_dimensions.py::test_table_1_preserves_dimension_glyphs_shown_by_pdftotext
1 failed in 0.09s
```

This proves the parent wheel retains the visible `112×112`, `56×56`,
`28×28`, `14×14`, and `7×7` values but drops all 16 font-mapped control
glyphs that `pdftotext` emits for the same Table 1 region.

## Fix

The embedded Type1 font declares codes 20 and 21 as
`bracketleftbigg`/`bracketrightbigg`, but those non-standard glyph names have
no Adobe Glyph List mapping. The engine now retains the raw code only when an
embedded Type1 encoding positively declares such an otherwise-unmapped glyph.
All three simple-font `Tj`/`TJ` paths retain that resolved font mapping.
Unresolved raw control bytes remain filtered.

## Fix HEAD GREEN proof

- Wheel build:
  `uv run --with 'maturin>=1.0,<2.0' maturin develop --release`
- Test command:
  `.venv/bin/python -m pytest -q tests/test_issue_19_table_dimensions.py`
- mocked: no
- live: yes
- exercised: the rebuilt Rust extension, the pinned PDF, system `pdftotext`,
  raw page extraction, and the pipeline's 29-by-10 Table 1 cell payload

```text
.                                                                        [100%]
1 passed in 2.48s
```

## Rust unit proof

Command:

```text
cargo test --lib test_parse_type1_encoding_preserves_declared_unknown_glyph_codes
cargo test --lib test_decode_text_to_unicode_filters_control_chars
```

Result:

```text
test fonts::type1_encoding::tests::test_parse_type1_encoding_preserves_declared_unknown_glyph_codes ... ok
test fonts::text_decode::tests::test_decode_text_to_unicode_filters_control_chars ... ok
```

The first check exercises the explicit embedded-font provenance fallback. The
second confirms unresolved raw control characters are still filtered.

## 108-fixture rollback gate

Command:

```text
.venv/bin/python -m pytest -q tests/test_rollback_fixtures.py
```

Result:

```text
........................................................................ [ 66%]
....................................                                     [100%]
108 passed in 381.55s (0:06:21)
```

## Annotation-call regeneration proof

The call was generated into a temporary file in the destination directory,
validated with `validate_annotation_call`, flushed, and installed with
`os.replace`; the destination directory was then flushed. The post-replace
check returned:

```text
{
  "output": "artifacts/pdf-lab/annotation-calls/1512.03385v1/annotation_call.json",
  "engine_commit": "2e7b3f4bbb413cac39992b03542eaa46b4078f4a",
  "items": 22,
  "p4_char_parity_items": 0,
  "table_1_dimension_glyphs": {
    "U+0014": 8,
    "U+0015": 8
  },
  "schema_valid": true,
  "atomic_replace": true
}
```

This proves the regenerated p4 Table-1 `char_parity_deficit` item is absent.
The wheel was rebuilt and this artifact was regenerated after committing the
engine/test change, so `engine_commit` identifies source that contains the fix:

```text
2e7b3f4b engine: fix #19 preserve Type1 table glyphs
annotation_call.json sha256:
755518cfa09925a0f870e6de99d7f06faea5633830e2439a1f4104768765f3fa
```

## Evidence scope

- mocked: no
- live: yes
- actually exercised: live local PDF bytes, `pdftotext`, rebuilt Rust/Python
  wheel, raw extraction, table extraction, annotation-call generation and
  schema validation, and all 108 rollback fixtures
- remains unverified: PDFs with other non-AGL embedded Type1 glyph names outside
  the 108-fixture suite
