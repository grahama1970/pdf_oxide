import { describe, expect, it } from 'vitest'
import { normalizeAnnotationCall } from './annotationCall'

const PDF_SHA = 'b'.repeat(64)

describe('annotation_call.v1 adapter', () => {
  it('normalizes a live annotation call without discarding blinded confidence', () => {
    const call = normalizeAnnotationCall({
      schema: 'pdf_oxide.annotation_call.v1',
      pdf_sha256: PDF_SHA,
      engine_name: 'pdf-oxide',
      engine_version: '0.3.14',
      engine_commit: '66f45558',
      accuracy_estimate: { basis: 'miscalibrated', value: 0.846 },
      doc: 'r5',
      items: [{
        page: 9,
        kind: 'block',
        bbox: [0.1, 0.2, 0.3, 0.1],
        reason: 'low_confidence',
        confidence: 0.314159,
        current_type: 'Body',
        text_excerpt: 'Example text',
        oracle_excerpt: 'Example oracle text',
        missing_text: '\u0014',
      }],
    })
    expect(call.documentId).toBe('r5')
    expect(call.engineName).toBe('pdf-oxide')
    expect(call.engineVersion).toBe('0.3.14')
    expect(call.items[0].normalizedBbox).toEqual([0.1, 0.2, 0.3, 0.1])
    expect(call.items[0].confidence).toBe(0.314159)
    expect(call.items[0].oracleExcerpt).toBe('Example oracle text')
    expect(call.items[0].missingText).toBe('\u0014')
  })

  it('fails closed on unsupported reason values', () => {
    expect(() => normalizeAnnotationCall({
      schema: 'pdf_oxide.annotation_call.v1',
      pdf_sha256: PDF_SHA,
      engine_commit: 'head',
      accuracy_estimate: { basis: 'test', value: 1 },
      items: [{ page: 0, kind: 'block', reason: 'invented_reason' }],
    })).toThrow(/reason is invalid/)
  })
})
