Position

REPAIR_CURRENT_GATE: Normalize this page-bottom page_chrome block to source_type: Footer in scripts/pdf_lab/snapshot_current_extraction.py, with one focused page-28 regression. Preserve raw.block_type: Body as provenance.

Evidence

The gate is not passed: review validation succeeded, but the page remains still_open because patch delegation was dry-run.

The snapshot currently copies block_type directly into source_type; later ledger application can correctly change type and semantic_role, but there is no post-ledger normalization of source_type.

The observed bbox begins at normalized y=0.941, and the block is already classified as header_footer_noise with semantic_role: page_chrome. Position-based footer recognition is a standard deterministic technique; current pypdf documentation demonstrates excluding headers and footers using page coordinates. 
PyPDF

Smallest proof:

Bash
pytest -q tests/test_pdf_lab_snapshot_current_extraction.py -k page28_footer_source_type

Then regenerate page 28 with the existing snapshot command and preserve:

artifacts/pdf_lab/page28_footer_source_type_20260721/page_after.json

artifacts/pdf_lab/page28_footer_source_type_20260721/audit_summary.json

The proof must assert that actual:p28:block:2 retains its text, bbox, type: header_footer_noise, and semantic_role: page_chrome; changes only source_type to Footer; and retains raw.block_type: Body.

Uncertainties

Use the conjunction of normalized bottom geometry, header_footer_noise, and page_chrome; do not key the repair to the literal text or broaden it to running-header normalization.

Blockers

None for the bounded local repair and deterministic proof.
