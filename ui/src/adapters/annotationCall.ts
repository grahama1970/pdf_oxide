import type { BboxXywh } from './pageImageRefs'
import type { BreadcrumbNode } from '../components/pdf-lab/PdfLabLabelingExport'
import {
  type CalibrationPageImageIndex,
  type ResolvedPageImage,
  resolvePageImageRef,
} from './pageImageRefs'
import { type SectionTreeV2, normalizeSectionPath } from './sectionTree'

export const ANNOTATION_CALL_SCHEMA = 'pdf_oxide.annotation_call.v1' as const

export type AnnotationKind = 'block' | 'region' | 'page'
export type AnnotationReason =
  | 'low_confidence'
  | 'char_parity_deficit'
  | 'unadjudicated_residual'
  | 'reviewer_flagged'

export interface RawAnnotationCallItem {
  page: number
  kind: AnnotationKind
  bbox?: [number, number, number, number]
  reason: AnnotationReason
  confidence?: number
  current_type?: string
  text_excerpt?: string
  page_image_refs?: unknown
  [key: string]: unknown
}

export interface RawAnnotationCall {
  schema: typeof ANNOTATION_CALL_SCHEMA
  pdf_sha256: string
  engine_commit: string
  accuracy_estimate: {
    basis: string
    value: number
  }
  items: RawAnnotationCallItem[]
  doc?: string
  document?: string
  [key: string]: unknown
}

export interface AnnotationQueueItem {
  id: string
  itemSha256: string
  callSha256: string
  sourceIndex: number
  documentId: string
  pdfSha256: string
  engineCommit: string
  accuracyBasis: string
  accuracyValue: number
  page: number
  kind: AnnotationKind
  reason: AnnotationReason
  bbox: readonly [number, number, number, number] | null
  normalizedBbox: BboxXywh | null
  currentType: string | null
  textExcerpt: string | null
  /** Kept in the model for calibration work; never render this value. */
  confidence: number | null
  pageImageRefs: unknown
  raw: RawAnnotationCallItem
}

export interface NormalizedAnnotationCall {
  schema: typeof ANNOTATION_CALL_SCHEMA
  documentId: string
  pdfSha256: string
  engineCommit: string
  accuracyBasis: string
  accuracyValue: number
  items: AnnotationQueueItem[]
}

const SHA256_RE = /^[a-f0-9]{64}$/i
const KINDS = new Set<AnnotationKind>(['block', 'region', 'page'])
const REASONS = new Set<AnnotationReason>([
  'low_confidence',
  'char_parity_deficit',
  'unadjudicated_residual',
  'reviewer_flagged',
])

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function normalizePdfSha(value: unknown): string {
  if (typeof value !== 'string') throw new Error('annotation_call.pdf_sha256 must be a string')
  const normalized = value.trim().replace(/^sha256:/i, '').toLowerCase()
  if (!SHA256_RE.test(normalized)) throw new Error('annotation_call.pdf_sha256 must be a SHA-256 digest')
  return normalized
}

function finiteNumber(value: unknown, field: string): number {
  const parsed = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(parsed)) throw new Error(`${field} must be finite`)
  return parsed
}

function finitePage(value: unknown): number {
  const parsed = finiteNumber(value, 'annotation item page')
  if (!Number.isInteger(parsed) || parsed < 0) throw new Error('annotation item page must be a non-negative integer')
  return parsed
}

function normalizeRawBbox(value: unknown): readonly [number, number, number, number] | null {
  if (value == null) return null
  if (!Array.isArray(value) || value.length !== 4) throw new Error('annotation item bbox must have four numbers')
  const numbers = value.map((part) => finiteNumber(part, 'annotation item bbox'))
  const [x, y, width, height] = numbers
  if (width <= 0 || height <= 0 || x < 0 || y < 0) throw new Error('annotation item bbox must be a positive [x,y,width,height] rectangle')
  return [x, y, width, height]
}

function maybeNormalizedBbox(
  bbox: readonly [number, number, number, number] | null,
): BboxXywh | null {
  if (!bbox) return null
  const [x, y, width, height] = bbox
  if (x + width > 1.000001 || y + height > 1.000001) return null
  return [x, y, width, height]
}

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null
}

