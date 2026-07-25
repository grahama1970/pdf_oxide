# Ticket #19 independent review

## Extraction-path verifier

Verdict: PASS

- Re-ran
  `.venv/bin/python -m pytest -q tests/test_issue_19_table_dimensions.py`:
  `1 passed in 2.41s`.
- Validated `pdf_oxide.annotation_call.v1`: 22 items, zero global or p4
  `char_parity_deficit` items.
- Confirmed parent `89f8d7b2` discarded the two unknown embedded Type1 glyph
  names and filtered their raw C0 fallback, matching the receipt's 8+8 RED.
- Confirmed the current test exercises the live Table-1 cell payload and the
  patch is limited to embedded Type1 mapping and three resolved-font extraction
  paths.

mocked: no

live: yes

## Adversarial verifier

Initial verdict: FAIL because the first regenerated artifact named the
pre-fix base commit. Required repair: commit the engine/test change, rebuild,
and regenerate with that fix-bearing commit.

Final verdict after repair: PASS

- Confirmed commit
  `2e7b3f4bbb413cac39992b03542eaa46b4078f4a` contains exactly the ticket's
  two engine files and red test.
- Confirmed the current engine/test paths match that commit byte-for-byte.
- Confirmed the regenerated annotation call declares that exact full SHA.
- Independently validated the annotation schema, 22-item count, and zero p4
  `char_parity_deficit` items.
- Confirmed the receipt records parent RED, fix GREEN, Rust unit checks,
  108/108 fixtures, atomic regeneration, and U+0014/U+0015 counts of 8/8.

mocked: no

live: yes (independent Git, schema, artifact, and regression checks; the
six-minute fixture suite was inspected from its recorded output rather than
rerun)

## Final reviewer verdict

PASS
