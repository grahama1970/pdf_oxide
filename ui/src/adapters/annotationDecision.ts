import type { AnnotationQueueItem } from './annotationCall'

export const ANNOTATION_DECISIONS = ['accept', 'defer', 'correct_type', 'correct_bounds'] as const
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
export type ElementType = typeof ELEMENT_TYPES[number]

export interface AnnotationDecisionEvent {
  schema: 'pdf_oxide.annotation_decision_event.v1'
  event_id: string
  idempotency_key: string
  item_id: string
  item_sha256: string
  call_sha256: string
  decision: AnnotationDecision
  corrected_type?: ElementType
  corrected_bounds?: [number, number, number, number]
  bbox_space?: 'pdf_points_bottom_left_xywh'
  revision_of?: string
  ts: string
  request_sha256: string
}

export interface AnnotationDecisionInput {
  idempotency_key: string
  item_id: string
  item_sha256: string
  call_sha256: string
  decision: AnnotationDecision
  corrected_type?: ElementType
  corrected_bounds?: [number, number, number, number]
  bbox_space?: 'pdf_points_bottom_left_xywh'
  revision_of?: string
  ts: string
}

function idempotencyKey(): string {
  return `annotation:${Date.now().toString(36)}:${globalThis.crypto.randomUUID()}`
}

export function buildAnnotationDecisionInput(
  item: AnnotationQueueItem,
  decision: AnnotationDecision,
  options: {
    correctedType?: ElementType
    correctedBounds?: [number, number, number, number]
    revisionOf?: string
    timestamp?: string
  } = {},
): AnnotationDecisionInput {
  const correctedType = options.correctedType
  const correctedBounds = options.correctedBounds
  if (decision === 'correct_type' && !correctedType) throw new Error('corrected type is required')
  if (decision !== 'correct_type' && correctedType) throw new Error('corrected type is not allowed')
  if (decision === 'correct_bounds' && !correctedBounds) throw new Error('corrected bounds are required')
  if (decision !== 'correct_bounds' && correctedBounds) throw new Error('corrected bounds are not allowed')
  return {
    idempotency_key: idempotencyKey(),
    item_id: item.id,
    item_sha256: item.itemSha256,
    call_sha256: item.callSha256,
    decision,
    ...(correctedType ? { corrected_type: correctedType } : {}),
    ...(correctedBounds
      ? { corrected_bounds: correctedBounds, bbox_space: 'pdf_points_bottom_left_xywh' as const }
      : {}),
    ...(options.revisionOf ? { revision_of: options.revisionOf } : {}),
    ts: options.timestamp ?? new Date().toISOString(),
  }
}

export function isAnnotationDecisionEvent(value: unknown): value is AnnotationDecisionEvent {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const event = value as Partial<AnnotationDecisionEvent>
  return event.schema === 'pdf_oxide.annotation_decision_event.v1'
    && typeof event.event_id === 'string'
    && typeof event.item_id === 'string'
    && typeof event.item_sha256 === 'string'
    && typeof event.call_sha256 === 'string'
    && ANNOTATION_DECISIONS.includes(event.decision as AnnotationDecision)
}
