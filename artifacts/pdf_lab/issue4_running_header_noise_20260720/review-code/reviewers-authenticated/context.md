GS001 Criterion 6 accepted patch structured code review.

Immutable goal: PDF-EXTRACTION-GS001-TAU-V1.
Patch scope: issue #4 NIST page 27 same-band running-header merge. The old ledger synthesized type running_header. The intended invariant is to match existing running_header fragments in the top margin, synthesize one header_footer_noise page_chrome parent, suppress child fragments, and preserve the table. The patch changes only the synthesized parent type and adds focused regression/evidence artifacts.

Deterministic evidence already produced:
- issue4_before_after_regression.json gate.status=PASS, issue_signature_decreased=true, blocking_class_increased=false, table_preserved=true.
- pytest command recorded in proof_summary.json: PYTHONPATH=python uv run --with reportlab --with pymupdf pytest -q tests/test_nist_running_header_merge.py -> 2 passed in 0.31s.
- ruff command recorded in proof_summary.json -> All checks passed.

Review requirements:
- Be read-only.
- Review correctness/regression, tests/validation, and evidence-closure safety.
- Do not request broad GS001 convergence, UI work, or unrelated refactors.
- Findings must cite concrete file/artifact evidence.
