Position

REPAIR_CURRENT_GATE: Page 26 is a real extractor-integration defect. Apply one bounded repair in scripts/pdf_lab/snapshot_current_extraction.py: when horizontal text is geometrically contained in a detected table, reconcile it into the appropriate table row/cell before side-chrome suppression, then remove the duplicate standalone block. Do not touch Tau or SciLLM.

Evidence

The snapshot path receives classified blocks first, then extracts tables separately, but its only generic table reconciliation handles QID-marked rows; its duplicate suppression likewise targets only QID rows. There is no corresponding reconciliation for ordinary text misclassified as Boilerplate inside a table.

The existing side-chrome suppression is text-and-margin based and has no table-containment exception, while the page-26 review found that both DOI fragments visually belong to the table’s first-row REVISION cell.

The focused test boundary already exists at tests/test_pdf_lab_snapshot_current_extraction.py, which directly loads this snapshot module and contains side-chrome and table-geometry regressions.

Focused proof:

Bash
pytest -q tests/test_pdf_lab_snapshot_current_extraction.py -k "page26 and table"
python scripts/pdf_lab/snapshot_current_extraction.py \
  --pdf <NIST_SP_800-53r5.pdf> \
  --max-pages 26 \
  --apply-mode release \
  --ledger <current-ledger> \
  --out artifacts/pdf_lab/page26_table_doi_reconciliation_20260721/page_after.json

The regression must prove that the table bbox remains unchanged, its text contains both NIST.SP.800-181 and NIST.SP.800-181r1, and neither fragment remains as a standalone header_footer_noise block. Preserve the result in artifacts/pdf_lab/page26_table_doi_reconciliation_20260721/audit_summary.json.

Uncertainties

The bundle does not expose the raw cell-boundary metadata, so the repair must demonstrate that both recovered lines enter the REVISION cell rather than being appended generically to the table text. This is a deterministic proof requirement, not a human decision.

Blockers

None for the local repair and focused proof. Live GitHub mutation remains separately approval-gated and is not required to close this repair step.

<<<WEBGPT_DONE:20260721T105742Z:9a2f95b5>>>_
