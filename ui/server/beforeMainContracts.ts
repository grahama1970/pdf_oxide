import { createHash } from 'crypto'
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  writeFileSync,
} from 'fs'
import { basename, dirname, relative, resolve } from 'path'

export type JsonRecord = Record<string, unknown>

export const CALIBRATION_LABELS = [
  'correct',
  'wrong_type',
  'wrong_bounds',
  'not_an_element',
] as const
export type CalibrationLabel = typeof CALIBRATION_LABELS[number]

export const ANNOTATION_DECISIONS = [
  'accept',
  'defer',
  'correct_type',
  'correct_bounds',
] as const
export type AnnotationDecision = typeof ANNOTATION_DECISIONS[number]

export const ELEMENT_TYPES = [
  'Body',
  'Caption',
  'Code',
  'Equation',
  'Figure',
  'Footnote',
  'Form',
  'Header',
  'List',
  'Reference',
  'Section',
  'Subtitle',
  'Table',
  'Title',
  'block',
  'page',
  'region',
] as const

export const PRIORITY_ORDER = [
  'char_parity_deficit',
  'reviewer_flagged',
  'low_confidence',
] as const
export type QueuePriority = typeof PRIORITY_ORDER[number]
export const QUEUE_POLICY = {
  schema: 'pdf_oxide.annotation_queue_policy.v1',
  priority_order: PRIORITY_ORDER,
  service_policy: 'all_items_servable',
} as const

const SHA256_RE = /^[0-9a-f]{64}$/
const IDEMPOTENCY_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$/

export class ContractError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message = code,
  ) {
    super(message)
  }
}

function asRecord(value: unknown, field: string): JsonRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ContractError(400, `invalid_${field}`, `${field} must be an object`)
  }
  return value as JsonRecord
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new ContractError(400, `invalid_${field}`, `${field} is required`)
  }
  return value.trim()
}

function lowerSha(value: unknown, field: string): string {
  const normalized = requiredString(value, field).toLowerCase()
  if (!SHA256_RE.test(normalized)) {
    throw new ContractError(400, `invalid_${field}`, `${field} must be a lowercase SHA-256`)
  }
  return normalized
}

function isoTimestamp(value: unknown, field = 'ts'): string {
  const timestamp = requiredString(value, field)
  if (Number.isNaN(Date.parse(timestamp)) || new Date(timestamp).toISOString() !== timestamp) {
    throw new ContractError(400, `invalid_${field}`, `${field} must be an ISO-8601 UTC timestamp`)
  }
  return timestamp
}

function idempotencyKey(value: unknown): string {
  const key = requiredString(value, 'idempotency_key')
  if (!IDEMPOTENCY_RE.test(key)) {
    throw new ContractError(400, 'invalid_idempotency_key')
  }
  return key
}

function sortedJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortedJsonValue)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.entries(value as JsonRecord)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, child]) => [key, sortedJsonValue(child)]),
  )
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortedJsonValue(value))
}

export function sha256(value: string | Buffer): string {
  return createHash('sha256').update(value).digest('hex')
}

export function readJsonLines(path: string): JsonRecord[] {
  if (!existsSync(path)) return []
  return readFileSync(path, 'utf-8')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      try {
        return asRecord(JSON.parse(line) as unknown, `jsonl_row_${index + 1}`)
      } catch (error) {
        if (error instanceof ContractError) throw error
        throw new ContractError(422, 'invalid_jsonl', `${path}:${index + 1}: ${String(error)}`)
      }
    })
}

function appendJsonLine(path: string, row: JsonRecord): void {
  mkdirSync(dirname(path), { recursive: true })
  appendFileSync(path, `${JSON.stringify(row)}\n`, { encoding: 'utf-8', flag: 'a' })
}

function writeTextAtomically(path: string, text: string): void {
  mkdirSync(dirname(path), { recursive: true })
  const temporary = `${path}.${process.pid}.tmp`
  writeFileSync(temporary, text, 'utf-8')
  renameSync(temporary, path)
}

