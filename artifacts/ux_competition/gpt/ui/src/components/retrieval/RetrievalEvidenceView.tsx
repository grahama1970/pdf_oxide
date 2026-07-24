import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, FileSearch2, Loader2 } from 'lucide-react'
import {
  assertOriginalPageImages,
  normalizeBboxXywh,
  parsePageImageIndex,
  resolvePageImageRefs,
  type BboxXywh,
  type PageImageIndex,
  type PageImageRef,
} from '../../adapters/pageImageRefs'
import { normalizeSectionTree, sectionForElement, type SectionTree } from '../../adapters/sectionTree'
import { NormalizedPageOverlay } from '../verification/NormalizedPageOverlay'
import '../verification/VerificationUx.css'

export interface RetrievalEvidenceItemInput {
  id?: string
  element_id?: string
  type?: string
  page: number
  bbox?: [number, number, number, number]
  pdf_sha256?: string
  section_id?: string
  section_path?: string[] | string
  text?: string
  excerpt?: string
  page_image_refs?: unknown
  provenance?: Record<string, unknown>
  [key: string]: unknown
}

export interface RetrievalAnswerInput {
  answer: string
  pdf_sha256?: string
  section_path?: string[] | string
  evidence?: RetrievalEvidenceItemInput[]
  citations?: RetrievalEvidenceItemInput[]
  elements?: RetrievalEvidenceItemInput[]
  [key: string]: unknown
}

export interface RetrievalEvidenceItem {
  elementId: string
  type: string
  page: number
  bbox: BboxXywh | undefined
  pdfSha256: string
  sectionId: string | null
  sectionPath: readonly string[]
  text: string | null
  pageImages: readonly PageImageRef[]
}

export interface NormalizedRetrievalAnswer {
  answer: string
  pdfSha256: string
  sectionPath: readonly string[]
  evidence: readonly RetrievalEvidenceItem[]
}

export interface RetrievalEvidenceViewProps {
  result: RetrievalAnswerInput
  pageImageIndex?: PageImageIndex | null
  sectionTree?: SectionTree | null
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null
}

function normalizePath(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).map((entry) => entry.trim()).filter(Boolean)
  if (typeof value === 'string') return value.split(/\s*(?:›|>|\/)\s*/).map((entry) => entry.trim()).filter(Boolean)
  return []
}

function normalizePdfSha(value: unknown): string {
  const normalized = asString(value)?.replace(/^sha256:/i, '').toLowerCase()
  if (!normalized || !/^[a-f0-9]{64}$/i.test(normalized)) throw new Error('retrieval answer is missing a valid pdf_sha256')
  return normalized
}

function evidenceRows(result: RetrievalAnswerInput): RetrievalEvidenceItemInput[] {
  const rows = result.evidence ?? result.citations ?? result.elements ?? []
  if (!Array.isArray(rows)) throw new Error('retrieval evidence must be an array')
  return rows
}

