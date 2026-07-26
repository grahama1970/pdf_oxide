Integrated commit pushed to `origin/main`:

`8a520d58d13a11395a851b95a4fee884b8986f2b`

Remote verification:

```text
git ls-remote origin refs/heads/main
8a520d58d13a11395a851b95a4fee884b8986f2b refs/heads/main
```

Integration proof in clean worktree:

```text
PYTHONPATH=python pytest -q tests/test_pdf_lab_snapshot_current_extraction.py tests/test_nist_table_duplicate_suppression.py tests/test_nist_page456_control_table_headers.py
26 passed, 5 warnings
```

Note: the clean integration worktree needed the same preset/applier state that the original live proof used, so the integrated commit includes `python/pdf_oxide/presets/applier.py` and `python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json` as task-relevant dependencies for the release-mode extraction path.
