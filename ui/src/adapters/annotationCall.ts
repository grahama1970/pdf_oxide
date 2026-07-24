import type { BreadcrumbNode } from '../components/pdf-lab/PdfLabLabelingExport'
import {
  type CalibrationPageImageIndex,
  type ResolvedPageImage,
  resolvePageImageRef,
} from './pageImageRefs'
import { type SectionTreeV2, normalizeSectionPath } from './sectionTree'

export type NormalizedBBox = [number, number, number, number]

export interface AnnotationCallItemV1 {
  page: number
  kind: string
  bbox: NormalizedBBox
  reason: string
  confidence: number
  text_excerpt: string
  section_id?: string
}

export interface AnnotationCallV1 {
  schema: 'pdf_oxide.annotation_call.v1'
  items: AnnotationCallItemV1[]
}

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

export const OLD_FLOW_ONLY_FIELDS = Object.freeze([
  'crop_uri',
  'page_image_uri',
  'json_pointer',
  'page_image_hash',
  'task_id',
] as const)

export function normalizeBBox(value: unknown, owner: string): NormalizedBBox {
  if (
    !Array.isArray(value)
    || value.length !== 4
    || !value.every(coordinate => typeof coordinate === 'number' && Number.isFinite(coordinate))
  ) {
    throw new Error(`${owner} bbox must contain four finite numbers`)
  }
  const [x0, y0, x1, y1] = value
  if (x0 < 0 || y0 < 0 || x1 > 1 || y1 > 1 || x0 >= x1 || y0 >= y1) {
    throw new Error(`${owner} bbox must be normalized xyxy coordinates with positive area`)
  }
  return [x0, y0, x1, y1]
}

function assertOpaqueConfidence(value: unknown, owner: string): void {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) {
    throw new Error(`${owner} confidence must be a finite number in [0, 1]`)
  }
}

function sha256(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value)) {
    throw new Error('calibration item_sha must be a lowercase SHA-256')
  }
  return value
}

export function normalizeAnnotationCall(value: unknown): LabelingRegionProps[] {
  if (!value || typeof value !== 'object') throw new Error('annotation call must be an object')
  const call = value as Partial<AnnotationCallV1>
  if (call.schema !== 'pdf_oxide.annotation_call.v1') {
    throw new Error('annotation call schema must be pdf_oxide.annotation_call.v1')
  }
  if (!Array.isArray(call.items)) throw new Error('annotation call items must be an array')
  return call.items.map((item, index) => {
    if (!item || typeof item !== 'object') throw new Error(`annotation item ${index} must be an object`)
    if (!Number.isInteger(item.page) || item.page < 0) throw new Error(`annotation item ${index} page is invalid`)
    if (typeof item.kind !== 'string' || !item.kind) throw new Error(`annotation item ${index} kind is required`)
    if (typeof item.reason !== 'string' || !item.reason) throw new Error(`annotation item ${index} reason is required`)
    if (typeof item.text_excerpt !== 'string') throw new Error(`annotation item ${index} text_excerpt is required`)
    assertOpaqueConfidence(item.confidence, `annotation item ${index}`)
    return {
      id: `annotation-call-${index}`,
      page: item.page,
      family: item.kind,
      bbox: normalizeBBox(item.bbox, `annotation item ${index}`),
      text_hint: item.text_excerpt,
      notes: item.reason,
      origin: 'agent_dispatcher',
    }
  })
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
  assertOpaqueConfidence(item.confidence, 'calibration item')

  const itemSha = sha256(itemShaValue)
  const page = Number(item.page)
  const bbox = normalizeBBox(item.bbox, 'calibration item')
  const normalizedPath = item.section_id && sectionTree
    ? normalizeSectionPath(sectionTree, item.section_id)
    : undefined
  return {
    itemSha,
    doc: item.doc,
    page,
    image: resolvePageImageRef(pageImages, item.doc, page),
    region: {
      id: `calibration-${itemSha}`,
      page,
      family: item.type,
      bbox,
      text_hint: item.text,
      breadcrumb: normalizedPath?.breadcrumb,
      breadcrumb_nodes: normalizedPath?.breadcrumbNodes,
      origin: 'agent_dispatcher',
    },
    provenance: normalizedPath?.provenance ?? [],
  }
}