export function normalizeAnnotationCall(raw: unknown, sourceName?: string): NormalizedAnnotationCall {
  const record = asRecord(raw)
  if (!record) throw new Error('annotation_call must be an object')
  if (record.schema !== ANNOTATION_CALL_SCHEMA) {
    throw new Error(`unsupported annotation_call schema: ${String(record.schema)}`)
  }

  const pdfSha256 = normalizePdfSha(record.pdf_sha256)
  const engineCommit = stringOrNull(record.engine_commit)
  if (!engineCommit) throw new Error('annotation_call.engine_commit is required')

  const accuracy = asRecord(record.accuracy_estimate)
  if (!accuracy) throw new Error('annotation_call.accuracy_estimate is required')
  const accuracyBasis = stringOrNull(accuracy.basis)
  if (!accuracyBasis) throw new Error('annotation_call.accuracy_estimate.basis is required')
  const accuracyValue = finiteNumber(accuracy.value, 'annotation_call.accuracy_estimate.value')

  const documentId = stringOrNull(record.doc)
    ?? stringOrNull(record.document)
    ?? sourceName
    ?? pdfSha256.slice(0, 12)

  if (!Array.isArray(record.items)) throw new Error('annotation_call.items must be an array')
  const items = record.items.map((rawItem, index): AnnotationQueueItem => {
    const item = asRecord(rawItem)
    if (!item) throw new Error(`annotation_call.items[${index}] must be an object`)
    if (!KINDS.has(item.kind as AnnotationKind)) throw new Error(`annotation_call.items[${index}].kind is invalid`)
    if (!REASONS.has(item.reason as AnnotationReason)) throw new Error(`annotation_call.items[${index}].reason is invalid`)

    const page = finitePage(item.page)
    const bbox = normalizeRawBbox(item.bbox)
    const confidence = item.confidence == null
      ? null
      : finiteNumber(item.confidence, `annotation_call.items[${index}].confidence`)
    if (confidence !== null && (confidence < 0 || confidence > 1)) {
      throw new Error(`annotation_call.items[${index}].confidence must be between 0 and 1`)
    }

    const kind = item.kind as AnnotationKind
    const reason = item.reason as AnnotationReason
    const itemId = stringOrNull(item.item_id) ?? `${pdfSha256}:${page}:${kind}:${reason}:${index}`
    const itemSha256 = item.item_sha256 == null
      ? pdfSha256
      : normalizePdfSha(item.item_sha256)
    const callSha256 = item.call_sha256 == null && record.call_sha256 == null
      ? pdfSha256
      : normalizePdfSha(item.call_sha256 ?? record.call_sha256)
    return {
      id: itemId,
      itemSha256,
      callSha256,
      sourceIndex: index,
      documentId,
      pdfSha256,
      engineCommit,
      accuracyBasis,
      accuracyValue,
      page,
      kind,
      reason,
      bbox,
      normalizedBbox: maybeNormalizedBbox(bbox),
      currentType: stringOrNull(item.current_type),
      textExcerpt: stringOrNull(item.text_excerpt),
      confidence,
      pageImageRefs: item.page_image_refs,
      raw: item as RawAnnotationCallItem,
    }
  })

  return {
    schema: ANNOTATION_CALL_SCHEMA,
    documentId,
    pdfSha256,
    engineCommit,
    accuracyBasis,
    accuracyValue,
    items,
  }
}

function flattenCallPayload(raw: unknown): Array<{ raw: unknown; sourceName?: string }> {
  if (Array.isArray(raw)) return raw.map((entry) => ({ raw: entry }))
  const record = asRecord(raw)
  if (!record) return [{ raw }]
  if (record.schema === ANNOTATION_CALL_SCHEMA) return [{ raw }]

  const calls = record.calls ?? record.annotation_calls ?? record.documents
  if (!Array.isArray(calls)) return [{ raw }]
  return calls.map((entry, index) => {
    const callRecord = asRecord(entry)
    return {
      raw: callRecord?.payload ?? callRecord?.call ?? entry,
      sourceName: stringOrNull(callRecord?.doc) ?? stringOrNull(callRecord?.name) ?? `document-${index + 1}`,
    }
  })
}

export function normalizeAnnotationCallCollection(raw: unknown): NormalizedAnnotationCall[] {
  return flattenCallPayload(raw).map(({ raw: call, sourceName }) => normalizeAnnotationCall(call, sourceName))
}

