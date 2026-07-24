import { describe, expect, it } from 'vitest'
import { nearestArtifactMount, type ArtifactMountCandidate } from './artifactMountPairing'

function candidate(
  path: string,
  documentIds: string[],
  pdfSha256s: string[],
): ArtifactMountCandidate & { url: string } {
  return { path, documentIds, pdfSha256s, url: `/artifacts/${path}` }
}

describe('artifact mount pairing', () => {
  it('pairs retrieval artifacts with co-located page images and section trees before other matches', () => {
    const retrievalPath = '/mount/round2-live/retrieval_result.json'
    const wrongSameDocument = candidate(
      '/mount/calibration/page_images_v1.json',
      ['1512.03385v1'],
      ['pdf-sha'],
    )
    const siblingPageImages = candidate(
      '/mount/round2-live/page_images_v1.json',
      ['1512.03385v1'],
      ['pdf-sha'],
    )
    const siblingTree = candidate(
      '/mount/round2-live/section_tree.json',
      [],
      ['pdf-sha'],
    )

    expect(nearestArtifactMount(
      retrievalPath,
      [wrongSameDocument, siblingPageImages],
      { documentIds: ['1512.03385v1'], pdfSha256: 'pdf-sha' },
    )).toBe(siblingPageImages)
    expect(nearestArtifactMount(
      retrievalPath,
      [siblingTree],
      { documentIds: ['1512.03385v1'], pdfSha256: 'pdf-sha' },
    )).toBe(siblingTree)
  })

  it('falls back to PDF identity and then explicit document identity', () => {
    const pdfMatch = candidate('/mount/a/page_images_v1.json', [], ['pdf-sha'])
    const documentMatch = candidate('/mount/b/page_images_v1.json', ['doc-1'], [])

    expect(nearestArtifactMount(
      '/mount/results/retrieval_result.json',
      [documentMatch, pdfMatch],
      { documentIds: ['doc-1'], pdfSha256: 'pdf-sha' },
    )).toBe(pdfMatch)
    expect(nearestArtifactMount(
      '/mount/results/retrieval_result.json',
      [documentMatch],
      { documentIds: ['doc-1'] },
    )).toBe(documentMatch)
  })
})
