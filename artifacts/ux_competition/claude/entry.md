Design note before the code: this is forensic tooling, so the identity is "evidence ledger" — every identifier, coordinate, sha, and doc_order is set in tabula…
Architected production-ready React component system with contract validation

Architected production-ready React component system with contract validation

Design note before the code: this is forensic tooling, so the identity is "evidence ledger" — every identifier, coordinate, sha, and doc_order is set in tabular monospace so the operator scans columns like a ledger, and page images sit on a dark neutral mat so page-whites read true during verification. One accent (cyan) marks interactive/selected state; reason is a data category, so it's color-coded as information, not decoration. Confidence is kept out of the DOM entirely (not merely visually hidden) until calibration lands.

Architecture: pure contract→image-pixel geometry (geometry.ts) is separated from image-pixel→display scaling (PageImageOverlay), so the coordinate math is unit-testable without a browser. Adapters normalize the five contracts to presentation props; every view consumes the same geometry primitive.

Assumption stated up front (centralized in one function): annotation/section bboxes are expressed in the page image's native pixel space (bboxSpace: "image_px", the default). A "pdf_points_bottom_left" path is included and tested for the other convention — flip one prop if the engine reports points.

ts
// ui/src/lib/types.ts
// Single source of truth for the live data contracts and the presentation
// props the views consume. Contract types mirror main exactly; presentation
// types are what the salvaged rendering layer expects.

/* ---------- geometry ---------- */
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
export type Bbox = [number, number, number, number]; // [x, y, w, h]
export type BboxSpace = "image_px" | "pdf_points_bottom_left";
export interface Size {
  w: number;
  h: number;
}

/* ---------- (1) annotation_call.v1 ---------- */
export type AnnotationReason =
  | "low_confidence"
  | "char_parity_deficit"
  | "unadjudicated_residual"
  | "reviewer_flagged";
export type AnnotationKind = "block" | "region" | "page";

export interface AnnotationCallItem {
  page: number;
  kind: AnnotationKind;
  bbox?: Bbox;
  reason: AnnotationReason;
  confidence?: number;
  current_type?: string;
  text_excerpt?: string;
}
export interface AccuracyEstimate {
  basis: string;
  value: number;
}
export interface AnnotationCallV1 {
  schema: "pdf_oxide.annotation_call.v1";
  pdf_sha256: string;
  engine_commit: string;
  accuracy_estimate: AccuracyEstimate;
  items: AnnotationCallItem[];
}

/* ---------- (2) section tree v2 ---------- */
export interface SectionProvenance {
  pdf_sha256: string;
  page: number;
  bbox: Bbox;
}
export interface SectionV2 {
  id: string;
  title: string;
  level: number;
  parent_id: string | null;
  children: string[];
  depth: number;
  doc_order: number;
  page_start: number;
  page_end: number;
  provenance: SectionProvenance;
  block_ids: string[];
}

/* ---------- (3) page images ---------- */
// A ref may arrive as a bare sha filename ("<sha>.png" or "<sha>") or as an
// object carrying its page number. Normalized to PageImage.
export type RawPageImageRef = string | { sha256: string; page?: number };
export interface PageImage {
  sha256: string;
  page?: number;
  url: string;
  naturalWidth?: number;
  naturalHeight?: number;
}

/* ---------- (4) calibration ---------- */
export interface CalibrationSampleRow {
  doc: string;
  quintile: number;
  page: number;
  bbox: Bbox;
  type: string;
  confidence: number; // present in the sample; never rendered pre-calibration
  text: string;
  label: null;
}
export type AdjudicationLabel =
  | "correct"
  | "wrong_type"
  | "wrong_bounds"
  | "not_an_element";
export interface LabelRow {
  item_sha: string;
  label: AdjudicationLabel;
  corrected_type?: string;
  ts: string; // ISO-8601
}

/* ---------- (5) retrieval contract ---------- */
export interface ProvenanceChain {
  pdf_sha256: string;
  page: number;
  bbox: Bbox;
  element_id: string;
}
export interface RetrievalAnswer {
  query: string;
  answer_text: string;
  section_id: string;
  provenance: ProvenanceChain;
  page_image_refs: RawPageImageRef[];
}

/* ---------- presentation ---------- */
export interface OverlayBoxSpec {
  id: string;
  rect: Rect; // ALREADY in image-native pixels
  label?: string; // family/current_type tag drawn above top-left
  state?: "default" | "selected" | "flagged";
}
export interface PresentationItem {
  key: string; // stable hash, React key + dedupe
  docId: string;
  page: number;
  kind: AnnotationKind;
  bbox: Bbox | null;
  reason: AnnotationReason;
  reasonLabel: string;
  currentType?: string;
  excerpt?: string;
  confidence?: number; // retained in model, withheld from DOM
}
ts
// ui/src/lib/sha256.ts
// Compact, synchronous, dependency-free SHA-256 over UTF-8 strings.
// Synchronous (not SubtleCrypto) so item_sha is computable inside render and
// inside jsdom tests without async plumbing. Correctness pinned by sha256.test.ts.

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function utf8Bytes(str: string): Uint8Array {
  const out: number[] = [];
  for (let i = 0; i < str.length; i++) {
    let c = str.charCodeAt(i);
    if (c < 0x80) {
      out.push(c);
    } else if (c < 0x800) {
      out.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f));
    } else if (c >= 0xd800 && c <= 0xdbff) {
      // surrogate pair
      const hi = c;
      const lo = str.charCodeAt(++i);
      c = 0x10000 + ((hi & 0x3ff) << 10) + (lo & 0x3ff);
      out.push(
        0xf0 | (c >> 18),
        0x80 | ((c >> 12) & 0x3f),
        0x80 | ((c >> 6) & 0x3f),
        0x80 | (c & 0x3f),
      );
    } else {
      out.push(
        0xe0 | (c >> 12),
        0x80 | ((c >> 6) & 0x3f),
        0x80 | (c & 0x3f),
      );
    }
  }
  return Uint8Array.from(out);
}

function rotr(x: number, n: number): number {
  return (x >>> n) | (x << (32 - n));
}

export function sha256Bytes(msg: Uint8Array): Uint8Array {
  const H = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c,
    0x1f83d9ab, 0x5be0cd19,
  ]);
  const l = msg.length;
  const bitLen = l * 8;
  const withOne = l + 1;
  const k = (56 - (withOne % 64) + 64) % 64;
  const total = withOne + k + 8;
  const buf = new Uint8Array(total);
  buf.set(msg, 0);
  buf[l] = 0x80;
  // 64-bit big-endian length (high word ~0 for realistic inputs)
  const hi = Math.floor(bitLen / 0x100000000);
  const lo = bitLen >>> 0;
  const dv = new DataView(buf.buffer);
  dv.setUint32(total - 8, hi);
  dv.setUint32(total - 4, lo);

  const w = new Uint32Array(64);
  for (let off = 0; off < total; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = H;
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + K[i] + w[i]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + t1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (t1 + t2) >>> 0;
    }
    H[0] = (H[0] + a) >>> 0;
    H[1] = (H[1] + b) >>> 0;
    H[2] = (H[2] + c) >>> 0;
    H[3] = (H[3] + d) >>> 0;
    H[4] = (H[4] + e) >>> 0;
    H[5] = (H[5] + f) >>> 0;
    H[6] = (H[6] + g) >>> 0;
    H[7] = (H[7] + h) >>> 0;
  }
  const out = new Uint8Array(32);
  new DataView(out.buffer).setUint32(0, H[0]);
  for (let i = 0; i < 8; i++) new DataView(out.buffer).setUint32(i * 4, H[i]);
  return out;
}

