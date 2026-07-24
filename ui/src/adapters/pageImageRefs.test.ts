import { describe, expect, it } from 'vitest'
import {
  assertOriginalPageImages,
  bboxStyle,
  normalizeBboxXyxy,
  normalizePdfBboxWithGeometry,
  normalizePdfBboxXywh,
  normalizePageImageRef,
  parsePageImageIndex,
  lookupPageImageRefs,
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

  it('never falls back to another document with the same page number', () => {
    const index = parsePageImageIndex({
      pages: [{
        doc: 'arxiv',
        page: 0,
        page_image_refs: [{ sha256: SHA, href: `/page_images/${SHA}.png` }],
      }],
    })
    expect(lookupPageImageRefs(index, 'nist', 0)).toEqual([])
    expect(lookupPageImageRefs(index, undefined, 0)).toHaveLength(1)
  })

  it('projects normalized xywh rectangles as percentages', () => {
    expect(bboxStyle([0.1, 0.2, 0.3, 0.4])).toEqual({
      left: '10%',
      top: '20%',
      width: '30%',
      height: '40%',
    })
  })

  it('projects bottom-left PDF-space xywh boxes into top-left browser space', () => {
    expect(normalizePdfBboxXywh(
      [153, 594, 306, 54],
      { width: 612, height: 792 },
    )).toEqual([
      0.25,
      (792 - 594 - 54) / 792,
      0.5,
      54 / 792,
    ])
  })

  it('projects cropped and rotated overlay fixtures within two rendered pixels', () => {
    const bbox = [30, 40, 50, 20]
    const cropBox = [10, 20, 200, 100] as const
    const fixtures = [
      { rotation: 0 as const, pixelWidth: 400, pixelHeight: 200, expected: [40, 120, 100, 40] },
      { rotation: 90 as const, pixelWidth: 200, pixelHeight: 400, expected: [40, 40, 40, 100] },
      { rotation: 180 as const, pixelWidth: 400, pixelHeight: 200, expected: [260, 40, 100, 40] },
      { rotation: 270 as const, pixelWidth: 200, pixelHeight: 400, expected: [120, 260, 40, 100] },
    ]
    for (const fixture of fixtures) {
      const normalized = normalizePdfBboxWithGeometry(bbox, { cropBox, ...fixture })
      const pixels = [
        normalized[0] * fixture.pixelWidth,
        normalized[1] * fixture.pixelHeight,
        normalized[2] * fixture.pixelWidth,
        normalized[3] * fixture.pixelHeight,
      ]
      pixels.forEach((value, index) => {
        expect(Math.abs(value - fixture.expected[index])).toBeLessThanOrEqual(2)
      })
    }
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

  it('resolves relative hrefs from the page-image index location', () => {
    const index = parsePageImageIndex({
      pages: [{
        doc: 'nist',
        page: 7,
        page_image_refs: [{
          sha256: SHA,
          href: `page_images/${SHA}.png`,
        }],
      }],
    }, { indexUrl: '/artifacts/pdf-lab/annotation-calls/nist/page_images_v1.json' })
    expect(index.byDocAndPage.get('nist::7')?.[0].href).toBe(
      `/artifacts/pdf-lab/annotation-calls/nist/page_images/${SHA}.png`,
    )
  })
})
