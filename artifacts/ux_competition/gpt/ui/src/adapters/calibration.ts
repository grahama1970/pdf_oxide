import { normalizeBboxXywh, type BboxXywh } from './pageImageRefs'

export type CalibrationLabel = 'correct' | 'wrong_type' | 'wrong_bounds' | 'not_an_element'

export interface CalibrationSampleItem {
  doc: string
  quintile: string
  page: number
  bbox: BboxXywh
  type: string
  /** Deliberately retained in memory only. UI code must never render it. */
  confidence: number
  text: string
  label: null
  pageImageRefs?: unknown
}

export interface CalibrationLabelRow {
  item_sha: string
  label: CalibrationLabel
  corrected_type?: string
  ts: string
}

const LABELS = new Set<CalibrationLabel>(['correct', 'wrong_type', 'wrong_bounds', 'not_an_element'])
const SHA256_RE = /^[a-f0-9]{64}$/i

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.trim().length === 0) throw new Error(`${field} is required`)
  return value.trim()
}

function finiteNumber(value: unknown, field: string): number {
  const numberValue = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numberValue)) throw new Error(`${field} must be finite`)
  return numberValue
}

function normalizeSampleItem(raw: unknown, index: number): CalibrationSampleItem {
  const record = asRecord(raw)
  if (!record) throw new Error(`sample row ${index + 1} must be an object`)
  const page = finiteNumber(record.page, `sample row ${index + 1}.page`)
  if (!Number.isInteger(page) || page < 0) throw new Error(`sample row ${index + 1}.page must be a non-negative integer`)
  const confidence = finiteNumber(record.confidence, `sample row ${index + 1}.confidence`)
  if (confidence < 0 || confidence > 1) throw new Error(`sample row ${index + 1}.confidence must be between 0 and 1`)
  if (record.label !== null) throw new Error(`sample row ${index + 1}.label must be null`)

  return {
    doc: requiredString(record.doc, `sample row ${index + 1}.doc`),
    quintile: requiredString(record.quintile, `sample row ${index + 1}.quintile`),
    page,
    bbox: normalizeBboxXywh(record.bbox)!,
    type: requiredString(record.type, `sample row ${index + 1}.type`),
    confidence,
    text: typeof record.text === 'string' ? record.text : '',
    label: null,
    pageImageRefs: record.page_image_refs,
  }
}

export function parseCalibrationSample(input: string | unknown): CalibrationSampleItem[] {
  let rows: unknown[]
  if (typeof input !== 'string') {
    rows = Array.isArray(input) ? input : [input]
  } else {
    const trimmed = input.trim()
    if (!trimmed) return []
    if (trimmed.startsWith('[')) {
      const parsed = JSON.parse(trimmed) as unknown
      if (!Array.isArray(parsed)) throw new Error('calibration sample JSON must be an array')
      rows = parsed
    } else {
      rows = trimmed.split(/\r?\n/).filter(Boolean).map((line, index) => {
        try {
          return JSON.parse(line) as unknown
        } catch (error) {
          throw new Error(`invalid JSONL on line ${index + 1}: ${error instanceof Error ? error.message : String(error)}`)
        }
      })
    }
  }
  return rows.map(normalizeSampleItem)
}

function canonicalSample(item: CalibrationSampleItem): string {
  return JSON.stringify({
    doc: item.doc,
    quintile: item.quintile,
    page: item.page,
    bbox: [...item.bbox],
    type: item.type,
    text: item.text,
  })
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export async function calibrationItemSha(item: CalibrationSampleItem): Promise<string> {
  const data = new TextEncoder().encode(canonicalSample(item))
  const digest = await globalThis.crypto.subtle.digest('SHA-256', data)
  return bytesToHex(new Uint8Array(digest))
}

export function buildCalibrationLabelRow(
  itemSha: string,
  label: CalibrationLabel,
  correctedType?: string,
  timestamp = new Date().toISOString(),
): CalibrationLabelRow {
  const normalizedSha = itemSha.trim().toLowerCase()
  if (!SHA256_RE.test(normalizedSha)) throw new Error('item_sha must be a SHA-256 digest')
  if (!LABELS.has(label)) throw new Error(`unsupported calibration label: ${label}`)
  if (Number.isNaN(Date.parse(timestamp))) throw new Error('ts must be an ISO-8601 timestamp')

  const normalizedCorrectedType = correctedType?.trim()
  if (label === 'wrong_type' && !normalizedCorrectedType) {
    throw new Error('corrected_type is required for wrong_type')
  }

  return {
    item_sha: normalizedSha,
    label,
    ...(normalizedCorrectedType ? { corrected_type: normalizedCorrectedType } : {}),
    ts: timestamp,
  }
}

export function isCalibrationLabelRow(value: unknown): value is CalibrationLabelRow {
  const record = asRecord(value)
  if (!record) return false
  if (typeof record.item_sha !== 'string' || !SHA256_RE.test(record.item_sha)) return false
  if (!LABELS.has(record.label as CalibrationLabel)) return false
  if (typeof record.ts !== 'string' || Number.isNaN(Date.parse(record.ts))) return false
  if (record.label === 'wrong_type' && (typeof record.corrected_type !== 'string' || !record.corrected_type.trim())) return false
  if (record.corrected_type != null && typeof record.corrected_type !== 'string') return false
  return Object.keys(record).every((key) => ['item_sha', 'label', 'corrected_type', 'ts'].includes(key))
}

export function serializeLabelRows(rows: readonly CalibrationLabelRow[]): string {
  rows.forEach((row, index) => {
    if (!isCalibrationLabelRow(row)) throw new Error(`invalid calibration label row at index ${index}`)
  })
  return rows.map((row) => JSON.stringify(row)).join('\n') + (rows.length ? '\n' : '')
}
