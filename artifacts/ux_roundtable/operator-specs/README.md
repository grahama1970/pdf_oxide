Operator-authored UI specs (2026-07-25), binding, in arrival order:
1 StickyActionBar - sticky bottom action bar, oracle-aware primary CTA (Enter=Accept Oracle Fix), hotkey badges, tertiary actions right.
2 TaskSummaryBanner - top of FLAG EVIDENCE column; plain-language flag translation (char_parity red "Missing Characters Detected"; low_confidence yellow "Low Engine Confidence").
3 ExtractedTextEditor - metadata collapsed in <details>; Corrected Text textarea flex-grow; inline "Inject Oracle Suggestion" (emerald) when oracle exists.
4 StreamlinedHeader - 48px sticky app bar: breadcrumb + demoted H1 + info tooltip + centered nav tabs + "N open" pulsing pill. Kills dashboard-default dead space.
5 QueueSidePanel - collapsible rail (288px->64px icon strip), quick filters at top, status icons (open red dot / active blue / deferred yellow clock / resolved green check + strikethrough), grouped by page.
6 CalibrationDashboard - calibrate tab: visual cheat sheet (DO/DON'T cards), Recent QA Overrides feed with View Diff, Gold-Standard-alignment ring + throughput stats (Resolved Today / Avg Time / Deferred) from real ledgers; "no data yet" where artifacts lack data.
7 RetrievalEvidenceTab - engine diagnostics: per-dimension confidence breakdown (text vs bbox), Raw Engine Payload JSON with Copy button, Similar Adjudicated Cases with the prior action taken; "no data yet" where no vector search exists.
Full operator component code preserved in session transcript 2026-07-25.
