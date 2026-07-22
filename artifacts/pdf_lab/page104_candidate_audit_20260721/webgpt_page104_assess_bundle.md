# PDF Lab Page104 Gate Assessment Bundle

## Current gate

Assess one PDF Lab candidate gate for `grahama1970/pdf_oxide`: page104 of
`NIST_SP_800-53r5.pdf`.

Return the required assess deliverable:

```text
DIAGNOSIS
PASS_CURRENT_GATE
```

or:

```text
DIAGNOSIS
BLOCKED_CURRENT_GATE: <one concrete blocker>
```

or:

```text
DIAGNOSIS
REJECTED_SCOPE_EXPANSION
```

## Goal lock

Do not redesign the PDF Lab workflow, propose dashboards, or expand into batch
classification. Decide this one gate only.

## Project objective

The active immutable goal is to harden all PDF Lab page candidates one
page/checklist item at a time. For each candidate, the project agent must use
current extraction evidence, visual evidence, a focused regression before any
patch, deterministic proof, and then commit/push only relevant files.

## Current status

Pages already advanced in this run:

- page456: control-table header regression receipt, pushed.
- page34: sidebar chrome regression receipt, pushed.
- page45: AC-1 list marker regression receipt, pushed.
- page421: glossary term-definition table materialization, visual receipt,
  regression, adjacent regression suite, pushed at `58d4937c103a2bcdcabf06ce526eb83e8dcc5dbf`.

Current next candidate from GS001 handoff:

- page104 had 11 independent reviewer findings before prior work and 3 after.
- Handoff source: `/home/graham/workspace/experiments/pdf_oxide-gs001/local/HANDOFF.md`,
  measured-position table.

## Research context

Brave search was run before this WebGPT call:

```text
query: PDF extraction page break standalone field label Control: list items next page
artifact: artifacts/pdf_lab/page104_candidate_audit_20260721/brave_page_break_field_label_search.json
```

The only relevant result was a Microsoft Q&A page whose snippet says cross-page
field extraction may truncate a value or ignore continuation text on the next
page. The search did not provide a project-specific answer.

## Local evidence

Current branch/worktree:

```text
repo: /tmp/pdf_oxide_page104_1784599760
HEAD: origin/main at 58d4937c103a2bcdcabf06ce526eb83e8dcc5dbf
```

Fresh current extraction artifacts:

```text
artifacts/pdf_lab/page104_candidate_audit_20260721/baseline_page104_snapshot.json
artifacts/pdf_lab/page104_candidate_audit_20260721/baseline_page105_snapshot.json
artifacts/pdf_lab/page104_candidate_audit_20260721/page104_original.png
artifacts/pdf_lab/page104_candidate_audit_20260721/test_logs/baseline_snapshot_page104.log
artifacts/pdf_lab/page104_candidate_audit_20260721/test_logs/baseline_snapshot_page105.log
```

Page104 snapshot summary:

```text
page: 104
pdf_page_index: 103
block_count: 26
type_counts:
  paragraph_block: 12
  section_heading: 6
  header_footer_noise: 4
  list: 2
  reference: 2
```

Potential selected defect:

```json
{
  "id": "actual:p104:block:25",
  "type": "paragraph_block",
  "source_type": "Body",
  "semantic_role": "nist_field_label",
  "text": "Control:",
  "bbox": [0.20588235294117646, 0.8697468150745739, 0.25978078405841504, 0.886674514924637]
}
```

Page105 immediately continues with AU-12 list items:

```json
[
  {
    "id": "actual:p105:block:4",
    "type": "list",
    "source_type": "List",
    "text": "a. Provide audit record generation capability for the event types the system is capable of auditing as defined in AU-2a on [Assignment: organization-defined system components];"
  },
  {
    "id": "actual:p105:block:5",
    "type": "list",
    "source_type": "List",
    "text": "b. Allow [Assignment: organization-defined personnel or roles] to select the event types that are to be logged by specific components of the system; and"
  },
  {
    "id": "actual:p105:block:6",
    "type": "list",
    "source_type": "List",
    "text": "c. Generate audit records for the event types defined in AU-2c that include the audit record content defined in AU-3."
  }
]
```

Relevant ledger rule already present:

```text
entry_id: nist-field-label-001
checklist_item: Keep standalone NIST field labels such as 'Control:' out of the section-heading family.
deterministic_rule: If a NIST Body block text exactly equals a known standalone field label, classify it as paragraph_block with semantic_role=nist_field_label.
```

Therefore `actual:p104:block:25` is already correctly typed by existing ledger
logic. The uncertainty is whether this is still a valid remaining defect because
the label is orphaned at a page break, or whether cross-page grouping is outside
the current per-page snapshot contract.

## Commands already run

```bash
uv run maturin develop
python scripts/pdf_lab/snapshot_current_extraction.py \
  --pdf /mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf \
  --out artifacts/pdf_lab/page104_candidate_audit_20260721/baseline_page104_snapshot.json \
  --max-pages 104 \
  --ledger python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json \
  --apply-mode release
python scripts/pdf_lab/snapshot_current_extraction.py \
  --pdf /mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf \
  --out artifacts/pdf_lab/page104_candidate_audit_20260721/baseline_page105_snapshot.json \
  --max-pages 105 \
  --ledger python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json \
  --apply-mode release
```

## Exact question

Should the project agent select the page104 `Control:` page-break orphan as the
next valid checklist item and write a focused regression/repair for cross-page
field-label grouping, or is that outside the current per-page snapshot contract
and therefore not a safe page104 repair target?

If it is a valid target, name the smallest safe file boundary and the exact
passing condition. If it is not valid, return `BLOCKED_CURRENT_GATE` and name
the missing evidence needed to select one of the 3 remaining page104 findings.

## Acceptance bar

Do not claim the project is complete. This assessment only decides whether this
one page104 candidate is actionable.