export interface CalibrationSampleEntry {
  item_sha: string
  item: JsonRecord
}

export interface CalibrationEvent extends JsonRecord {
  schema: 'pdf_oxide.calibration_label_event.v1'
  event_id: string
  idempotency_key: string
  action: 'label' | 'undo'
  item_sha: string
  label?: CalibrationLabel
  corrected_type?: string
  revision_of?: string
  ts: string
  request_sha256: string
}

export interface CalibrationProjectionRow extends JsonRecord {
  item_sha: string
  label: CalibrationLabel
  corrected_type?: string
  ts: string
  event_id: string
}

function parseCalibrationEvents(path: string): CalibrationEvent[] {
  return readJsonLines(path).map((row, index) => {
    if (
      row.schema !== 'pdf_oxide.calibration_label_event.v1'
      || typeof row.event_id !== 'string'
      || typeof row.idempotency_key !== 'string'
      || (row.action !== 'label' && row.action !== 'undo')
      || typeof row.item_sha !== 'string'
      || typeof row.ts !== 'string'
      || typeof row.request_sha256 !== 'string'
    ) {
      throw new ContractError(422, 'invalid_calibration_event_store', `invalid event row ${index + 1}`)
    }
    return row as CalibrationEvent
  })
}

export interface CalibrationState {
  active: Map<string, CalibrationEvent>
  stacks: Map<string, CalibrationEvent[]>
}

export function projectCalibrationEvents(events: readonly CalibrationEvent[]): CalibrationState {
  const stacks = new Map<string, CalibrationEvent[]>()
  const seenIds = new Set<string>()
  for (const event of events) {
    if (seenIds.has(event.event_id)) {
      throw new ContractError(422, 'duplicate_calibration_event_id')
    }
    seenIds.add(event.event_id)
    const stack = stacks.get(event.item_sha) ?? []
    const current = stack.at(-1)
    if (event.action === 'undo') {
      if (!current || event.revision_of !== current.event_id) {
        throw new ContractError(422, 'invalid_calibration_undo_chain')
      }
      stack.pop()
    } else {
      if (current && event.revision_of !== current.event_id) {
        throw new ContractError(422, 'invalid_calibration_revision_chain')
      }
      if (!current && event.revision_of) {
        throw new ContractError(422, 'orphan_calibration_revision')
      }
      stack.push(event)
    }
    stacks.set(event.item_sha, stack)
  }
  const active = new Map<string, CalibrationEvent>()
  for (const [itemSha, stack] of stacks) {
    const current = stack.at(-1)
    if (current) active.set(itemSha, current)
  }
  return { active, stacks }
}

function calibrationProjection(
  entries: readonly CalibrationSampleEntry[],
  state: CalibrationState,
): CalibrationProjectionRow[] {
  return entries.flatMap((entry) => {
    const event = state.active.get(entry.item_sha)
    if (!event?.label) return []
    return [{
      item_sha: entry.item_sha,
      label: event.label,
      ...(event.corrected_type ? { corrected_type: event.corrected_type } : {}),
      ts: event.ts,
      event_id: event.event_id,
    }]
  })
}

function writeCalibrationProjection(
  path: string,
  entries: readonly CalibrationSampleEntry[],
  state: CalibrationState,
): CalibrationProjectionRow[] {
  const rows = calibrationProjection(entries, state)
  writeTextAtomically(path, rows.map((row) => JSON.stringify(row)).join('\n') + (rows.length ? '\n' : ''))
  return rows
}

function calibrationCursor(
  entries: readonly CalibrationSampleEntry[],
  state: CalibrationState,
): { index: number; item_sha: string | null; resolved: number; total: number } {
  const index = entries.findIndex((entry) => !state.active.has(entry.item_sha))
  return {
    index: index < 0 ? Math.max(0, entries.length - 1) : index,
    item_sha: index < 0 ? null : entries[index].item_sha,
    resolved: state.active.size,
    total: entries.length,
  }
}