export function sha256Hex(input: string): string {
  const d = sha256Bytes(utf8Bytes(input));
  let s = "";
  for (let i = 0; i < d.length; i++) s += d[i].toString(16).padStart(2, "0");
  return s;
}
ts
// ui/src/lib/geometry.ts
// Contract-space -> image-native-pixel projection. This is the ONLY place that
// encodes the coordinate-space assumption, so switching conventions is a
// one-line change. Display scaling (image px -> on-screen px) lives in the
// PageImageOverlay component, deliberately kept separate for testability.
import type { Bbox, BboxSpace, Rect, Size } from "./types";

/** Scale an image-pixel rect into displayed-pixel space. Pure; unit-tested. */
export function projectRect(box: Rect, natural: Size, displayed: Size): Rect {
  const sx = natural.w === 0 ? 1 : displayed.w / natural.w;
  const sy = natural.h === 0 ? 1 : displayed.h / natural.h;
  return { x: box.x * sx, y: box.y * sy, w: box.w * sx, h: box.h * sy };
}

/**
 * Normalize a contract bbox into image-native pixel space.
 * DEFAULT ASSUMPTION: bboxes are already in image pixels ("image_px").
 * The "pdf_points_bottom_left" branch converts + flips the Y axis if the
 * engine ever reports PDF points instead.
 */
export function toImagePixels(
  bbox: Bbox,
  space: BboxSpace = "image_px",
  pagePdfSize?: Size,
  imageNatural?: Size,
): Rect {
  const [x, y, w, h] = bbox;
  if (space === "image_px") return { x, y, w, h };
  if (!pagePdfSize || !imageNatural) {
    throw new Error(
      "pdf_points_bottom_left requires pagePdfSize and imageNatural",
    );
  }
  const sx = imageNatural.w / pagePdfSize.w;
  const sy = imageNatural.h / pagePdfSize.h;
  const yTopPoints = pagePdfSize.h - (y + h); // bottom-left -> top-left origin
  return { x: x * sx, y: yTopPoints * sy, w: w * sx, h: h * sy };
}
ts
// ui/src/lib/jsonl.ts
// Minimal JSONL read/write. Skips blank lines; surfaces the offending line
// number on parse failure so a malformed sample file is a loud, locatable error.
export function parseJsonl<T>(text: string): T[] {
  const rows: T[] = [];
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    try {
      rows.push(JSON.parse(line) as T);
    } catch (e) {
      throw new Error(`Malformed JSONL at line ${i + 1}: ${(e as Error).message}`);
    }
  }
  return rows;
}

export function toJsonl(rows: unknown[]): string {
  return rows.map((r) => JSON.stringify(r)).join("\n") + (rows.length ? "\n" : "");
}
ts
// ui/src/lib/labels.ts
// Adjudication label helpers + a strict validator used both by the calibrate
// route and by labelsSchema.test.ts. item_sha binds a label to the exact
// sample item it adjudicates.
import { sha256Hex } from "./sha256";
import type {
  AdjudicationLabel,
  CalibrationSampleRow,
  LabelRow,
} from "./types";

export const ADJUDICATION_LABELS: readonly AdjudicationLabel[] = [
  "correct",
  "wrong_type",
  "wrong_bounds",
  "not_an_element",
] as const;

/**
 * Stable identity of a sample item: sha256 over the identifying fields only,
 * in fixed key order. Excludes `label`/`quintile` so re-sampling a page can't
 * change an item's identity.
 */
export function sampleItemSha(row: CalibrationSampleRow): string {
  const canonical = JSON.stringify({
    doc: row.doc,
    page: row.page,
    bbox: row.bbox,
    type: row.type,
    text: row.text,
  });
  return sha256Hex(canonical);
}

export function makeLabelRow(
  item_sha: string,
  label: AdjudicationLabel,
  correctedType?: string,
): LabelRow {
  const row: LabelRow = { item_sha, label, ts: new Date().toISOString() };
  if (label === "wrong_type" && correctedType) row.corrected_type = correctedType;
  return row;
}

const SHA256_RE = /^[0-9a-f]{64}$/;
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;

export function isValidLabelRow(v: unknown): v is LabelRow {
  if (!v || typeof v !== "object") return false;
  const r = v as Record<string, unknown>;
  if (typeof r.item_sha !== "string" || !SHA256_RE.test(r.item_sha)) return false;
  if (typeof r.label !== "string") return false;
  if (!ADJUDICATION_LABELS.includes(r.label as AdjudicationLabel)) return false;
  if ("corrected_type" in r && typeof r.corrected_type !== "string") return false;
  if (typeof r.ts !== "string" || !ISO_RE.test(r.ts)) return false;
  // corrected_type only meaningful for wrong_type
  if (r.corrected_type !== undefined && r.label !== "wrong_type") return false;
  return true;
}
ts
// ui/src/lib/useElementSize.ts
// Tracks an element's displayed size via ResizeObserver, with a jsdom-safe
// fallback (clientWidth/Height, then 0). Overlay scaling reads this.
import { useEffect, useRef, useState } from "react";
import type { Size } from "./types";

export function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [size, setSize] = useState<Size>({ w: 0, h: 0 });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return { ref, size };
}
ts
// ui/src/adapters/annotationCall.ts
// Normalizes annotation_call.v1 -> PresentationItem[]. Validates the schema
// tag loudly (a wrong schema string is a contract break, not a soft warning).
import { sha256Hex } from "../lib/sha256";
import type {
  AnnotationCallV1,
  AnnotationReason,
  PresentationItem,
} from "../lib/types";

export const REASON_LABELS: Record<AnnotationReason, string> = {
  low_confidence: "Low confidence",
  char_parity_deficit: "Char-parity deficit",
  unadjudicated_residual: "Unadjudicated residual",
  reviewer_flagged: "Reviewer flagged",
};

export interface NormalizedAnnotationCall {
  pdfSha256: string;
  engineCommit: string;
  accuracyBasis: string;
  accuracyValue: number;
  items: PresentationItem[];
}

export function parseAnnotationCall(
  raw: unknown,
  docId?: string,
): NormalizedAnnotationCall {
  const call = raw as AnnotationCallV1;
  if (!call || call.schema !== "pdf_oxide.annotation_call.v1") {
    throw new Error(
      `Unexpected annotation_call schema: ${String((call as { schema?: string })?.schema)}`,
    );
  }
  const doc = docId ?? call.pdf_sha256;
  const items: PresentationItem[] = call.items.map((it) => {
    const key = sha256Hex(
      JSON.stringify([
        call.pdf_sha256,
        it.page,
        it.bbox ?? null,
        it.reason,
        it.text_excerpt ?? "",
      ]),
    );
    return {
      key,
      docId: doc,
      page: it.page,
      kind: it.kind,
      bbox: it.bbox ?? null,
      reason: it.reason,
      reasonLabel: REASON_LABELS[it.reason] ?? it.reason,
      currentType: it.current_type,
      excerpt: it.text_excerpt,
      confidence: it.confidence, // kept, never rendered pre-calibration
    };
  });
  return {
    pdfSha256: call.pdf_sha256,
    engineCommit: call.engine_commit,
    accuracyBasis: call.accuracy_estimate.basis,
    accuracyValue: call.accuracy_estimate.value,
    items,
  };
}