export function normalizeRetrievalEvidence(
  result: RetrievalAnswerInput,
  pageImageIndex?: PageImageIndex | null,
  sectionTree?: SectionTree | null,
): NormalizedRetrievalAnswer {
  const answer = asString(result.answer)
  if (!answer) throw new Error('retrieval answer text is required')
  const rows = evidenceRows(result)
  if (rows.length === 0) throw new Error('retrieval contract violation: answer has no evidence elements')
  const resultPdfSha = normalizePdfSha(result.pdf_sha256 ?? rows[0]?.pdf_sha256 ?? sectionTree?.pdfSha256)
  const topLevelPath = normalizePath(result.section_path)

  const evidence = rows.map((row, index): RetrievalEvidenceItem => {
    const page = Number(row.page)
    if (!Number.isInteger(page) || page < 0) throw new Error(`evidence[${index}].page must be a non-negative integer`)
    const elementId = asString(row.element_id ?? row.id)
    if (!elementId) throw new Error(`evidence[${index}] is missing element id`)
    const pdfSha256 = normalizePdfSha(row.pdf_sha256 ?? resultPdfSha)
    if (pdfSha256 !== resultPdfSha) throw new Error(`evidence[${index}] points at a different PDF`)

    const pageImages = resolvePageImageRefs(row, pageImageIndex, {
      doc: asString(row.doc) ?? undefined,
      page,
      pdfSha256,
    })
    assertOriginalPageImages(pageImages, `element ${elementId}`)
    const pageImage = pageImages[0]
    const bbox = normalizeBboxXywh(row.bbox, pageImage)
    const section = sectionTree ? sectionForElement(sectionTree, row) : null
    const sectionId = asString(row.section_id) ?? section?.id ?? null
    const sectionPath = normalizePath(row.section_path)
    const finalPath = sectionPath.length > 0
      ? sectionPath
      : section?.path.length
        ? [...section.path]
        : topLevelPath
    if (finalPath.length === 0) throw new Error(`evidence[${index}] is missing section_path`)

    return {
      elementId,
      type: asString(row.type) ?? 'element',
      page,
      bbox,
      pdfSha256,
      sectionId,
      sectionPath: finalPath,
      text: asString(row.text ?? row.excerpt),
      pageImages,
    }
  })

  return {
    answer,
    pdfSha256: resultPdfSha,
    sectionPath: topLevelPath.length > 0 ? topLevelPath : evidence[0].sectionPath,
    evidence,
  }
}

export function assertRetrievalEvidence(
  result: RetrievalAnswerInput,
  pageImageIndex?: PageImageIndex | null,
  sectionTree?: SectionTree | null,
): void {
  void normalizeRetrievalEvidence(result, pageImageIndex, sectionTree)
}

function formatBbox(bbox: BboxXywh | undefined): string {
  return bbox ? `[${bbox.map((value) => value.toFixed(4)).join(', ')}]` : '(page-level evidence)'
}

export function RetrievalEvidenceView({ result, pageImageIndex, sectionTree }: RetrievalEvidenceViewProps) {
  const normalized = useMemo(() => {
    try {
      return { value: normalizeRetrievalEvidence(result, pageImageIndex, sectionTree), error: null }
    } catch (error) {
      return { value: null, error: error instanceof Error ? error.message : String(error) }
    }
  }, [pageImageIndex, result, sectionTree])

  if (!normalized.value) {
    return (
      <main className="pdf-verify-route pdf-verify-route--center" data-confidence-hidden="true" data-testid="retrieval-contract-failure">
        <AlertTriangle aria-hidden="true" />
        <h1>Answer withheld</h1>
        <p>{normalized.error}</p>
        <strong>Original page images, section path, and provenance are mandatory.</strong>
      </main>
    )
  }

  const answer = normalized.value
  return (
    <main className="pdf-verify-route pdf-verify-retrieval" data-confidence-hidden="true">
      <header className="pdf-verify-header">
        <div>
          <span className="pdf-verify-kicker">Verification-first retrieval</span>
          <h1>Traceable answer</h1>
          <nav data-testid="section-breadcrumb" className="pdf-verify-breadcrumb" aria-label="Section path">
            {answer.sectionPath.map((part, index) => (
              <span key={`${part}-${index}`}>
                {index > 0 && <i aria-hidden="true">›</i>}
                {part}
              </span>
            ))}
          </nav>
        </div>
        <div className="pdf-verify-proof-chip"><FileSearch2 /> {answer.evidence.length} source element{answer.evidence.length === 1 ? '' : 's'}</div>
      </header>

      <article className="pdf-verify-answer">
        <span>Answer</span>
        <p>{answer.answer}</p>
      </article>

      <section className="pdf-verify-evidence-grid" aria-label="Source evidence">
        {answer.evidence.map((item) => (
          <article className="pdf-verify-evidence-card" key={item.elementId}>
            <header>
              <div>
                <span>{item.type}</span>
                <strong>{item.elementId}</strong>
              </div>
              <em>Page {item.page}</em>
            </header>

            <nav data-testid="section-breadcrumb" className="pdf-verify-breadcrumb is-compact" aria-label={`Section path for ${item.elementId}`}>
              {item.sectionPath.map((part, index) => (
                <span key={`${item.elementId}-${part}-${index}`}>
                  {index > 0 && <i aria-hidden="true">›</i>}
                  {part}
                </span>
              ))}
            </nav>

            {item.pageImages.map((pageImage) => (
              <NormalizedPageOverlay
                key={pageImage.sha256}
                pageImage={pageImage}
                bbox={item.bbox}
                label={item.type}
                alt={`Original PDF page ${item.page} supporting element ${item.elementId}`}
                compact
              />
            ))}

            <ol className="pdf-verify-provenance" data-testid="provenance-chain" aria-label={`Provenance chain for ${item.elementId}`}>
              <li><span>PDF</span><code>{item.pdfSha256}</code></li>
              <li><span>Page</span><code>{item.page}</code></li>
              <li><span>Bounds</span><code>{formatBbox(item.bbox)}</code></li>
              <li><span>Element</span><code>{item.elementId}</code></li>
              {item.sectionId && <li><span>Section</span><code>{item.sectionId}</code></li>}
            </ol>

            {item.text && (
              <details className="pdf-verify-excerpt">
                <summary>Extracted text</summary>
                <p>{item.text}</p>
              </details>
            )}
          </article>
        ))}
      </section>
    </main>
  )
}