export function getCalibrationContract(
  eventsPath: string,
  labelsPath: string,
  entries: readonly CalibrationSampleEntry[],
): JsonRecord {
  const events = parseCalibrationEvents(eventsPath)
  const state = projectCalibrationEvents(events)
  const labels = writeCalibrationProjection(labelsPath, entries, state)
  return {
    schema: 'pdf_oxide.calibration_events_response.v1',
    events,
    labels,
    cursor: calibrationCursor(entries, state),
  }
}

export function appendCalibrationEvent(
  raw: unknown,
  eventsPath: string,
  labelsPath: string,
  entries: readonly CalibrationSampleEntry[],
): { event: CalibrationEvent; duplicate: boolean; response: JsonRecord } {
  const input = asRecord(raw, 'calibration_event')
  const allowed = new Set([
    'action',
    'corrected_type',
    'idempotency_key',
    'item_sha',
    'label',
    'revision_of',
    'ts',
  ])
  if (Object.keys(input).some((key) => !allowed.has(key))) {
    throw new ContractError(400, 'unexpected_calibration_event_field')
  }
  const normalized: JsonRecord = {
    action: input.action,
    idempotency_key: idempotencyKey(input.idempotency_key),
    item_sha: lowerSha(input.item_sha, 'item_sha'),
    ts: isoTimestamp(input.ts),
  }
  if (input.revision_of !== undefined) normalized.revision_of = lowerSha(input.revision_of, 'revision_of')
  if (input.label !== undefined) normalized.label = input.label
  if (input.corrected_type !== undefined) normalized.corrected_type = requiredString(input.corrected_type, 'corrected_type')
  const requestSha = sha256(canonicalJson(normalized))

  const events = parseCalibrationEvents(eventsPath)
  const priorKey = events.find((event) => event.idempotency_key === normalized.idempotency_key)
  if (priorKey) {
    if (priorKey.request_sha256 !== requestSha) {
      throw new ContractError(409, 'idempotency_key_reused')
    }
    return {
      event: priorKey,
      duplicate: true,
      response: getCalibrationContract(eventsPath, labelsPath, entries),
    }
  }

  const known = new Set(entries.map((entry) => entry.item_sha))
  if (!known.has(String(normalized.item_sha))) {
    throw new ContractError(409, 'unknown_or_stale_item_sha')
  }
  if (normalized.action !== 'label' && normalized.action !== 'undo') {
    throw new ContractError(400, 'invalid_calibration_action')
  }
  const state = projectCalibrationEvents(events)
  const current = state.active.get(String(normalized.item_sha))
  if (normalized.action === 'undo') {
    if (normalized.label !== undefined || normalized.corrected_type !== undefined) {
      throw new ContractError(400, 'undo_must_not_include_label')
    }
    if (!current || normalized.revision_of !== current.event_id) {
      throw new ContractError(409, 'stale_calibration_revision')
    }
  } else {
    if (!CALIBRATION_LABELS.includes(normalized.label as CalibrationLabel)) {
      throw new ContractError(400, 'invalid_calibration_label')
    }
    if (normalized.label === 'wrong_type' && !normalized.corrected_type) {
      throw new ContractError(400, 'corrected_type_required')
    }
    if (normalized.label !== 'wrong_type' && normalized.corrected_type) {
      throw new ContractError(400, 'corrected_type_not_allowed')
    }
    if (current && normalized.revision_of !== current.event_id) {
      throw new ContractError(409, 'calibration_amendment_requires_revision_of')
    }
    if (!current && normalized.revision_of) {
      throw new ContractError(409, 'orphan_calibration_revision')
    }
  }

  const eventId = sha256(canonicalJson({
    schema: 'pdf_oxide.calibration_label_event.v1',
    ...normalized,
    request_sha256: requestSha,
  }))
  const event = {
    schema: 'pdf_oxide.calibration_label_event.v1',
    event_id: eventId,
    ...normalized,
    request_sha256: requestSha,
  } as CalibrationEvent
  appendJsonLine(eventsPath, event)
  const response = getCalibrationContract(eventsPath, labelsPath, entries)
  return { event, duplicate: false, response }
}

