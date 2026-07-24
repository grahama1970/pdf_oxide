import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { isCalibrationLabelRow, type CalibrationSampleItem } from '../../adapters/calibration'
import { normalizeAnnotationCall } from '../../adapters/annotationCall'
import type { PageImageRef } from '../../adapters/pageImageRefs'
import { AnnotationQueueRoute } from '../annotation/AnnotationQueueRoute'
import { CalibrateRoute } from '../calibration/CalibrateRoute'
import { assertRetrievalEvidence, RetrievalEvidenceView } from '../retrieval/RetrievalEvidenceView'
import { NormalizedPageOverlay } from './NormalizedPageOverlay'

const PDF_SHA = 'd'.repeat(64)
const IMAGE_SHA = 'e'.repeat(64)
const PAGE_IMAGE: PageImageRef = {
  sha256: IMAGE_SHA,
  filename: `${IMAGE_SHA}.png`,
  href: `/page_images/${IMAGE_SHA}.png`,
  mimeType: 'image/png',
  page: 3,
  width: 1000,
  height: 1400,
  doc: 'sample-doc',
  pdfSha256: PDF_SHA,
}

function retrievalResult(withImage: boolean) {
  return {
    answer: 'The requested figure shows the residual architecture.',
    pdf_sha256: PDF_SHA,
    section_path: ['Results', 'Residual learning'],
    evidence: [{
      element_id: 'figure-7',
      type: 'figure',
      page: 3,
      bbox: [0.1, 0.2, 0.3, 0.4] as [number, number, number, number],
      section_path: ['Results', 'Residual learning'],
      page_image_refs: withImage ? [{ sha256: IMAGE_SHA, href: `/page_images/${IMAGE_SHA}.png`, page: 3 }] : undefined,
    }],
  }
}

describe('verification-first UX', () => {
  it('renders the original page, section breadcrumb, and provenance chain', () => {
    render(<RetrievalEvidenceView result={retrievalResult(true)} />)
    expect(screen.getByTestId('page-image')).toBeInTheDocument()
    expect(screen.getAllByTestId('section-breadcrumb').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByTestId('provenance-chain')).toHaveTextContent(PDF_SHA)
    expect(screen.getByTestId('provenance-chain')).toHaveTextContent('figure-7')
  })

  it('fails the retrieval contract when an original page image is missing', () => {
    expect(() => assertRetrievalEvidence(retrievalResult(false))).toThrow(/missing original page image/)
    render(<RetrievalEvidenceView result={retrievalResult(false)} />)
    expect(screen.getByTestId('retrieval-contract-failure')).toHaveTextContent('Answer withheld')
  })

  it('projects normalized bbox coordinates onto the page image', () => {
    render(<NormalizedPageOverlay pageImage={PAGE_IMAGE} bbox={[0.1, 0.2, 0.3, 0.4]} label="figure" />)
    expect(screen.getByTestId('bbox-overlay')).toHaveStyle({
      left: '10%',
      top: '20%',
      width: '30%',
      height: '40%',
    })
  })

  it('keeps confidence opaque and writes an exact labels_v1 row', async () => {
    const sample: CalibrationSampleItem = {
      doc: 'sample-doc',
      quintile: 'q2',
      page: 3,
      bbox: [0.1, 0.2, 0.3, 0.4],
      type: 'figure',
      confidence: 0.123456789,
      text: 'Residual architecture diagram',
      label: null,
      pageImageRefs: [{ sha256: IMAGE_SHA, href: `/page_images/${IMAGE_SHA}.png`, page: 3 }],
    }
    const persistLabel = vi.fn(async () => undefined)
    render(<CalibrateRoute initialRows={[sample]} persistLabel={persistLabel} />)

    const route = screen.getByTestId('calibrate-route')
    expect(route).toHaveAttribute('data-confidence-hidden', 'true')
    expect(route).not.toHaveTextContent('0.123456789')
    await waitFor(() => expect(route).toHaveAttribute('data-item-sha-ready', 'true'))

    fireEvent.click(screen.getByRole('button', { name: /Correct/ }))
    await waitFor(() => expect(persistLabel).toHaveBeenCalledTimes(1))
    const row = persistLabel.mock.calls[0][0]
    expect(isCalibrationLabelRow(row)).toBe(true)
    expect(Object.keys(row).sort()).toEqual(['item_sha', 'label', 'ts'])
    expect(row.label).toBe('correct')
  })

  it('virtualizes a 2161-item annotation queue', () => {
    const call = normalizeAnnotationCall({
      schema: 'pdf_oxide.annotation_call.v1',
      pdf_sha256: PDF_SHA,
      engine_commit: 'head',
      accuracy_estimate: { basis: 'opaque', value: 0.5 },
      doc: 'large-doc',
      items: Array.from({ length: 2161 }, (_, page) => ({
        page,
        kind: 'block',
        reason: page % 2 === 0 ? 'low_confidence' : 'reviewer_flagged',
        confidence: 0.5,
        current_type: 'Body',
        text_excerpt: `Row ${page}`,
      })),
    })
    render(<AnnotationQueueRoute initialCalls={[call]} />)
    const renderedRows = screen.getAllByTestId('annotation-row')
    expect(renderedRows.length).toBeLessThan(40)
    expect(screen.getByTestId('annotation-queue-route')).toHaveAttribute('data-confidence-hidden', 'true')
    expect(screen.getByTestId('annotation-queue-route')).not.toHaveTextContent('0.5')
  })
})
