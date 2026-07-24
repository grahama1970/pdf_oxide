import { describe, expect, it } from 'vitest'
import {
  assertOriginalPageImages,
  bboxStyle,
  normalizeBboxXyxy,
  normalizePageImageRef,
  parsePageImageIndex,
} from './pageImageRefs'

const SHA = 'a'.repeat(64)

describe('page image refs', () => {
  it('accepts content-addressed PNGs and indexes them by document/page', () => {
    const index = parsePageImageIndex({
      pages: [{
        doc: 'nist',
        page: 7,
        page_image_refs: [{ sha256: SHA, href: `/page_images/${SHA}.png`, width: 1000, height: 1400 }],
      }],
    })
    const refs = index.byDocAndPage.get('nist::7') ?? []
    assertOriginalPageImages(refs, 'nist page 7')
    expect(refs[0]).toMatchObject({ sha256: SHA, filename: `${SHA}.png`, page: 7 })
  })

  it('rejects non-content-addressed filenames', () => {
    expect(() => normalizePageImageRef('/page_images/page-7.png')).toThrow(/not content addressed/)
  })

  it('projects normalized xywh rectangles as percentages', () => {
    expect(bboxStyle([0.1, 0.2, 0.3, 0.4])).toEqual({
      left: '10%',
      top: '20%',
      width: '30%',
      height: '40%',
    })
  })

  it('adapts extraction xyxy boxes and calibration manifests without field drift', () => {
    const bbox = normalizeBboxXyxy([0.1, 0.2, 0.4, 0.6])
    expect(bbox?.slice(0, 2)).toEqual([0.1, 0.2])
    expect(bbox?.[2]).toBeCloseTo(0.3)
    expect(bbox?.[3]).toBeCloseTo(0.4)
    const index = parsePageImageIndex({
      schema: 'pdf_oxide.calibration_page_images.v1',
      documents: {
        nist: {
          pdf_sha256: 'b'.repeat(64),
          directory: 'page_images',
          images: [{ page: 7, filename: `${SHA}.png`, byte_sha256: 'c'.repeat(64) }],
        },
      },
    }, { baseUrl: '/artifacts/pdf-lab/calibration' })
    expect(index.byDocAndPage.get('nist::7')?.[0].href).toBe(
      `/artifacts/pdf-lab/calibration/page_images/${SHA}.png`,
    )
  })
})