export interface AnnotationSource {
  path: string
  relative_path: string
  doc_id: string
  call_sha256: string
  payload: JsonRecord
}

export interface QueueItemRef extends JsonRecord {
  item_id: string
  item_sha256: string
  call_sha256: string
  source_path: string
  source_index: number
  doc_id: string
  page: number
  reason: string
}

export interface AnnotationQueueManifest extends JsonRecord {
  schema: 'pdf_oxide.annotation_queue_manifest.v1'
  priority_order: QueuePriority[]
  counts: Record<QueuePriority | 'total', number>
  source_hashes: Record<string, string>
  items: QueueItemRef[]
}

export function loadAnnotationSources(artifactsRoot: string): AnnotationSource[] {
  const callsRoot = resolve(artifactsRoot, 'annotation-calls')
  if (!existsSync(callsRoot)) return []
  return readdirSync(callsRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .flatMap((entry) => {
      const path = resolve(callsRoot, entry.name, 'annotation_call.json')
      if (!existsSync(path)) return []
      const bytes = readFileSync(path)
      const payload = asRecord(JSON.parse(bytes.toString('utf-8')) as unknown, 'annotation_call')
      return [{
        path,
        relative_path: relative(artifactsRoot, path),
        doc_id: entry.name,
        call_sha256: sha256(bytes),
        payload,
      }]
    })
    .sort((left, right) => left.relative_path.localeCompare(right.relative_path))
}

function queueItemRef(source: AnnotationSource, raw: unknown, sourceIndex: number): QueueItemRef {
  const item = asRecord(raw, 'annotation_call_item')
  const reason = requiredString(item.reason, 'reason')
  const page = Number(item.page)
  if (!Number.isInteger(page) || page < 0) throw new ContractError(422, 'invalid_annotation_page')
  const itemSha = sha256(canonicalJson({
    call_sha256: source.call_sha256,
    source_index: sourceIndex,
    item,
  }))
  return {
    item_id: `${source.call_sha256}:${sourceIndex}`,
    item_sha256: itemSha,
    call_sha256: source.call_sha256,
    source_path: source.relative_path,
    source_index: sourceIndex,
    doc_id: source.doc_id,
    page,
    reason,
  }
}

export function buildAnnotationQueueManifest(artifactsRoot: string): AnnotationQueueManifest {
  const sources = loadAnnotationSources(artifactsRoot)
  const items: QueueItemRef[] = []
  for (const source of sources) {
    const rawItems = source.payload.items
    if (!Array.isArray(rawItems)) throw new ContractError(422, 'annotation_items_missing')
    rawItems.forEach((raw, index) => {
      const ref = queueItemRef(source, raw, index)
      if (!PRIORITY_ORDER.includes(ref.reason as QueuePriority)) {
        throw new ContractError(422, 'unclassified_annotation_reason', ref.reason)
      }
      items.push(ref)
    })
  }
  items.sort((left, right) => {
    const priority = PRIORITY_ORDER.indexOf(left.reason as QueuePriority)
      - PRIORITY_ORDER.indexOf(right.reason as QueuePriority)
    return priority
      || left.source_path.localeCompare(right.source_path)
      || left.source_index - right.source_index
  })
  const counts = Object.fromEntries([
    ['total', items.length],
    ...PRIORITY_ORDER.map((reason) => [
      reason,
      items.filter((item) => item.reason === reason).length,
    ]),
  ]) as Record<QueuePriority | 'total', number>
  return {
    schema: 'pdf_oxide.annotation_queue_manifest.v1',
    priority_order: [...PRIORITY_ORDER],
    counts,
    source_hashes: Object.fromEntries(sources.map((source) => [source.relative_path, source.call_sha256])),
    items,
  }
}

export function writeAnnotationQueueManifest(artifactsRoot: string, outputPath: string): AnnotationQueueManifest {
  const manifest = buildAnnotationQueueManifest(artifactsRoot)
  writeTextAtomically(outputPath, `${JSON.stringify(manifest, null, 2)}\n`)
  return manifest
}

export function verifyAnnotationQueueManifest(
  artifactsRoot: string,
  manifestPath: string,
): { manifest: AnnotationQueueManifest; sources: AnnotationSource[] } {
  const persisted = asRecord(JSON.parse(readFileSync(manifestPath, 'utf-8')) as unknown, 'annotation_queue_manifest')
  const expected = buildAnnotationQueueManifest(artifactsRoot)
  if (canonicalJson(persisted) !== canonicalJson(expected)) {
    throw new ContractError(422, 'stale_annotation_queue_manifest')
  }
  if (
    expected.counts.total !== 2161
    || expected.counts.char_parity_deficit !== 54
    || expected.counts.reviewer_flagged !== 5
    || expected.counts.low_confidence !== 2102
  ) {
    throw new ContractError(
      422,
      'unexpected_annotation_queue_counts',
      `expected 54/5/2102 (2161 total), received ${JSON.stringify(expected.counts)}`,
    )
  }
  if (expected.items.length !== expected.counts.total) {
    throw new ContractError(422, 'annotation_queue_item_count_mismatch')
  }
  return { manifest: expected, sources: loadAnnotationSources(artifactsRoot) }
}

export function prioritizedQueueResponse(
  artifactsRoot: string,
  manifestPath: string,
): JsonRecord {
  const { manifest, sources } = verifyAnnotationQueueManifest(artifactsRoot, manifestPath)
  const bySource = new Map(sources.map((source) => [source.relative_path, source]))
  const calls = PRIORITY_ORDER.flatMap((reason) => {
    const selectedBySource = new Map<string, QueueItemRef[]>()
    for (const item of manifest.items.filter((candidate) => candidate.reason === reason)) {
      const rows = selectedBySource.get(item.source_path) ?? []
      rows.push(item)
      selectedBySource.set(item.source_path, rows)
    }
    return [...selectedBySource.entries()].map(([sourcePath, refs]) => {
      const source = bySource.get(sourcePath)
      if (!source) throw new ContractError(422, 'queue_source_missing')
      const rawItems = source.payload.items
      if (!Array.isArray(rawItems)) throw new ContractError(422, 'annotation_items_missing')
      return {
        ...source.payload,
        doc: source.doc_id,
        call_sha256: source.call_sha256,
        priority_group: reason,
        items: refs.map((ref) => ({
          ...asRecord(rawItems[ref.source_index], 'annotation_call_item'),
          item_id: ref.item_id,
          item_sha256: ref.item_sha256,
          call_sha256: ref.call_sha256,
          source_index: ref.source_index,
          priority_group: reason,
        })),
      }
    })
  })
  return {
    schema: 'pdf_oxide.annotation_queue_response.v1',
    priority_order: manifest.priority_order,
    source_hashes: manifest.source_hashes,
    counts: manifest.counts,
    calls,
  }
}

export interface AnnotationDecisionEvent extends JsonRecord {
  schema: 'pdf_oxide.annotation_decision_event.v1'
  event_id: string
  idempotency_key: string
  item_id: string
  item_sha256: string
  call_sha256: string
  decision: AnnotationDecision
  corrected_type?: string
  corrected_bounds?: [number, number, number, number]
  bbox_space?: 'pdf_points_bottom_left_xywh'
  revision_of?: string
  ts: string
  request_sha256: string
}

function parseAnnotationDecisionEvents(path: string): AnnotationDecisionEvent[] {
  return readJsonLines(path).map((row, index) => {
    if (
      row.schema !== 'pdf_oxide.annotation_decision_event.v1'
      || typeof row.event_id !== 'string'
      || typeof row.idempotency_key !== 'string'
      || typeof row.item_id !== 'string'
      || typeof row.item_sha256 !== 'string'
      || typeof row.call_sha256 !== 'string'
      || typeof row.decision !== 'string'
      || typeof row.ts !== 'string'
      || typeof row.request_sha256 !== 'string'
    ) {
      throw new ContractError(422, 'invalid_annotation_decision_store', `invalid event row ${index + 1}`)
    }
    return row as AnnotationDecisionEvent
  })
}

export function activeAnnotationDecisions(
  events: readonly AnnotationDecisionEvent[],
): Map<string, AnnotationDecisionEvent> {
  const active = new Map<string, AnnotationDecisionEvent>()
  const ids = new Set<string>()
  for (const event of events) {
    if (ids.has(event.event_id)) throw new ContractError(422, 'duplicate_annotation_event_id')
    ids.add(event.event_id)
    const current = active.get(event.item_id)
    if (current && event.revision_of !== current.event_id) {
      throw new ContractError(422, 'invalid_annotation_revision_chain')
    }
    if (!current && event.revision_of) throw new ContractError(422, 'orphan_annotation_revision')
    active.set(event.item_id, event)
  }
  return active
}

function normalizeBounds(value: unknown): [number, number, number, number] {
  if (!Array.isArray(value) || value.length !== 4) {
    throw new ContractError(400, 'invalid_corrected_bounds')
  }
  const values = value.map(Number)
  if (!values.every(Number.isFinite)) throw new ContractError(400, 'invalid_corrected_bounds')
  const [x, y, width, height] = values
  if (x < 0 || y < 0 || width <= 0 || height <= 0) {
    throw new ContractError(400, 'invalid_corrected_bounds')
  }
  return [x, y, width, height]
}

export function getAnnotationDecisions(path: string): JsonRecord {
  const events = parseAnnotationDecisionEvents(path)
  return {
    schema: 'pdf_oxide.annotation_decisions_response.v1',
    events,
    active: [...activeAnnotationDecisions(events).values()],
  }
}

export function appendAnnotationDecision(
  raw: unknown,
  decisionsPath: string,
  artifactsRoot: string,
  manifestPath: string,
): { event: AnnotationDecisionEvent; duplicate: boolean; response: JsonRecord } {
  const input = asRecord(raw, 'annotation_decision')
  const allowed = new Set([
    'bbox_space',
    'call_sha256',
    'corrected_bounds',
    'corrected_type',
    'decision',
    'idempotency_key',
    'item_id',
    'item_sha256',
    'revision_of',
    'ts',
  ])
  if (Object.keys(input).some((key) => !allowed.has(key))) {
    throw new ContractError(400, 'unexpected_annotation_decision_field')
  }
  const normalized: JsonRecord = {
    idempotency_key: idempotencyKey(input.idempotency_key),
    item_id: requiredString(input.item_id, 'item_id'),
    item_sha256: lowerSha(input.item_sha256, 'item_sha256'),
    call_sha256: lowerSha(input.call_sha256, 'call_sha256'),
    decision: input.decision,
    ts: isoTimestamp(input.ts),
  }
  if (input.revision_of !== undefined) normalized.revision_of = lowerSha(input.revision_of, 'revision_of')
  if (input.corrected_type !== undefined) normalized.corrected_type = requiredString(input.corrected_type, 'corrected_type')
  if (input.corrected_bounds !== undefined) normalized.corrected_bounds = normalizeBounds(input.corrected_bounds)
  if (input.bbox_space !== undefined) normalized.bbox_space = input.bbox_space
  const requestSha = sha256(canonicalJson(normalized))

  const events = parseAnnotationDecisionEvents(decisionsPath)
  const priorKey = events.find((event) => event.idempotency_key === normalized.idempotency_key)
  if (priorKey) {
    if (priorKey.request_sha256 !== requestSha) throw new ContractError(409, 'idempotency_key_reused')
    return { event: priorKey, duplicate: true, response: getAnnotationDecisions(decisionsPath) }
  }

  const { manifest } = verifyAnnotationQueueManifest(artifactsRoot, manifestPath)
  const item = manifest.items.find((candidate) => candidate.item_id === normalized.item_id)
  if (
    !item
    || item.call_sha256 !== normalized.call_sha256
    || item.item_sha256 !== normalized.item_sha256
  ) {
    throw new ContractError(409, 'unknown_or_stale_annotation_item')
  }
  if (!ANNOTATION_DECISIONS.includes(normalized.decision as AnnotationDecision)) {
    throw new ContractError(400, 'invalid_annotation_decision')
  }
  if (
    normalized.decision === 'correct_type'
    && !ELEMENT_TYPES.includes(normalized.corrected_type as typeof ELEMENT_TYPES[number])
  ) {
    throw new ContractError(400, 'invalid_corrected_type')
  }
  if (normalized.decision !== 'correct_type' && normalized.corrected_type !== undefined) {
    throw new ContractError(400, 'corrected_type_not_allowed')
  }
  if (normalized.decision === 'correct_bounds') {
    if (!normalized.corrected_bounds || normalized.bbox_space !== 'pdf_points_bottom_left_xywh') {
      throw new ContractError(400, 'corrected_bounds_and_space_required')
    }
  } else if (normalized.corrected_bounds !== undefined || normalized.bbox_space !== undefined) {
    throw new ContractError(400, 'corrected_bounds_not_allowed')
  }
  const active = activeAnnotationDecisions(events)
  const current = active.get(String(normalized.item_id))
  if (current && normalized.revision_of !== current.event_id) {
    throw new ContractError(409, 'annotation_amendment_requires_revision_of')
  }
  if (!current && normalized.revision_of) {
    throw new ContractError(409, 'orphan_annotation_revision')
  }

  const eventId = sha256(canonicalJson({
    schema: 'pdf_oxide.annotation_decision_event.v1',
    ...normalized,
    request_sha256: requestSha,
  }))
  const event = {
    schema: 'pdf_oxide.annotation_decision_event.v1',
    event_id: eventId,
    ...normalized,
    request_sha256: requestSha,
  } as AnnotationDecisionEvent
  appendJsonLine(decisionsPath, event)
  return { event, duplicate: false, response: getAnnotationDecisions(decisionsPath) }
}

export function appendTimingEvent(raw: unknown, path: string): JsonRecord {
  const input = asRecord(raw, 'ux_timing_event')
  const event: JsonRecord = {
    schema: 'pdf_oxide.ux_timing_event.v1',
    event_id: lowerSha(input.event_id, 'event_id'),
    workload_id: requiredString(input.workload_id, 'workload_id'),
    fixture_sha256: lowerSha(input.fixture_sha256, 'fixture_sha256'),
    ui_commit: requiredString(input.ui_commit, 'ui_commit'),
    item_id: requiredString(input.item_id, 'item_id'),
    action: requiredString(input.action, 'action'),
    started_at: isoTimestamp(input.started_at, 'started_at'),
    completed_at: isoTimestamp(input.completed_at, 'completed_at'),
    duration_ms: Number(input.duration_ms),
  }
  if (!Number.isFinite(event.duration_ms) || Number(event.duration_ms) < 0) {
    throw new ContractError(400, 'invalid_duration_ms')
  }
  const events = readJsonLines(path)
  const duplicate = events.find((row) => row.event_id === event.event_id)
  if (duplicate) {
    if (canonicalJson(duplicate) !== canonicalJson(event)) {
      throw new ContractError(409, 'timing_event_id_reused')
    }
    return duplicate
  }
  appendJsonLine(path, event)
  return event
}

export function manifestFilename(path: string): string {
  return basename(path)
}
