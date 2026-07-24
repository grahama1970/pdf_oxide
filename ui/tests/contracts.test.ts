import assert from 'node:assert/strict'
import test from 'node:test'

import {
  OLD_FLOW_ONLY_FIELDS,
  normalizeAnnotationCall,
  normalizeCalibrationItem,
} from '../src/adapters/annotationCall'
import type { CalibrationPageImageIndex } from '../src/adapters/pageImageRefs'
import { normalizeSectionPath } from '../src/adapters/sectionTree'

const sha = 'a'.repeat(64)
const naming = 'sha256(canonical JSON of schema,pdf_sha256,page_index,dpi,format + NUL + PNG bytes)'
const imageIndex: CalibrationPageImageIndex = {
  schema: 'pdf_oxide.calibration_page_images.v1',
  documents: {
    fixture: {
      schema: 'pdf_oxide.page_image.v1',
      pdf_sha256: 'd'.repeat(64),
      directory: 'page_images',
      dpi: 150,
      format: 'png',
      naming,
      images: [{ page: 2, filename: `${'b'.repeat(64)}.png`, byte_sha256: 'c'.repeat(64) }],
    },
  },
}

test('new annotation-call fields map without old-flow-only dependencies', () => {
  const regions = normalizeAnnotationCall({
    schema: 'pdf_oxide.annotation_call.v1',
    items: [{
      page: 2,
      kind: 'block',
      bbox: [0.1, 0.2, 0.4, 0.6],
      reason: 'low_confidence',
      confidence: 0.125,
      text_excerpt: 'fixture excerpt',
    }],
  })
  assert.deepEqual(regions, [{
    id: 'annotation-call-0',
    page: 2,
    family: 'block',
    bbox: [0.1, 0.2, 0.4, 0.6],
    text_hint: 'fixture excerpt',
    notes: 'low_confidence',
    origin: 'agent_dispatcher',
  }])
  assert.deepEqual(OLD_FLOW_ONLY_FIELDS, [
    'crop_uri',
    'page_image_uri',
    'json_pointer',
    'page_image_hash',
    'task_id',
  ])
})

test('legacy-only payload cannot satisfy the new annotation contract', () => {
  assert.throws(
    () => normalizeAnnotationCall({
      schema: 'pdf_oxide.annotation_call.v1',
      items: [{
        crop_uri: '/old/crop.png',
        page_image_uri: '/old/page.png',
        json_pointer: '/elements/1',
        page_image_hash: sha,
        task_id: 'legacy-task',
      }],
    }),
    /page is invalid/,
  )
})

test('calibration mapping preserves the exact bbox and requires a content-addressed page image', () => {
  const normalized = normalizeCalibrationItem({
    doc: 'fixture',
    quintile: 0,
    page: 2,
    bbox: [0.125, 0.25, 0.625, 0.75],
    type: 'table',
    confidence: 0.123456,
    text: 'A table candidate',
    label: null,
  }, sha, imageIndex)
  assert.deepEqual(normalized.region.bbox, [0.125, 0.25, 0.625, 0.75])
  assert.equal(normalized.region.family, 'table')
  assert.equal(normalized.region.page, 2)
  assert.equal(normalized.image.filename, `${'b'.repeat(64)}.png`)
  assert.equal('confidence' in normalized.region, false)

  assert.throws(
    () => normalizeCalibrationItem({
      doc: 'fixture',
      quintile: 0,
      page: 2,
      type: 'table',
      confidence: 0.123456,
      text: 'No bbox',
      label: null,
    }, sha, imageIndex),
    /bbox must contain four finite numbers/,
  )
})

test('section tree v2 parent, children, order, and provenance map to existing breadcrumbs', () => {
  const tree = {
    schema: 'pdf_oxide.section_tree.v2',
    sections: [
      {
        id: 'root',
        title: 'Document',
        parent_id: null,
        children: ['child'],
        doc_order: 0,
        provenance: { pdf_sha256: sha, page: 0, bbox: null },
      },
      {
        id: 'child',
        title: 'Section 1',
        parent_id: 'root',
        children: [],
        doc_order: 1,
        provenance: { pdf_sha256: sha, page: 2, bbox: [0.1, 0.1, 0.9, 0.2] },
      },
    ],
  } as const
  const normalized = normalizeSectionPath(tree, 'child')
  assert.deepEqual(normalized.breadcrumb, ['Document', 'Section 1'])
  assert.equal(normalized.breadcrumbNodes[1].parent_node_id, 'root')
  assert.deepEqual(normalized.provenance[1].bbox, [0.1, 0.1, 0.9, 0.2])
})
