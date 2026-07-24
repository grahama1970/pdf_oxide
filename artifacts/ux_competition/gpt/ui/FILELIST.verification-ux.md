# PDF Oxide verification-first UX file manifest

| Path | Purpose | Assumptions |
|---|---|---|
| `ui/src/adapters/annotationCall.ts` | Strictly validates `pdf_oxide.annotation_call.v1` and flattens calls into queue presentation rows. | `bbox` is `[x,y,width,height]`; values above 1 require page-image dimensions before visual projection; confidence remains in memory but must never enter the DOM. |
| `ui/src/adapters/pageImageRefs.ts` | Validates content-addressed PNG references, builds a document/page lookup index, resolves mandatory source images, and projects normalized boxes. | Page image filenames are exactly `<sha256>.png`; absolute bboxes need width/height metadata; page indices are passed through without adding one. |
| `ui/src/adapters/sectionTree.ts` | Validates section-tree v2 as an acyclic, parent/child-consistent forest with strict `doc_order`, paths, and block lookup. | All sections in one payload belong to one PDF; `depth` equals parent-chain depth; each block belongs to at most one section. |
| `ui/src/adapters/calibration.ts` | Parses `sample_v1.jsonl`, hashes blinded items, and validates/serializes exact `labels_v1.jsonl` rows. | Sample bboxes are normalized `[x,y,width,height]`; confidence is excluded from the item hash and all rendered output. |
| `ui/src/components/verification/NormalizedPageOverlay.tsx` | Shared original-page renderer with CVAT-style normalized overlay and four-position label-anchor cycling. | The supplied image is the original content-addressed page PNG, not a derived crop. |
| `ui/src/components/calibration/CalibrateRoute.tsx` | Blinded, one-item-at-a-time calibration workflow with page image, overlay, keyboard decisions, exact label writes, and export. | `sample` and `pageImages` URLs are immutable artifacts; `/api/pdf-lab/calibration/labels` is writable; `wrong_type` requires `corrected_type`. |
| `ui/src/components/annotation/AnnotationQueueRoute.tsx` | Virtualized 2k+ annotation-call consumer with document/reason/kind/search filters and source-page inspection. | A manifest may contain embedded calls or URLs; metadata triage is allowed without an image, but visual adjudication is not. |
| `ui/src/components/retrieval/RetrievalEvidenceView.tsx` | Fail-closed answer view that always renders original page images, section breadcrumbs, and PDF→page→bbox→element provenance. | An answer must have at least one evidence element; every evidence element must resolve at least one content-addressed PNG and a section path. |
| `ui/src/components/verification/VerificationUx.css` | Complete responsive visual system for calibration, annotation queue, and retrieval evidence modes. | Existing global `index.css` keeps `html/body/#root` full-height and the app shell is flex-column. |
| `ui/src/App.tsx` | Adds `#pdf-lab/annotations`, `#pdf-lab/calibrate`, and `#pdf-lab/evidence` while preserving all existing `PdfLabView` subpaths. | Existing `PdfLabView` continues to export `PdfLabView` with `initialSubpath`, `pdfUrl`, and `extractionUrl` props. |
| `ui/server/index.ts` | Preserves the standalone loop/review API and adds serialized, validated GET/POST storage for `labels_v1.jsonl`. | Artifact roots are trusted operator configuration; label output defaults to `<artifacts>/calibration/labels_v1.jsonl`; browser and API share origin. |
| `ui/src/adapters/pageImageRefs.test.ts` | Verifies content addressing, index lookup, rejection of non-hash filenames, and bbox projection. | Vitest runs in jsdom. |
| `ui/src/adapters/annotationCall.test.ts` | Verifies live-contract normalization and fail-closed enum handling. | Confidence may exist only in the model. |
| `ui/src/adapters/sectionTree.test.ts` | Verifies breadcrumb construction, block lookup, and hierarchy inconsistency rejection. | Fixture uses zero-based page indices. |
| `ui/src/components/verification/VerificationUx.test.tsx` | Tests mandatory images, provenance/breadcrumb rendering, overlay coordinates, confidence opacity, exact label rows, and 2161-item virtualization. | Content-addressed test images use synthetic SHA filenames; network is not required. |
| `ui/src/test/setup.ts` | Installs jest-dom assertions for Vitest. | `@testing-library/jest-dom` is installed. |
| `ui/vitest.config.ts` | Configures React/jsdom tests and setup discovery. | Vite 7 and Vitest 3 share the existing React plugin. |
| `ui/package.json` | Adds repeatable test commands and UI-test dependencies without changing runtime React/Vite versions. | Lockfile regeneration is performed by the repository owner with its normal package manager. |

## Routes

- `#pdf-lab/annotations?calls=<annotation-call-or-manifest-url>&pageImages=<page-image-index-url>`
- `#pdf-lab/calibrate?sample=<sample_v1.jsonl-url>&pageImages=<page-image-index-url>`
- `#pdf-lab/evidence?result=<retrieval-result-url>&pageImages=<page-image-index-url>&tree=<section-tree-v2-url>`

## Machine checks

```bash
cd ui
npm install
npm run typecheck
npm test
npm run build
```
