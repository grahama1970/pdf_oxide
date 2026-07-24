# COMPETITION: pdf_oxide UX upgrade — COMPLETE CODE deliverable

You are one of three independent competitors (ChatGPT, Claude, Kimi).
Deliver COMPLETE, drop-in code — not advice, not sketches. Best entry
(or synthesis) lands in the repo after adversarial review; your entry
is judged on: contract fidelity, completeness (compiles as given),
verification-first UX quality, and test realism.

## The product (operator's goal, verbatim decisions)
pdf_oxide serves AGENTS: >=95% extraction accuracy; the engine surfaces
elements it is CONFUSED about as an annotation call for humans; a user
asks about a specific section/table/figure and gets a TRACEABLE answer;
every answer ships the ORIGINAL PDF PAGE IMAGES because extraction is
messy and will make mistakes — verification is inherent to consumption.

## Data contracts (all live on main)
1. annotation_call.v1: {schema:"pdf_oxide.annotation_call.v1",
   pdf_sha256, engine_commit, accuracy_estimate:{basis,value},
   items:[{page,kind:"block"|"region"|"page",bbox?,[x,y,w,h],
   reason:"low_confidence"|"char_parity_deficit"|
   "unadjudicated_residual"|"reviewer_flagged", confidence?,
   current_type?, text_excerpt?}]}. 2161 live items across 4 docs.
2. Section tree v2: sections[{id,title,level,parent_id,children[],
   depth,doc_order,page_start,page_end,provenance:{pdf_sha256,page,
   bbox},block_ids[]}] — acyclic forest, doc_order total.
3. page_images: content-addressed PNGs (sha256 names) in
   output_dir/page_images/; elements carry page_image_refs[].
4. calibration sample_v1.jsonl rows: {doc,quintile,page,bbox,type,
   confidence,text,label:null} (100 items); adjudications ->
   labels_v1.jsonl {item_sha,label:"correct"|"wrong_type"|
   "wrong_bounds"|"not_an_element",corrected_type?,ts}.
5. Retrieval contract: every answer view MUST render original page
   image(s) + section_path breadcrumb + provenance chain
   (pdf_sha256 -> page -> bbox -> element id). Missing page image =
   test failure.

## Existing UI (upgrade in place — panel-decided architecture)
Repo: pdf_oxide/ui (React+Vite+TS). Salvage the rendering layer
(bbox->pixel projection, canvas overlays, CVAT-style tag positioning in
PdfLabLabelingPage.tsx ~3700 lines; PdfLabView.tsx ~4800; PdfCanvas in
datalake-explorer). Archive SurgicalTriage*, *StaticProof*,
ProductionWorkflow*. Build adapters so new contracts feed existing
presentation props. Confidence must be OPAQUE in the DOM
(data-confidence-hidden="true") until calibration lands.

## Existing interfaces you are adapting to (verbatim excerpts)
}

function loadCustomFamilyIds(): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(CUSTOM_FAMILIES_STORAGE_KEY) ?? '[]')
    if (!Array.isArray(parsed)) return []
    return [...new Set(parsed.map(value => normalizeFamilyId(String(value))).filter(Boolean))]
  } catch {
    return []
  }
}

function normalizeFamilyId(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9_:-]+/g, '_').replace(/^_+|_+$/g, '')
}

interface Region {
  /** Stable client-side id (not persisted directly). */
  id: string
  family: FamilyId
  /** Normalized image coords. Always x0<x1, y0<y1, all in [0,1]. */
  bbox: [number, number, number, number]
  /** Optional, mirror expected_elements.json fields. */
  label?: string
  text_hint?: string
  lead_label?: string
  breadcrumb?: string[]
  breadcrumb_nodes?: BreadcrumbNode[]
  notes?: string
  semantic_role?: string
  target_page?: number
  dot_leader?: boolean
  toc_title?: string
  toc_entries?: TocEntry[]
  extraction?: RegionExtraction
  /** Where the family tag sits relative to the bbox. Click the tag to
   *  cycle through the four anchored positions. Default `top-outside` =
   *  just above the bbox's top-left, the conventional CVAT / VIA position. */
  labelAnchor?: LabelAnchor
  /** Where the region came from. 'human' = labeler drew it; agent origins
   *  are pre-computed candidates the labeler verifies / corrects.
   *  `'agent_link_sweep'` = emitted by `extract_link_chips.py` via the PDF
   *  /Dest annotations; `'agent_dispatcher'` = emitted by the canary
   *  dispatcher (future). */
  origin?: 'human' | 'agent_link_sweep' | 'agent_dispatcher'
  /** Agent-origin metadata: where the link points (for control_link /
   *  publication_link chips). Used by the labeler to verify the link's
   *  canonical target is correct. */
  agentMeta?: {
    destPage?: number | null
    destYNorm?: number | null
    actionUrl?: string | null
  }
}

interface RegionExtraction {
  source?: string
  source_id?: string
  bbox?: [number, number, number, number]
  text?: string
  table_json?: {

## DELIVERABLE (complete code, every file full-content):
1. ui/src/adapters/annotationCall.ts, pageImageRefs.ts, sectionTree.ts
2. Calibrate mode integration for PdfLabLabelingPage (full modified
   sections or a wrapper component CalibrateRoute.tsx, complete).
3. RetrievalEvidenceView.tsx: query result -> answer + page image +
   [data-testid=page-image|section-breadcrumb|provenance-chain].
4. Annotation queue view consuming annotation_call.v1 (2161-item scale:
   virtualized list, filter by reason/doc).
5. Tests (Playwright or vitest+jsdom): the acceptance set — page image
   present else FAIL; bbox overlay coordinates; confidence hidden;
   labels row schema.
6. A FILELIST manifest: path -> one-line purpose, and any assumptions.
Output everything in one response, fenced per file with its path.