export function flattenAnnotationItems(calls: readonly NormalizedAnnotationCall[]): AnnotationQueueItem[] {
  return calls.flatMap((call) => call.items)
}

export function annotationReasonLabel(reason: AnnotationReason): string {
  switch (reason) {
    case 'low_confidence': return 'Low-confidence classification'
    case 'char_parity_deficit': return 'Character parity deficit'
    case 'unadjudicated_residual': return 'Unadjudicated residual'
    case 'reviewer_flagged': return 'Reviewer flagged'
  }
}

// Existing PdfLabLabelingPage compatibility. The winning queue consumes the
// strict normalized call above; the mature labeling renderer still expects
// normalized xyxy Region props and server-supplied item identities.
export type NormalizedBBox = [number, number, number, number]

export interface CalibrationSampleItem {
  doc: string
  quintile: number
  page: number
  bbox: NormalizedBBox
  type: string
  confidence: number
  text: string
  label: null
  section_id?: string
}

export interface LabelingRegionProps {
  id: string
  page: number
  family: string
  bbox: NormalizedBBox
  text_hint: string
  notes?: string
  breadcrumb?: string[]
  breadcrumb_nodes?: BreadcrumbNode[]
  origin: 'agent_dispatcher'
}

export interface NormalizedLabelingItem {
  itemSha: string
  doc: string
  page: number
  image: ResolvedPageImage
  region: LabelingRegionProps
  provenance: unknown[]
}

function normalizeCompatibilityBbox(value: unknown, owner: string): NormalizedBBox {
  if (
    !Array.isArray(value)
    || value.length !== 4
    || !value.every((coordinate) => typeof coordinate === 'number' && Number.isFinite(coordinate))
  ) {
    throw new Error(`${owner} bbox must contain four finite numbers`)
  }
  const [x0, y0, x1, y1] = value
  if (x0 < 0 || y0 < 0 || x1 > 1 || y1 > 1 || x0 >= x1 || y0 >= y1) {
    throw new Error(`${owner} bbox must be normalized xyxy coordinates with positive area`)
  }
  return [x0, y0, x1, y1]
}

function opaqueConfidence(value: unknown, owner: string): void {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) {
    throw new Error(`${owner} confidence must be a finite number in [0, 1]`)
  }
}

export function normalizeCalibrationItem(
  itemValue: unknown,
  itemShaValue: unknown,
  pageImages: CalibrationPageImageIndex,
  sectionTree?: SectionTreeV2,
): NormalizedLabelingItem {
  if (!itemValue || typeof itemValue !== 'object') throw new Error('calibration item must be an object')
  const item = itemValue as Partial<CalibrationSampleItem>
  if (typeof item.doc !== 'string' || !item.doc) throw new Error('calibration item doc is required')
  if (!Number.isInteger(item.quintile) || Number(item.quintile) < 0 || Number(item.quintile) > 4) {
    throw new Error('calibration item quintile must be an integer in [0, 4]')
  }
  if (!Number.isInteger(item.page) || Number(item.page) < 0) throw new Error('calibration item page is invalid')
  if (typeof item.type !== 'string' || !item.type) throw new Error('calibration item type is required')
  if (typeof item.text !== 'string') throw new Error('calibration item text is required')
  if (item.label !== null) throw new Error('calibration sample item label must be null')
  opaqueConfidence(item.confidence, 'calibration item')
  if (typeof itemShaValue !== 'string' || !/^[0-9a-f]{64}$/.test(itemShaValue)) {
    throw new Error('calibration item_sha must be a lowercase SHA-256')
  }

  const page = Number(item.page)
  const normalizedPath = item.section_id && sectionTree
    ? normalizeSectionPath(sectionTree, item.section_id)
    : undefined
  return {
    itemSha: itemShaValue,
    doc: item.doc,
    page,
    image: resolvePageImageRef(pageImages, item.doc, page),
    region: {
      id: `calibration-${itemShaValue}`,
      page,
      family: item.type,
      bbox: normalizeCompatibilityBbox(item.bbox, 'calibration item'),
      text_hint: item.text,
      breadcrumb: normalizedPath?.breadcrumb,
      breadcrumb_nodes: normalizedPath?.breadcrumbNodes,
      origin: 'agent_dispatcher',
    },
    provenance: normalizedPath?.provenance ?? [],
  }
}
