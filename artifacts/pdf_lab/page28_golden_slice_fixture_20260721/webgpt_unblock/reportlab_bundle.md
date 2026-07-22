# PDF Oxide Page28 Runner Dependency Triage

## Current Gate

Make `tests/test_nist_page_28_regression.py` run past setup using recovered expected-elements fixtures without changing expected labels to chase current output.

## Evidence

- The old setup failure was missing `/tmp/pdf-lab-golden-slices/nist_page_28_printed_page_1/expected_elements_v2.json`.
- Recovered fixture files exist in a sibling GS001 repo and were copied into this repo under `tests/fixtures/golden_slices/gs001_nist_page28/`.
- The test was updated to prefer `/tmp` but fall back to the committed fixture.
- After adding `loguru` to the dev dependency group, page28 setup now fails at:

```text
ModuleNotFoundError: No module named 'reportlab'
```

- The import path is `build_golden_slice_bundle.py -> from pdf_oxide.presets.applier import ... -> pdf_oxide.presets.__init__ -> pdf_oxide.presets.tables -> from reportlab.lib import colors`.

## Research Context

Brave search for `Python package imports optional reportlab ModuleNotFoundError pyproject dependency` returned generic results saying the module must be installed in the active Python environment. No project-specific public result was found.

## Question

For this gate, is the narrow local repair to declare `reportlab` in the development/test dependency group so the existing runner can import the current package, or should the package import be changed to avoid importing table/reportlab code when importing `pdf_oxide.presets.applier`?

Return one ruling:

- `PASS_CURRENT_GATE`
- `BLOCKED_CURRENT_GATE: <one concrete blocker>`
- `REJECTED_SCOPE_EXPANSION`
