# UX Competition Round 5 Receipt

## Verdict

PASS. The requested round-5 source sites have complete four-attribute coverage,
the rebuilt live app passed 34/34 deterministic interactions across four
surfaces, and the route-matched screenshots were visually inspected.

## Live runtime

- Repository serving port 3013:
  `/home/graham/workspace/experiments/pdf_oxide-p34-coder/ui`
- Artifact root:
  `/tmp/pdf-lab-ui/live-mount`
- Build served:
  `ui/dist/assets/index-CBxmp5tE.js` and
  `ui/dist/assets/index-CiTbV1Hu.css`
- Live fixture family:
  `/tmp/pdf-lab-ui/live-mount/round2-live`
- `mocked: no`
- `live: yes`

The annotation, calibration, and evidence surfaces used real JSON/JSONL/PNG
artifacts from the mounted artifact root. The interaction run deliberately did
not submit an adjudication or write a label; it exercised filters, selection,
typing, label repositioning, expansion, disabled states, titles, action IDs,
ARIA state, values, and target dimensions.

## Four-attribute source audit

Audit command:

```bash
node artifacts/ux_competition/round5/qid-coverage-audit.mjs \
  > artifacts/ux_competition/round5/qid-coverage.json
```

The audit parses TSX with the TypeScript compiler API and counts native
interactive source sites (`a`, `button`, `input`, `select`, `summary`,
`textarea`). A site is covered only when it has `data-qid`,
`data-qs-action`, and `title`, and its file has a one-for-one
`useRegisterAction` registration.

| New source component | Interactive sites | All four covered | Misses |
|---|---:|---:|---|
| `ui/src/App.tsx` | 1 | 1 | none |
| `ui/src/components/annotation/AnnotationQueueRoute.tsx` | 6 | 6 | none |
| `ui/src/components/calibration/CalibrateRoute.tsx` | 8 | 8 | none |
| `ui/src/components/retrieval/RetrievalEvidenceView.tsx` | 1 | 1 | none |
| `ui/src/components/verification/NormalizedPageOverlay.tsx` | 1 | 1 | none |
| **Total** | **17** | **17** | **none** |

The App site is the reusable navigation-link component and renders four live
tabs. Dynamic annotation rows, reason buttons, evidence cards, and page-overlay
labels use stable sanitized entity qualifiers. Hooks are called at child
component top level, never inside a map callback. Every registration uses
`app: 'pdf-lab'`. QIDs follow `component:element:qualifier`; action IDs use
uppercase underscore names with component prefixes.

Machine-readable audit:
[`qid-coverage.json`](qid-coverage.json)

The skill documentation names `verify-data-qid.py`, but that executable is not
present under either governing skill root. The checked-in TypeScript AST audit
and the live `test-interactions` QID compliance scan provide the available
source and DOM enforcement.

## Deterministic live interaction proof

Generation was run for the root, annotation, calibration, and evidence URLs.
The stock generator appends `/` after a hash route, so the final combined
manifest normalizes the origin into `base_url` and places each exact hash URL
in its surface `path`.

Final manifest:
[`manifest.json`](manifest.json)

Generated inputs:

- [`generated-root.json`](generated-root.json)
- [`generated-annotations.json`](generated-annotations.json)
- [`generated-calibrate.json`](generated-calibrate.json)
- [`generated-evidence.json`](generated-evidence.json)

Final command:

```bash
/home/graham/workspace/experiments/agent-skills/skills/test-interactions/run.sh run \
  --manifest /home/graham/workspace/experiments/pdf_oxide-p34-coder/artifacts/ux_competition/round5/manifest.json \
  --output-dir /home/graham/workspace/experiments/pdf_oxide-p34-coder/artifacts/ux_competition/round5/captures/final
```

Result: **34 PASS / 0 FAIL / 0 WARN / 34 total** across `root`,
`annotations`, `calibrate`, and `evidence`.

Native run result:
[`captures/final/results.json`](captures/final/results.json)

The manifest is non-trivial: it contains screenshots plus live hover, click,
and type interactions, and deterministic assertions for value, ARIA selection,
enabled/disabled state, title, `data-qs-action`, and minimum 44x44 target size.
Per-surface QID compliance is enabled.

The first root scan produced 12 C02 failures (four new navigation tabs and
eight existing root controls). After the focused CSS repair, the final run
records positive 44x44-or-larger assertions and zero compliance failures.

## Visually inspected captures

- Root:
  [`root/0001_root-overview_screenshot.png`](captures/final/root/0001_root-overview_screenshot.png)
- Annotation row selection and moved label:
  [`annotations/0008_annotation-detail_click.png`](captures/final/annotations/0008_annotation-detail_click.png)
- Calibration corrected-type input and enabled decision:
  [`calibrate/0005_calibration-decision-controls_hover.png`](captures/final/calibrate/0005_calibration-decision-controls_hover.png)
- Retrieval source image, provenance chain, and expanded excerpt:
  [`evidence/0003_retrieval-evidence-controls_click.png`](captures/final/evidence/0003_retrieval-evidence-controls_click.png)

All route screenshots show the matching active navigation tab and the expected
surface content. DOM assertions alone were not used as visual proof.

## Static proof

```text
npm run typecheck
  PASS — tsc --noEmit

npm test -- --run
  PASS — 4 files, 16 tests

npm run build
  PASS — 1779 modules transformed

git diff --check
  PASS
```

The Vite large-chunk warning remains pre-existing advisory output; the build
completed successfully.

## Scope notes

Round 4 is not present in this branch (HEAD before this work was round 3b), and
no mounts picker, mounts endpoint, or new empty-state action exists in the
checked-out sources. Therefore there was nothing from those round-4 additions
to instrument. The new routes were exercised with explicit live artifact query
parameters. Bare-route auto-resolution remains outside this round-5 receipt
unless the missing round-4 work is integrated.

This receipt proves source-site four-attribute coverage, live DOM interaction
behavior, touch-target compliance for the exercised surfaces, route-matched
visual rendering, TypeScript correctness, tests, and production build. It does
not prove successful persistence into the external `app_actions` ArangoDB
collection or semantic correctness of the underlying PDF extraction.