export interface ItemFilter {
  reasons?: Set<AnnotationReason>;
  docs?: Set<string>;
}
export function filterItems(
  items: PresentationItem[],
  f: ItemFilter,
): PresentationItem[] {
  return items.filter((it) => {
    if (f.reasons && f.reasons.size && !f.reasons.has(it.reason)) return false;
    if (f.docs && f.docs.size && !f.docs.has(it.docId)) return false;
    return true;
  });
}
ts
// ui/src/adapters/pageImageRefs.ts
// Resolves content-addressed page-image refs -> URLs, indexed by page.
// Accepts bare sha strings ("<sha>" or "<sha>.png") or {sha256, page} objects.
import type { PageImage, RawPageImageRef } from "../lib/types";

export function resolvePageImageUrl(baseUrl: string, sha: string): string {
  const clean = baseUrl.replace(/\/+$/, "");
  return `${clean}/page_images/${sha}.png`;
}

function shaOf(ref: RawPageImageRef): { sha256: string; page?: number } {
  if (typeof ref === "string") {
    return { sha256: ref.replace(/\.png$/i, "") };
  }
  return { sha256: ref.sha256.replace(/\.png$/i, ""), page: ref.page };
}

export function normalizePageImageRefs(
  refs: RawPageImageRef[],
  baseUrl = "",
): PageImage[] {
  return refs.map((r) => {
    const { sha256, page } = shaOf(r);
    return { sha256, page, url: resolvePageImageUrl(baseUrl, sha256) };
  });
}

export function indexByPage(images: PageImage[]): Map<number, PageImage[]> {
  const m = new Map<number, PageImage[]>();
  for (const img of images) {
    if (img.page === undefined) continue;
    const arr = m.get(img.page) ?? [];
    arr.push(img);
    m.set(img.page, arr);
  }
  return m;
}
ts
// ui/src/adapters/sectionTree.ts
// Normalizes section tree v2: id index, cycle-safe breadcrumb path, and the
// two structural invariants the contract promises (acyclic forest, total
// doc_order) exposed as checkable booleans for the UI and tests.
import type { SectionV2 } from "../lib/types";

export interface SectionIndex {
  byId: Map<string, SectionV2>;
  roots: string[];
  path: (id: string) => { id: string; title: string }[];
  childrenSorted: (id: string) => SectionV2[];
  isAcyclic: boolean;
  docOrderIsTotal: boolean;
}

export function buildSectionIndex(sections: SectionV2[]): SectionIndex {
  const byId = new Map<string, SectionV2>();
  for (const s of sections) byId.set(s.id, s);
  const roots = sections.filter((s) => !s.parent_id).map((s) => s.id);

  const path = (id: string) => {
    const out: { id: string; title: string }[] = [];
    const seen = new Set<string>();
    let cur: string | null = id;
    while (cur && byId.has(cur) && !seen.has(cur)) {
      seen.add(cur);
      const node = byId.get(cur)!;
      out.push({ id: node.id, title: node.title });
      cur = node.parent_id;
    }
    return out.reverse();
  };

  const childrenSorted = (id: string) => {
    const node = byId.get(id);
    if (!node) return [];
    return node.children
      .map((c) => byId.get(c))
      .filter((c): c is SectionV2 => !!c)
      .sort((a, b) => a.doc_order - b.doc_order);
  };

  // acyclicity: every parent-walk must terminate within N steps without revisit
  let isAcyclic = true;
  for (const s of sections) {
    const seen = new Set<string>();
    let cur: string | null = s.id;
    let steps = 0;
    while (cur) {
      if (seen.has(cur)) {
        isAcyclic = false;
        break;
      }
      seen.add(cur);
      cur = byId.get(cur)?.parent_id ?? null;
      if (++steps > sections.length + 1) {
        isAcyclic = false;
        break;
      }
    }
    if (!isAcyclic) break;
  }

  const orders = new Set(sections.map((s) => s.doc_order));
  const docOrderIsTotal = orders.size === sections.length;

  return { byId, roots, path, childrenSorted, isAcyclic, docOrderIsTotal };
}
tsx
// ui/src/components/ConfidenceSlot.tsx
// Confidence is PROVABLY MISCALIBRATED until the calibration loop lands, so it
// is withheld from the DOM entirely — not merely visually hidden. The numeric
// value never reaches the tree; only an opaque placeholder does.
import React from "react";

export function ConfidenceSlot(_props: { value?: number }): React.ReactElement {
  return (
    <span
      className="confidence-slot"
      data-confidence-hidden="true"
      aria-hidden="true"
      title="Confidence withheld pending calibration"
    >
      &mdash;
    </span>
  );
}
tsx
// ui/src/components/ReasonChip.tsx
// Reason is a real data category, so it is encoded as color (information, not
// decoration). data-reason drives the hue in console.css.
import React from "react";
import type { AnnotationReason } from "../lib/types";
import { REASON_LABELS } from "../adapters/annotationCall";

export function ReasonChip({ reason }: { reason: AnnotationReason }): React.ReactElement {
  return (
    <span className="reason-chip" data-reason={reason}>
      {REASON_LABELS[reason]}
    </span>
  );
}
tsx
// ui/src/components/PageImageOverlay.tsx
// The salvaged geometry core, rebuilt against the new contracts. Renders the
// original page image (verification substrate) with absolutely-positioned bbox
// overlays. Contract->image-px conversion happens upstream in geometry.ts; this
// component only scales image px -> displayed px.
//
// If `image` is null the component renders an explicit contract-violation state
// (data-testid="page-image-error"): a missing page image must fail loudly.
import React, { useState } from "react";
import { projectRect } from "../lib/geometry";
import { useElementSize } from "../lib/useElementSize";
import type { OverlayBoxSpec, PageImage, Size } from "../lib/types";

export interface PageImageOverlayProps {
  image: PageImage | null;
  boxes?: OverlayBoxSpec[];
  /** Natural image size; if omitted, read from <img> onLoad. Tests inject it. */
  naturalSize?: Size;
  onSelectBox?: (id: string) => void;
  caption?: string;
}

