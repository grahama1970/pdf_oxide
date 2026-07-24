import { dirname } from 'node:path'

export interface ArtifactMountCandidate {
  path: string
  documentIds: readonly string[]
  pdfSha256s: readonly string[]
}

export interface ArtifactMountMatch {
  documentIds?: readonly string[]
  pdfSha256?: string
}

export function nearestArtifactMount<T extends ArtifactMountCandidate>(
  artifactPath: string,
  candidates: readonly T[],
  match: ArtifactMountMatch = {},
): T | undefined {
  const sameDirectory = candidates.find((candidate) => (
    dirname(candidate.path) === dirname(artifactPath)
  ))
  if (sameDirectory) return sameDirectory

  if (match.pdfSha256) {
    const samePdf = candidates.find((candidate) => (
      candidate.pdfSha256s.includes(match.pdfSha256 as string)
    ))
    if (samePdf) return samePdf
  }

  const documentIds = match.documentIds ?? []
  return candidates.find((candidate) => (
    documentIds.some((documentId) => candidate.documentIds.includes(documentId))
  ))
}