export interface RetrievalEvidenceRouteProps {
  resultUrl: string
  pageImageIndexUrl?: string
  sectionTreeUrl?: string
  fetchImpl?: typeof fetch
}

export function RetrievalEvidenceRoute({
  resultUrl,
  pageImageIndexUrl,
  sectionTreeUrl,
  fetchImpl = fetch,
}: RetrievalEvidenceRouteProps) {
  const [result, setResult] = useState<RetrievalAnswerInput | null>(null)
  const [pageImageIndex, setPageImageIndex] = useState<PageImageIndex | null>(null)
  const [sectionTree, setSectionTree] = useState<SectionTree | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const loadJson = async (url: string): Promise<unknown> => {
      const response = await fetchImpl(url)
      if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`)
      return response.json()
    }
    void Promise.all([
      loadJson(resultUrl),
      pageImageIndexUrl ? loadJson(pageImageIndexUrl) : Promise.resolve(null),
      sectionTreeUrl ? loadJson(sectionTreeUrl) : Promise.resolve(null),
    ]).then(([rawResult, rawImages, rawTree]) => {
      if (cancelled) return
      setResult(rawResult as RetrievalAnswerInput)
      setPageImageIndex(rawImages ? parsePageImageIndex(rawImages) : null)
      setSectionTree(rawTree ? normalizeSectionTree(rawTree) : null)
    }).catch((loadError) => {
      if (!cancelled) setError(loadError instanceof Error ? loadError.message : String(loadError))
    })
    return () => { cancelled = true }
  }, [fetchImpl, pageImageIndexUrl, resultUrl, sectionTreeUrl])

  if (error) {
    return (
      <main className="pdf-verify-route pdf-verify-route--center" data-confidence-hidden="true">
        <AlertTriangle />
        <h1>Retrieval evidence failed to load</h1>
        <p>{error}</p>
      </main>
    )
  }
  if (!result) {
    return (
      <main className="pdf-verify-route pdf-verify-route--center" data-confidence-hidden="true">
        <Loader2 className="pdf-verify-spin" />
        <p>Loading answer, section tree, and original page images…</p>
      </main>
    )
  }
  return <RetrievalEvidenceView result={result} pageImageIndex={pageImageIndex} sectionTree={sectionTree} />
}