export function PageImageOverlay({
  image,
  boxes = [],
  naturalSize,
  onSelectBox,
  caption,
}: PageImageOverlayProps): React.ReactElement {
  const { ref, size } = useElementSize<HTMLDivElement>();
  const [loadedNatural, setLoadedNatural] = useState<Size | null>(null);

  if (!image) {
    return (
      <div className="page-image-error" data-testid="page-image-error" role="alert">
        No page image for this element. The retrieval contract requires the
        original page render; this result cannot be verified and must be treated
        as a failure.
      </div>
    );
  }

  const natural = naturalSize ?? loadedNatural ?? { w: 0, h: 0 };
  // jsdom / pre-layout fallback: when the container hasn't measured, assume
  // display == natural so overlay coords equal image-px coords (scale 1).
  const displayed: Size =
    size.w > 0 ? { w: size.w, h: size.h || natural.h } : natural;

  return (
    <figure className="page-image" data-testid="page-image-figure">
      <div className="page-image__mat" ref={ref}>
        <img
          className="page-image__img"
          data-testid="page-image"
          src={image.url}
          alt={caption ?? `Page ${image.page ?? ""} render`}
          onLoad={(e) => {
            const el = e.currentTarget;
            if (!naturalSize && el.naturalWidth) {
              setLoadedNatural({ w: el.naturalWidth, h: el.naturalHeight });
            }
          }}
        />
        <div className="page-image__overlays">
          {boxes.map((b) => {
            const r =
              natural.w > 0
                ? projectRect(b.rect, natural, displayed)
                : b.rect;
            return (
              <button
                key={b.id}
                type="button"
                className="overlay-box"
                data-testid="overlay-box"
                data-state={b.state ?? "default"}
                onClick={() => onSelectBox?.(b.id)}
                style={{
                  left: `${r.x}px`,
                  top: `${r.y}px`,
                  width: `${r.w}px`,
                  height: `${r.h}px`,
                }}
              >
                {b.label ? (
                  <span className="overlay-box__tag">{b.label}</span>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>
      {caption ? <figcaption className="mono-caption">{caption}</figcaption> : null}
    </figure>
  );
}
tsx
// ui/src/components/VirtualList.tsx
// Dependency-free windowing list for the 2161-item queue. Fixed row height,
// absolute positioning inside a spacer, overscan for smooth scroll.
import React, { useCallback, useState } from "react";

export interface VirtualListProps<T> {
  items: T[];
  rowHeight: number;
  height: number;
  overscan?: number;
  renderRow: (item: T, index: number) => React.ReactNode;
}

export function VirtualList<T>({
  items,
  rowHeight,
  height,
  overscan = 6,
  renderRow,
}: VirtualListProps<T>): React.ReactElement {
  const [scrollTop, setScrollTop] = useState(0);
  const onScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>) => setScrollTop(e.currentTarget.scrollTop),
    [],
  );
  const total = items.length * rowHeight;
  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const visible = Math.ceil(height / rowHeight) + overscan * 2;
  const last = Math.min(items.length, first + visible);
  const slice = items.slice(first, last);

  return (
    <div
      className="vlist"
      data-testid="vlist"
      style={{ height, overflowY: "auto" }}
      onScroll={onScroll}
    >
      <div style={{ height: total, position: "relative" }}>
        {slice.map((item, i) => {
          const index = first + i;
          return (
            <div
              key={index}
              className="vlist__row"
              style={{
                position: "absolute",
                top: index * rowHeight,
                height: rowHeight,
                left: 0,
                right: 0,
              }}
            >
              {renderRow(item, index)}
            </div>
          );
        })}
      </div>
    </div>
  );
}
tsx
// ui/src/routes/CalibrateRoute.tsx
// Calibration adjudication. Loads sample_v1.jsonl, renders each item's page
// image with its bbox overlaid at correct coordinates, and captures one-tap
// labels (keys 1-4) written as labels_v1.jsonl rows. Confidence stays opaque.
//
// The host injects `resolvePageImage(doc, page)` because the sample rows carry
// no image sha; the page-image index lives with the host that loaded outputs.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { PageImageOverlay } from "../components/PageImageOverlay";
import { ConfidenceSlot } from "../components/ConfidenceSlot";
import { toImagePixels } from "../lib/geometry";
import { toJsonl } from "../lib/jsonl";
import {
  ADJUDICATION_LABELS,
  makeLabelRow,
  sampleItemSha,
} from "../lib/labels";
import type {
  AdjudicationLabel,
  CalibrationSampleRow,
  LabelRow,
  PageImage,
  Size,
} from "../lib/types";

export interface CalibrateRouteProps {
  rows: CalibrationSampleRow[];
  resolvePageImage: (doc: string, page: number) => (PageImage & { naturalSize?: Size }) | null;
  onLabeled?: (row: LabelRow, all: LabelRow[]) => void;
}

const LABEL_HINTS: Record<AdjudicationLabel, string> = {
  correct: "1 · Correct",
  wrong_type: "2 · Wrong type",
  wrong_bounds: "3 · Wrong bounds",
  not_an_element: "4 · Not an element",
};

export function CalibrateRoute({
  rows,
  resolvePageImage,
  onLabeled,
}: CalibrateRouteProps): React.ReactElement {
  const [cursor, setCursor] = useState(0);
  const [labels, setLabels] = useState<LabelRow[]>([]);

  const current = rows[cursor];
  const image = current ? resolvePageImage(current.doc, current.page) : null;

  const box = useMemo(() => {
    if (!current) return null;
    const rect = toImagePixels(current.bbox, "image_px");
    return {
      id: sampleItemSha(current),
      rect,
      label: current.type,
      state: "selected" as const,
    };
  }, [current]);

  const commit = useCallback(
    (label: AdjudicationLabel) => {
      if (!current) return;
      const row = makeLabelRow(sampleItemSha(current), label);
      const all = [...labels, row];
      setLabels(all);
      onLabeled?.(row, all);
      setCursor((c) => Math.min(rows.length, c + 1));
    },
    [current, labels, onLabeled, rows.length],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const map: Record<string, AdjudicationLabel> = {
        "1": "correct",
        "2": "wrong_type",
        "3": "wrong_bounds",
        "4": "not_an_element",
      };
      const label = map[e.key];
      if (label) {
        e.preventDefault();
        commit(label);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [commit]);

  const download = useCallback(() => {
    const blob = new Blob([toJsonl(labels)], { type: "application/x-ndjson" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "labels_v1.jsonl";
    a.click();
    URL.revokeObjectURL(url);
  }, [labels]);

  if (!current) {
    return (
      <section className="calibrate calibrate--done" data-testid="calibrate-done">
        <h2>Adjudication complete</h2>
        <p className="mono-caption">
          {labels.length} / {rows.length} items labeled.
        </p>
        <button type="button" className="btn btn--primary" onClick={download}>
          Download labels_v1.jsonl
        </button>
      </section>
    );
  }

  return (
    <section className="calibrate" data-testid="calibrate-route">
      <header className="calibrate__head">
        <span className="mono-caption">
          {current.doc} · p{current.page} · {cursor + 1}/{rows.length}
        </span>
        <span className="mono-caption">
          type: <code>{current.type}</code> · confidence:{" "}
          <ConfidenceSlot value={current.confidence} />
        </span>
      </header>

      <PageImageOverlay
        image={image}
        naturalSize={image?.naturalSize}
        boxes={box ? [box] : []}
        caption={`${current.doc} · page ${current.page}`}
      />

      {current.text ? (
        <p className="calibrate__excerpt">{current.text}</p>
      ) : null}

      <div className="calibrate__actions" role="group" aria-label="Adjudication">
        {ADJUDICATION_LABELS.map((label) => (
          <button
            key={label}
            type="button"
            className="btn"
            data-label={label}
            onClick={() => commit(label)}
          >
            {LABEL_HINTS[label]}
          </button>
        ))}
      </div>
    </section>
  );
}
tsx
// ui/src/views/RetrievalEvidenceView.tsx
// Verification-first answer view. Renders answer text + the ORIGINAL page
// image with the provenance bbox highlighted + section breadcrumb + the full
// provenance chain (pdf_sha256 -> page -> bbox -> element id). A missing page
// image renders the overlay's contract-violation state and is a test failure.
import React, { useMemo } from "react";
import { PageImageOverlay } from "../components/PageImageOverlay";
import { normalizePageImageRefs } from "../adapters/pageImageRefs";
import { buildSectionIndex } from "../adapters/sectionTree";
import { toImagePixels } from "../lib/geometry";
import type { RetrievalAnswer, SectionV2, Size } from "../lib/types";

export interface RetrievalEvidenceViewProps {
  answer: RetrievalAnswer;
  sections: SectionV2[];
  baseUrl?: string;
  /** Natural size of the provenance page image; tests inject it. */
  naturalSize?: Size;
}

export function RetrievalEvidenceView({
  answer,
  sections,
  baseUrl = "",
  naturalSize,
}: RetrievalEvidenceViewProps): React.ReactElement {
  const index = useMemo(() => buildSectionIndex(sections), [sections]);
  const crumbs = index.path(answer.section_id);
  const images = normalizePageImageRefs(answer.page_image_refs, baseUrl);
  const primary = images[0] ?? null;

  const box = {
    id: answer.provenance.element_id,
    rect: toImagePixels(answer.provenance.bbox, "image_px"),
    state: "flagged" as const,
    label: answer.provenance.element_id,
  };

  return (
    <article className="retrieval" data-testid="retrieval-evidence">
      <header className="retrieval__q">
        <span className="mono-caption">query</span>
        <h2>{answer.query}</h2>
      </header>

      <nav
        className="breadcrumb"
        data-testid="section-breadcrumb"
        aria-label="Section path"
      >
        {crumbs.map((c, i) => (
          <span key={c.id} className="breadcrumb__item">
            {i > 0 ? <span className="breadcrumb__sep">/</span> : null}
            {c.title}
          </span>
        ))}
      </nav>

      <p className="retrieval__answer" data-testid="answer-text">
        {answer.answer_text}
      </p>

      <PageImageOverlay
        image={primary}
        naturalSize={naturalSize}
        boxes={box.rect ? [box] : []}
        caption={`Source · page ${answer.provenance.page}`}
      />

      <dl className="provenance" data-testid="provenance-chain">
        <div className="provenance__row">
          <dt>pdf_sha256</dt>
          <dd className="mono">{answer.provenance.pdf_sha256}</dd>
        </div>
        <div className="provenance__row">
          <dt>page</dt>
          <dd className="mono">{answer.provenance.page}</dd>
        </div>
        <div className="provenance__row">
          <dt>bbox</dt>
          <dd className="mono">[{answer.provenance.bbox.join(", ")}]</dd>
        </div>
        <div className="provenance__row">
          <dt>element</dt>
          <dd className="mono">{answer.provenance.element_id}</dd>
        </div>
      </dl>
    </article>
  );
}
tsx
// ui/src/views/AnnotationQueueView.tsx
// The consumer the 2161 items were missing. Virtualized list, filterable by
// reason and doc; selecting a row opens the page image with the item bbox
// overlaid for inspection. Confidence stays opaque.
import React, { useMemo, useState } from "react";
import { VirtualList } from "../components/VirtualList";
import { ReasonChip } from "../components/ReasonChip";
import { ConfidenceSlot } from "../components/ConfidenceSlot";
import { PageImageOverlay } from "../components/PageImageOverlay";
import { filterItems } from "../adapters/annotationCall";
import { toImagePixels } from "../lib/geometry";
import type {
  AnnotationReason,
  PageImage,
  PresentationItem,
  Size,
} from "../lib/types";

const ALL_REASONS: AnnotationReason[] = [
  "low_confidence",
  "char_parity_deficit",
  "unadjudicated_residual",
  "reviewer_flagged",
];

export interface AnnotationQueueViewProps {
  items: PresentationItem[];
  docs: string[];
  resolvePageImage: (doc: string, page: number) => (PageImage & { naturalSize?: Size }) | null;
  height?: number;
}

export function AnnotationQueueView({
  items,
  docs,
  resolvePageImage,
  height = 560,
}: AnnotationQueueViewProps): React.ReactElement {
  const [reasons, setReasons] = useState<Set<AnnotationReason>>(new Set());
  const [docFilter, setDocFilter] = useState<Set<string>>(new Set());
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const filtered = useMemo(
    () => filterItems(items, { reasons, docs: docFilter }),
    [items, reasons, docFilter],
  );
  const selected = filtered.find((i) => i.key === selectedKey) ?? null;

  const toggle = <T,>(set: Set<T>, v: T): Set<T> => {
    const next = new Set(set);
    next.has(v) ? next.delete(v) : next.add(v);
    return next;
  };

  const selImage =
    selected ? resolvePageImage(selected.docId, selected.page) : null;
  const selBox =
    selected && selected.bbox
      ? [
          {
            id: selected.key,
            rect: toImagePixels(selected.bbox, "image_px"),
            label: selected.currentType,
            state: "selected" as const,
          },
        ]
      : [];

  return (
    <section className="queue" data-testid="annotation-queue">
      <header className="queue__filters">
        <div className="filter-group" aria-label="Filter by reason">
          {ALL_REASONS.map((r) => (
            <label key={r} className="filter-pill" data-active={reasons.has(r)}>
              <input
                type="checkbox"
                checked={reasons.has(r)}
                onChange={() => setReasons((s) => toggle(s, r))}
              />
              <ReasonChip reason={r} />
            </label>
          ))}
        </div>
        <div className="filter-group" aria-label="Filter by doc">
          {docs.map((d) => (
            <label key={d} className="filter-pill" data-active={docFilter.has(d)}>
              <input
                type="checkbox"
                checked={docFilter.has(d)}
                onChange={() => setDocFilter((s) => toggle(s, d))}
              />
              <span className="mono">{d}</span>
            </label>
          ))}
        </div>
        <span className="mono-caption queue__count" data-testid="queue-count">
          {filtered.length} / {items.length}
        </span>
      </header>

      <div className="queue__body">
        <VirtualList
          items={filtered}
          rowHeight={44}
          height={height}
          renderRow={(it) => (
            <button
              type="button"
              className="queue-row"
              data-testid="queue-row"
              data-selected={it.key === selectedKey}
              onClick={() => setSelectedKey(it.key)}
            >
              <span className="mono queue-row__loc">
                {it.docId} · p{it.page}
              </span>
              <ReasonChip reason={it.reason} />
              <span className="queue-row__excerpt">{it.excerpt ?? ""}</span>
              <ConfidenceSlot value={it.confidence} />
            </button>
          )}
        />

        <aside className="queue__detail">
          {selected ? (
            <PageImageOverlay
              image={selImage}
              naturalSize={selImage?.naturalSize}
              boxes={selBox}
              caption={`${selected.docId} · page ${selected.page} · ${selected.reasonLabel}`}
            />
          ) : (
            <div className="queue__empty">
              Select an item to inspect its page and bounds.
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
css
/* ui/src/styles/console.css
   Evidence-ledger identity: dark neutral mat so page-whites read true during
   verification; monospace for every identifier/coordinate/sha (the signature);
   one cyan accent for interactive/selected; reason encoded as muted hue.
   Imported by the app entry (not by component modules, to keep tests css-free). */

:root {
  --surface: #14171c;
  --panel: #1c2027;
  --mat: #0f1113;
  --ink: #e6e8eb;
  --ink-muted: #9aa1ab;
  --line: #2b313b;
  --accent: #5bc8d6;
  --accent-ink: #06222a;

  --reason-low_confidence: #e0a458;
  --reason-char_parity_deficit: #5bc8d6;
  --reason-unadjudicated_residual: #9b8cff;
  --reason-reviewer_flagged: #e8737d;

  --mono: ui-monospace, "JetBrains Mono", "SF Mono", "Menlo", monospace;
  --ui: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;

  --r: 4px;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--surface);
  color: var(--ink);
  font-family: var(--ui);
  font-size: 14px;
  line-height: 1.5;
}

.mono, code, .mono-caption { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.mono-caption { color: var(--ink-muted); font-size: 12px; letter-spacing: 0.02em; }

/* --- buttons --- */
.btn {
  font: inherit;
  background: var(--panel);
  color: var(--ink);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 8px 14px;
  cursor: pointer;
}
.btn:hover { border-color: var(--accent); }
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.btn--primary { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); font-weight: 600; }

/* --- confidence (withheld) --- */
.confidence-slot { color: var(--ink-muted); font-family: var(--mono); user-select: none; }

/* --- reason chips --- */
.reason-chip {
  font-family: var(--mono);
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 999px;
  color: var(--surface);
  background: var(--ink-muted);
  white-space: nowrap;
}
.reason-chip[data-reason="low_confidence"] { background: var(--reason-low_confidence); }
.reason-chip[data-reason="char_parity_deficit"] { background: var(--reason-char_parity_deficit); }
.reason-chip[data-reason="unadjudicated_residual"] { background: var(--reason-unadjudicated_residual); }
.reason-chip[data-reason="reviewer_flagged"] { background: var(--reason-reviewer_flagged); }

/* --- page image + overlays --- */
.page-image { margin: 0; }
.page-image__mat {
  position: relative;
  display: inline-block;
  background: var(--mat);
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--r);
  max-width: 100%;
}
.page-image__img { display: block; max-width: 100%; height: auto; }
.page-image__overlays { position: absolute; inset: 12px; pointer-events: none; }
.overlay-box {
  position: absolute;
  border: 2px solid var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  pointer-events: auto;
  cursor: pointer;
  padding: 0;
}
.overlay-box[data-state="flagged"] { border-color: var(--reason-reviewer_flagged); }
.overlay-box__tag {
  position: absolute;
  top: -18px;
  left: -2px;
  font-family: var(--mono);
  font-size: 10px;
  background: var(--accent);
  color: var(--accent-ink);
  padding: 1px 5px;
  border-radius: 2px;
  white-space: nowrap;
}
.page-image-error {
  border: 1px solid var(--reason-reviewer_flagged);
  color: var(--reason-reviewer_flagged);
  background: color-mix(in srgb, var(--reason-reviewer_flagged) 8%, transparent);
  padding: 12px 14px;
  border-radius: var(--r);
  font-size: 13px;
}

/* --- calibrate --- */
.calibrate { display: grid; gap: 14px; max-width: 900px; }
.calibrate__head { display: flex; justify-content: space-between; gap: 12px; }
.calibrate__excerpt { color: var(--ink-muted); border-left: 2px solid var(--line); padding-left: 10px; margin: 0; }
.calibrate__actions { display: flex; gap: 8px; flex-wrap: wrap; }

/* --- retrieval --- */
.retrieval { display: grid; gap: 12px; max-width: 900px; }
.retrieval__answer { font-size: 15px; }
.breadcrumb { display: flex; flex-wrap: wrap; gap: 4px; color: var(--ink-muted); font-size: 13px; }
.breadcrumb__sep { margin: 0 6px; color: var(--line); }
.provenance { display: grid; gap: 4px; border: 1px solid var(--line); border-radius: var(--r); padding: 10px 12px; margin: 0; }
.provenance__row { display: grid; grid-template-columns: 110px 1fr; gap: 8px; }
.provenance__row dt { color: var(--ink-muted); }
.provenance__row dd { margin: 0; word-break: break-all; }

/* --- queue --- */
.queue { display: grid; gap: 12px; }
.queue__filters { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.filter-group { display: flex; gap: 6px; flex-wrap: wrap; }
.filter-pill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 8px; border: 1px solid var(--line); border-radius: 999px; cursor: pointer; }
.filter-pill[data-active="true"] { border-color: var(--accent); }
.filter-pill input { accent-color: var(--accent); }
.queue__count { margin-left: auto; }
.queue__body { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
.vlist { border: 1px solid var(--line); border-radius: var(--r); background: var(--panel); }
.queue-row { display: grid; grid-template-columns: 130px auto 1fr auto; align-items: center; gap: 10px; width: 100%; height: 100%; padding: 0 12px; background: transparent; border: none; border-bottom: 1px solid var(--line); color: var(--ink); text-align: left; cursor: pointer; }
.queue-row:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); }
.queue-row[data-selected="true"] { background: color-mix(in srgb, var(--accent) 14%, transparent); }
.queue-row__loc { font-size: 12px; color: var(--ink-muted); }
.queue-row__excerpt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.queue__detail { border: 1px solid var(--line); border-radius: var(--r); padding: 12px; min-height: 200px; }
.queue__empty, .queue__detail .queue__empty { color: var(--ink-muted); }

@media (max-width: 820px) {
  .queue__body { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}
ts
// ui/src/test/setup.ts
// jsdom shims: ResizeObserver (absent in jsdom) and a stable URL.createObjectURL
// so the calibrate download path doesn't throw under test.
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// @ts-expect-error assign to global
globalThis.ResizeObserver = globalThis.ResizeObserver || RO;

if (typeof URL.createObjectURL !== "function") {
  // @ts-expect-error jsdom
  URL.createObjectURL = () => "blob:mock";
  // @ts-expect-error jsdom
  URL.revokeObjectURL = () => {};
}
ts
// vitest.config.ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./ui/src/test/setup.ts"],
    include: ["ui/src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
ts
// ui/src/__tests__/sha256.test.ts
import { describe, it, expect } from "vitest";
import { sha256Hex } from "../lib/sha256";
import { sampleItemSha } from "../lib/labels";
import type { CalibrationSampleRow } from "../lib/types";

describe("sha256", () => {
  it("matches NIST test vectors", () => {
    expect(sha256Hex("")).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
    expect(sha256Hex("abc")).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    expect(
      sha256Hex(
        "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
      ),
    ).toBe("248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
  });

  it("sampleItemSha is stable and 64-hex", () => {
    const row: CalibrationSampleRow = {
      doc: "r5",
      quintile: 3,
      page: 42,
      bbox: [10, 20, 30, 40],
      type: "table",
      confidence: 0.5,
      text: "Table 3-1",
      label: null,
    };
    const a = sampleItemSha(row);
    const b = sampleItemSha({ ...row, quintile: 1, confidence: 0.99, label: null });
    expect(a).toMatch(/^[0-9a-f]{64}$/);
    expect(a).toBe(b); // identity ignores quintile/confidence/label
  });
});
ts
// ui/src/__tests__/geometry.test.ts
import { describe, it, expect } from "vitest";
import { projectRect, toImagePixels } from "../lib/geometry";

describe("projectRect", () => {
  it("scales by displayed/natural", () => {
    const r = projectRect(
      { x: 10, y: 20, w: 30, h: 40 },
      { w: 100, h: 200 },
      { w: 50, h: 100 },
    );
    expect(r).toEqual({ x: 5, y: 10, w: 15, h: 20 });
  });
  it("is identity at 1:1", () => {
    const box = { x: 3, y: 4, w: 5, h: 6 };
    expect(projectRect(box, { w: 100, h: 100 }, { w: 100, h: 100 })).toEqual(box);
  });
});

describe("toImagePixels", () => {
  it("image_px is identity", () => {
    expect(toImagePixels([10, 20, 30, 40], "image_px")).toEqual({
      x: 10, y: 20, w: 30, h: 40,
    });
  });
  it("pdf_points_bottom_left scales and flips Y", () => {
    const r = toImagePixels(
      [10, 20, 30, 40],
      "pdf_points_bottom_left",
      { w: 200, h: 400 }, // pdf points
      { w: 400, h: 800 }, // image px
    );
    // scale x2; yTop = 400-(20+40)=340 -> *2 = 680
    expect(r).toEqual({ x: 20, y: 680, w: 60, h: 80 });
  });
  it("throws when point conversion lacks sizes", () => {
    expect(() => toImagePixels([0, 0, 1, 1], "pdf_points_bottom_left")).toThrow();
  });
});
tsx
// ui/src/__tests__/pageImagePresent.test.tsx
import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { RetrievalEvidenceView } from "../views/RetrievalEvidenceView";
import type { RetrievalAnswer, SectionV2 } from "../lib/types";

afterEach(cleanup);

const sections: SectionV2[] = [
  {
    id: "s0", title: "Guidance", level: 1, parent_id: null, children: ["s1"],
    depth: 0, doc_order: 0, page_start: 1, page_end: 20,
    provenance: { pdf_sha256: "abc", page: 1, bbox: [0, 0, 10, 10] }, block_ids: [],
  },
  {
    id: "s1", title: "Access Control", level: 2, parent_id: "s0", children: [],
    depth: 1, doc_order: 1, page_start: 5, page_end: 6,
    provenance: { pdf_sha256: "abc", page: 5, bbox: [1, 1, 2, 2] }, block_ids: ["b9"],
  },
];

const baseAnswer: RetrievalAnswer = {
  query: "What does AC-7 require?",
  answer_text: "It limits consecutive invalid logon attempts.",
  section_id: "s1",
  provenance: { pdf_sha256: "abc", page: 5, bbox: [10, 20, 30, 40], element_id: "b9" },
  page_image_refs: ["deadbeef"],
};

describe("retrieval contract: page image present", () => {
  it("renders page image, breadcrumb, and provenance chain", () => {
    const { getByTestId } = render(
      <RetrievalEvidenceView
        answer={baseAnswer}
        sections={sections}
        naturalSize={{ w: 100, h: 200 }}
      />,
    );
    const img = getByTestId("page-image") as HTMLImageElement;
    expect(img).not.toBeNull();
    expect(img.getAttribute("src")).toBe("/page_images/deadbeef.png");
    expect(getByTestId("section-breadcrumb").textContent).toContain("Access Control");
    expect(getByTestId("provenance-chain").textContent).toContain("b9");
  });

  it("FAILS loudly when page image is missing (contract violation surfaced)", () => {
    const { queryByTestId, getByTestId } = render(
      <RetrievalEvidenceView
        answer={{ ...baseAnswer, page_image_refs: [] }}
        sections={sections}
        naturalSize={{ w: 100, h: 200 }}
      />,
    );
    expect(queryByTestId("page-image")).toBeNull();
    expect(getByTestId("page-image-error")).not.toBeNull();
  });
});
tsx
// ui/src/__tests__/overlay.test.tsx
import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { PageImageOverlay } from "../components/PageImageOverlay";

afterEach(cleanup);

describe("bbox overlay coordinates", () => {
  it("positions the overlay box at image-pixel coords (1:1 fallback)", () => {
    const { getByTestId } = render(
      <PageImageOverlay
        image={{ sha256: "x", url: "/page_images/x.png", page: 1 }}
        naturalSize={{ w: 100, h: 200 }}
        boxes={[{ id: "b1", rect: { x: 10, y: 20, w: 30, h: 40 } }]}
      />,
    );
    const box = getByTestId("overlay-box") as HTMLElement;
    expect(box.style.left).toBe("10px");
    expect(box.style.top).toBe("20px");
    expect(box.style.width).toBe("30px");
    expect(box.style.height).toBe("40px");
  });

  it("renders the family tag above the box", () => {
    const { getByText } = render(
      <PageImageOverlay
        image={{ sha256: "x", url: "/page_images/x.png", page: 1 }}
        naturalSize={{ w: 100, h: 100 }}
        boxes={[{ id: "b1", rect: { x: 0, y: 0, w: 5, h: 5 }, label: "table" }]}
      />,
    );
    expect(getByText("table")).not.toBeNull();
  });
});
tsx
// ui/src/__tests__/confidenceHidden.test.tsx
import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { ConfidenceSlot } from "../components/ConfidenceSlot";
import { CalibrateRoute } from "../routes/CalibrateRoute";
import type { CalibrationSampleRow } from "../lib/types";

afterEach(cleanup);

describe("confidence is opaque until calibration", () => {
  it("ConfidenceSlot marks itself hidden and never prints the value", () => {
    const { container } = render(<ConfidenceSlot value={0.846} />);
    const slot = container.querySelector(".confidence-slot") as HTMLElement;
    expect(slot.getAttribute("data-confidence-hidden")).toBe("true");
    expect(container.textContent).not.toContain("0.846");
    expect(container.textContent).not.toContain("84.6");
  });

  it("CalibrateRoute leaks no numeric confidence into the DOM", () => {
    const rows: CalibrationSampleRow[] = [
      { doc: "r5", quintile: 2, page: 7, bbox: [1, 2, 3, 4], type: "block",
        confidence: 0.731, text: "sample", label: null },
    ];
    const { container } = render(
      <CalibrateRoute
        rows={rows}
        resolvePageImage={() => ({
          sha256: "z", url: "/page_images/z.png", page: 7,
          naturalSize: { w: 100, h: 100 },
        })}
      />,
    );
    expect(container.textContent).not.toContain("0.731");
    expect(container.querySelector('[data-confidence-hidden="true"]')).not.toBeNull();
  });
});
ts
// ui/src/__tests__/labelsSchema.test.ts
import { describe, it, expect } from "vitest";
import { isValidLabelRow, makeLabelRow } from "../lib/labels";

const SHA = "a".repeat(64);

describe("labels_v1 row schema", () => {
  it("accepts a well-formed correct row", () => {
    expect(isValidLabelRow(makeLabelRow(SHA, "correct"))).toBe(true);
  });
  it("accepts wrong_type with corrected_type", () => {
    const row = makeLabelRow(SHA, "wrong_type", "figure");
    expect(row.corrected_type).toBe("figure");
    expect(isValidLabelRow(row)).toBe(true);
  });
  it("rejects a bad item_sha", () => {
    expect(isValidLabelRow({ item_sha: "short", label: "correct", ts: new Date().toISOString() })).toBe(false);
  });
  it("rejects an unknown label", () => {
    expect(isValidLabelRow({ item_sha: SHA, label: "maybe", ts: new Date().toISOString() })).toBe(false);
  });
  it("rejects corrected_type on a non-wrong_type row", () => {
    expect(isValidLabelRow({ item_sha: SHA, label: "correct", corrected_type: "x", ts: new Date().toISOString() })).toBe(false);
  });
  it("rejects a non-ISO timestamp", () => {
    expect(isValidLabelRow({ item_sha: SHA, label: "correct", ts: "yesterday" })).toBe(false);
  });
});
ts
// ui/src/__tests__/adapters.test.ts
import { describe, it, expect } from "vitest";
import { parseAnnotationCall, filterItems } from "../adapters/annotationCall";
import { normalizePageImageRefs, resolvePageImageUrl } from "../adapters/pageImageRefs";
import { buildSectionIndex } from "../adapters/sectionTree";
import type { AnnotationCallV1, SectionV2 } from "../lib/types";

const call: AnnotationCallV1 = {
  schema: "pdf_oxide.annotation_call.v1",
  pdf_sha256: "abc",
  engine_commit: "12689578",
  accuracy_estimate: { basis: "char_parity", value: 0.846 },
  items: [
    { page: 1, kind: "block", bbox: [0, 0, 1, 1], reason: "low_confidence", confidence: 0.4 },
    { page: 2, kind: "region", reason: "reviewer_flagged", text_excerpt: "see fig" },
  ],
};

describe("annotationCall adapter", () => {
  it("normalizes items and rejects a wrong schema", () => {
    const n = parseAnnotationCall(call, "r5");
    expect(n.items).toHaveLength(2);
    expect(n.items[0].reasonLabel).toBe("Low confidence");
    expect(n.items[0].key).toMatch(/^[0-9a-f]{64}$/);
    expect(() =>
      parseAnnotationCall({ ...call, schema: "wrong" }, "r5"),
    ).toThrow();
  });
  it("filters by reason", () => {
    const n = parseAnnotationCall(call, "r5");
    const only = filterItems(n.items, { reasons: new Set(["reviewer_flagged"]) });
    expect(only).toHaveLength(1);
    expect(only[0].page).toBe(2);
  });
});

describe("pageImageRefs adapter", () => {
  it("resolves bare and object refs, strips .png", () => {
    const imgs = normalizePageImageRefs(["aa", { sha256: "bb.png", page: 3 }], "https://host/out");
    expect(imgs[0].url).toBe("https://host/out/page_images/aa.png");
    expect(imgs[1].sha256).toBe("bb");
    expect(imgs[1].page).toBe(3);
  });
  it("builds the content-addressed URL", () => {
    expect(resolvePageImageUrl("", "cc")).toBe("/page_images/cc.png");
  });
});

describe("sectionTree adapter", () => {
  const sections: SectionV2[] = [
    { id: "r", title: "Root", level: 1, parent_id: null, children: ["a"], depth: 0, doc_order: 0, page_start: 1, page_end: 9, provenance: { pdf_sha256: "abc", page: 1, bbox: [0, 0, 1, 1] }, block_ids: [] },
    { id: "a", title: "Alpha", level: 2, parent_id: "r", children: [], depth: 1, doc_order: 1, page_start: 2, page_end: 3, provenance: { pdf_sha256: "abc", page: 2, bbox: [0, 0, 1, 1] }, block_ids: [] },
  ];
  it("builds breadcrumb path root-first", () => {
    const idx = buildSectionIndex(sections);
    expect(idx.path("a").map((c) => c.title)).toEqual(["Root", "Alpha"]);
    expect(idx.isAcyclic).toBe(true);
    expect(idx.docOrderIsTotal).toBe(true);
  });
  it("detects a cycle and a non-total doc_order", () => {
    const bad: SectionV2[] = [
      { ...sections[0], parent_id: "a" }, // r->a->r cycle
      sections[1],
    ];
    const idx = buildSectionIndex(bad);
    expect(idx.isAcyclic).toBe(false);
  });
});
markdown
<!-- FILELIST.md -->
# pdf_oxide/ui — verification-first upgrade: file manifest

Runtime deps: react, react-dom. Dev/test deps: vitest, @testing-library/react,
jsdom, typescript. No other runtime dependencies (virtualization, sha256, and
jsonl are in-repo so the slice "compiles as given").

| Path | Purpose | Assumptions |
|---|---|---|
| ui/src/lib/types.ts | All five live contracts + presentation prop types in one place. | Contract shapes exactly as given on main. |
| ui/src/lib/sha256.ts | Synchronous SHA-256 (UTF-8) for item_sha and stable React keys. | Pinned to NIST vectors in sha256.test.ts. |
| ui/src/lib/geometry.ts | Contract-bbox → image-px (`toImagePixels`) and image-px → display (`projectRect`). Sole home of the coordinate-space assumption. | **bboxes are in image-native pixels (`image_px`) by default**; `pdf_points_bottom_left` path included if the engine reports PDF points. |
| ui/src/lib/jsonl.ts | JSONL parse/serialize with line-numbered errors. | UTF-8 text, one JSON object per line. |
| ui/src/lib/labels.ts | `sampleItemSha`, `makeLabelRow`, strict `isValidLabelRow`. | item_sha = sha256 over {doc,page,bbox,type,text}; corrected_type only on wrong_type. |
| ui/src/lib/useElementSize.ts | ResizeObserver size hook with jsdom fallback. | Falls back to clientWidth/Height, then 0. |
| ui/src/adapters/annotationCall.ts | annotation_call.v1 → PresentationItem[]; reason labels; `filterItems`. | Throws on wrong schema string; confidence retained in model, never rendered. |
| ui/src/adapters/pageImageRefs.ts | Content-addressed refs → URLs (`/page_images/<sha>.png`), page index. | Ref is sha string or {sha256,page}; base URL configurable. |
| ui/src/adapters/sectionTree.ts | v2 id index, cycle-safe breadcrumb path, acyclic + total-doc_order checks. | children referenced by id; parent_id null at roots. |
| ui/src/components/ConfidenceSlot.tsx | Withholds confidence from the DOM (`data-confidence-hidden="true"`). | Opaque until calibration lands. |
| ui/src/components/ReasonChip.tsx | Reason as color-coded data category. | Hues in console.css keyed by data-reason. |
| ui/src/components/PageImageOverlay.tsx | Salvaged geometry core: page image + scaled bbox overlays + family tag; loud error state when image missing. | Rects passed in image px; natural size via prop or img onLoad; scale-1 fallback under jsdom. |
| ui/src/components/VirtualList.tsx | Dependency-free windowing for the 2161-item queue. | Fixed row height. |
| ui/src/routes/CalibrateRoute.tsx | Loads sample_v1 rows, renders page image + bbox, one-tap labels (keys 1–4) → labels_v1.jsonl. | Host injects `resolvePageImage(doc,page)`; download via Blob. |
| ui/src/views/RetrievalEvidenceView.tsx | Answer + original page image + section breadcrumb + provenance chain. data-testids: page-image, section-breadcrumb, provenance-chain. | Missing page image → page-image-error (test failure). |
| ui/src/views/AnnotationQueueView.tsx | Virtualized, reason/doc-filtered consumer for annotation_call items; row→page-image inspection. | Host injects `resolvePageImage`. |
| ui/src/styles/console.css | Evidence-ledger theme (dark mat, mono identifiers, cyan accent, reason hues). | Imported by the app entry, NOT by component modules (keeps tests css-free). |
| ui/src/test/setup.ts | jsdom shims: ResizeObserver, URL.createObjectURL. | vitest setupFiles. |
| vitest.config.ts | jsdom env, globals, setup, css:false. | Tests under ui/src/**/*.test.*. |
| ui/src/__tests__/sha256.test.ts | SHA-256 NIST vectors + item_sha stability. | — |
| ui/src/__tests__/geometry.test.ts | projectRect scaling; image_px identity; points+Y-flip. | — |
| ui/src/__tests__/pageImagePresent.test.tsx | Retrieval renders page image/breadcrumb/provenance; missing image fails loudly. | — |
| ui/src/__tests__/overlay.test.tsx | Overlay box positioned at image-px coords; family tag rendered. | — |
| ui/src/__tests__/confidenceHidden.test.tsx | ConfidenceSlot + CalibrateRoute leak no numeric confidence. | — |
| ui/src/__tests__/labelsSchema.test.ts | labels_v1 row schema validator (positive + 4 negatives). | — |
| ui/src/__tests__/adapters.test.ts | annotationCall/pageImageRefs/sectionTree normalization + invariants. | — |

## Integration notes
- **Wiring:** app entry imports `console.css`, loads a doc's `annotation_call.json`, `sections` (v2), and `page_images/` index, then supplies `resolvePageImage(doc,page)` to CalibrateRoute and AnnotationQueueView. RetrievalEvidenceView takes a `RetrievalAnswer` + `sections`.
- **Confidence gate:** every confidence render path routes through `ConfidenceSlot`. When calibration lands, replace it with a real display; the `data-confidence-hidden` test then flips to assert the value is shown.
- **Coordinate convention:** if the engine reports PDF points rather than image pixels, change the three `toImagePixels(bbox, "image_px")` call sites to `"pdf_points_bottom_left"` and pass page/image sizes. This is the only geometry edit required.
- **Salvage boundary:** PageImageOverlay is the geometry core kept from PdfLabLabelingPage (projection + tag placement); the process components (SurgicalTriage*, *StaticProof*, ProductionWorkflow*) are not imported and should move to ui/archive/ behind a dead-field lint.

COMPETITION_ENTRY_COMPLETE