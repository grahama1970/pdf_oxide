# Pass-1 Output Contract v1 (FROZEN 2026-07-22)

Panel precondition (roundtable 2026-07-22): this contract is frozen and
versioned BEFORE any Pass-2/shadow-harness code is written. Pass-2 builds
against this schema, not against the implementation. Changes require a v2
with a migration note; v1 fields may not change meaning.

## Scope

Pass 1 = deterministic page-local extraction as shipped by
`extract_document(reconcile_tables=true)` at engine 523e9670. Pass 1 output
is IMMUTABLE input to Pass 2: Pass 2 may add derived structures and
promotion decisions; it may never mutate, retype, or delete Pass-1 blocks.

## Block record (per page, ordered)

| field | type | semantics |
|---|---|---|
| block_type | string | Debug form of BlockType (Body, Title, Subtitle, List, Caption, Footnote, Header, Footer, Boilerplate, ChapterLabel, Reference, ...). Page-local, conservative: Pass 1 does NOT make document-context promotions. |
| text | string | Normalized text (full_normalize). Character content is gate-protected (>=0.999 corpus retention). |
| bbox | [f32;4] | xywh, BOTTOM-LEFT origin, page points. (Tables/figures use top-origin x0y0x1y1 — documented divergence.) |
| font_size / font_name / is_bold | f32/string/bool | dominant span typography |
| confidence | f32 | classifier confidence 0-1 |
| header_level | u8? | present for heading-family blocks |
| paragraph_id | usize | stable within a page run; merger provenance |

Stable identity for diffing: md5("blk_{page}_{bbox}") as computed by the
pipeline (`_build_blocks`). Any Pass-2 decision must reference blocks by
this id + page.

## Reconciliation provenance (already engine-side)

- Tables: `tables[]` (top-origin bbox, cells with rebuilt text);
  `consumed_blocks[]` = (page, ConsumedBlock{bbox, text, original_type,
  table_order, cells}) — exact character accounting, fail-open.
- Figures: `figures[].content_blocks[]` (absorbed blocks, text preserved
  verbatim) and `figures[].suppressed_table_orders[]`.

## Candidate-role annotations (v1 = absent)

Pass 1 v1 emits NO candidate roles. Pass-2 shadow derives candidates
(heading promotion, reference grouping, hierarchy assignment, table joins)
from the fields above and must emit its own diffable artifact
(`pass2_shadow.json`) referencing Pass-1 block ids. A future Pass-1 v2 may
add conservative candidate-role hints; that is a v2 discussion.

## Invariants Pass 2 may rely on

1. Deterministic: same PDF + same engine commit => byte-identical blocks.
2. No block text deletion relative to spans (char accounting gates).
3. Chrome (Header/Footer/Boilerplate) is typed page-locally and is stable
   across the corpus (frozen regression set + rollback fixtures).
4. bbox conventions per the table above; page_height for conversions comes
   from the page media box, not assumed 792.
