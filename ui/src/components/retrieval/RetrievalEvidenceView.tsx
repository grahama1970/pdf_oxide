import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, FileSearch2, Loader2 } from 'lucide-react'
import {
  assertOriginalPageImages,
  normalizeBboxXyxy,
  parsePageImageIndex,
  resolvePageImageRefs,
  type BboxXywh,
  type PageImageIndex,
  type PageImageRef,
} from '../../adapters/pageImageRefs'
import { normalizeSectionTree, sectionForElement, type SectionTree } from '../../adapters/sectionTree'
import { useRegisterAction } from '../../hooks/useRegisterAction'
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
  schema?: string
  section_id?: string
  pdf_sha256?: string
  section_path?: string[] | string
  vector_provenance?: unknown
  evidence_groups?: Array<{
    page: number
    page_image: {
      href: string
      sha256: string
      byte_sha256?: string
      verified: boolean
      width?: number
      height?: number
    }
    evidence: Array<Omit<RetrievalEvidenceItemInput, 'page'> & {
      page?: number
      overlay_number: number
    }>
  }>
  evidence?: RetrievalEvidenceItemInput[]
  citations?: RetrievalEvidenceItemInput[]
  elements?: RetrievalEvidenceItemInput[]
  [key: string]: unknown
}

export interface RetrievalEvidenceItem {
  overlayNumber: number
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
  evidenceGroups: readonly {
    page: number
    pageImage: PageImageRef
    evidence: readonly RetrievalEvidenceItem[]
  }[]
}

export interface RetrievalEvidenceViewProps {
  result: RetrievalAnswerInput
  pageImageIndex?: PageImageIndex | null
  sectionTree?: SectionTree | null
  artifactsRoot?: string
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
  if (Array.isArray(result.answers) || Array.isArray(result.alternatives) || Array.isArray(result.ranked_answers)) {
    throw new Error('retrieval contract permits exactly one answer')
  }
  if (Array.isArray(result.evidence_groups)) {
    if (result.schema !== 'pdf_oxide.retrieval_answer.v1') throw new Error('unsupported retrieval answer schema')
    const resultPdfSha = normalizePdfSha(result.pdf_sha256)
    const topLevelPath = normalizePath(result.section_path)
    const sectionId = asString(result.section_id)
    if (!sectionId || topLevelPath.length === 0) throw new Error('retrieval answer requires exact section binding')
    if (result.evidence_groups.length === 0) throw new Error('retrieval answer has no evidence groups')
    const pages = new Set<number>()
    const evidenceGroups = result.evidence_groups.map((group, groupIndex) => {
      const page = Number(group.page)
      if (!Number.isInteger(page) || page < 0 || pages.has(page)) {
        throw new Error(`evidence_groups[${groupIndex}] page must be unique and non-negative`)
      }
      pages.add(page)
      const image = group.page_image
      if (!image || image.verified !== true) throw new Error(`evidence_groups[${groupIndex}] lacks a verified page image`)
      const imageSha = normalizePdfSha(image.sha256)
      const href = asString(image.href)
      if (!href) throw new Error(`evidence_groups[${groupIndex}] page image href is required`)
      const pageImage: PageImageRef = {
        sha256: imageSha,
        filename: href.split('/').pop() ?? `${imageSha}.png`,
        href,
        mimeType: 'image/png',
        page,
        width: image.width,
        height: image.height,
        pdfSha256: resultPdfSha,
      }
      const overlayNumbers = new Set<number>()
      const evidence = group.evidence.map((row, index): RetrievalEvidenceItem => {
        const overlayNumber = Number(row.overlay_number)
        if (!Number.isInteger(overlayNumber) || overlayNumber < 1 || overlayNumbers.has(overlayNumber)) {
          throw new Error(`evidence_groups[${groupIndex}].evidence[${index}] overlay number is invalid`)
        }
        overlayNumbers.add(overlayNumber)
        const elementId = asString(row.element_id ?? row.id)
        if (!elementId) throw new Error(`evidence_groups[${groupIndex}].evidence[${index}] is missing element id`)
        const rowSectionId = asString(row.section_id)
        const rowSectionPath = normalizePath(row.section_path)
        if (!rowSectionId || rowSectionPath.length === 0) throw new Error(`evidence ${elementId} lacks exact section binding`)
        return {
          overlayNumber,
          elementId,
          type: asString(row.type) ?? 'element',
          page,
          bbox: normalizeBboxXyxy(row.bbox, pageImage),
          pdfSha256: resultPdfSha,
          sectionId: rowSectionId,
          sectionPath: rowSectionPath,
          text: asString(row.text ?? row.excerpt),
          pageImages: [pageImage],
        }
      })
      if (evidence.length === 0) throw new Error(`evidence_groups[${groupIndex}] contains no evidence`)
      return { page, pageImage, evidence }
    })
    return {
      answer,
      pdfSha256: resultPdfSha,
      sectionPath: topLevelPath,
      evidence: evidenceGroups.flatMap((group) => group.evidence),
      evidenceGroups,
    }
  }
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
    const bbox = normalizeBboxXyxy(row.bbox, pageImage)
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
      overlayNumber: index + 1,
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

  const grouped = new Map<string, RetrievalEvidenceItem[]>()
  for (const item of evidence) {
    const key = `${item.page}:${item.pageImages[0].sha256}`
    const items = grouped.get(key) ?? []
    items.push(item)
    grouped.set(key, items)
  }
  return {
    answer,
    pdfSha256: resultPdfSha,
    sectionPath: topLevelPath.length > 0 ? topLevelPath : evidence[0].sectionPath,
    evidence,
    evidenceGroups: [...grouped.values()]
      .map((items) => ({
        page: items[0].page,
        pageImage: items[0].pageImages[0],
        evidence: items,
      })),
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

function qidQualifier(value: string): string {
  return value.trim().replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'unknown'
}

function RetrievalEvidenceCard({ item }: { item: RetrievalEvidenceItem }) {
  const qualifier = qidQualifier(item.elementId)
  const excerptQid = `retrieval-evidence:excerpt:${qualifier}`
  useRegisterAction(excerptQid, {
    app: 'pdf-lab',
    action: 'RETRIEVAL_EVIDENCE_TOGGLE_EXCERPT',
    label: `Toggle extracted text for ${item.elementId}`,
    description: 'Expand or collapse the extracted source text attached to this provenance chain',
  })

  return (
    <article className="pdf-verify-evidence-card">
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

      <ol className="pdf-verify-provenance" data-testid="provenance-chain" aria-label={`Provenance chain for ${item.elementId}`}>
        <li><span>PDF</span><code>{item.pdfSha256}</code></li>
        <li><span>Page</span><code>{item.page}</code></li>
        <li><span>Bounds</span><code>{formatBbox(item.bbox)}</code></li>
        <li><span>Element</span><code>{item.elementId}</code></li>
        {item.sectionId && <li><span>Section</span><code>{item.sectionId}</code></li>}
      </ol>

      {item.text && (
        <details className="pdf-verify-excerpt">
          <summary
            data-qid={excerptQid}
            data-qs-action="RETRIEVAL_EVIDENCE_TOGGLE_EXCERPT"
            title={`Show or hide extracted text for ${item.elementId}`}
          >
            Extracted text
          </summary>
          <p>{item.text}</p>
        </details>
      )}
    </article>
  )
}

function RetrievalEvidenceGroup({
  group,
}: {
  group: NormalizedRetrievalAnswer['evidenceGroups'][number]
}) {
  return (
    <section className="pdf-verify-evidence-group" data-testid="evidence-group">
      <NormalizedPageOverlay
        pageImage={group.pageImage}
        overlays={group.evidence.flatMap((item) => item.bbox
          ? [{ bbox: item.bbox, label: String(item.overlayNumber) }]
          : [])}
        alt={`Original PDF page ${group.page} supporting ${group.evidence.length} evidence items`}
        actionQualifier={`evidence-page-${group.page}`}
        compact
      />
      <div className="pdf-verify-evidence-grid">
        {group.evidence.map((item) => (
          <RetrievalEvidenceCard key={item.elementId} item={item} />
        ))}
      </div>
    </section>
  )
}

export function RetrievalEvidenceView({
  result,
  pageImageIndex,
  sectionTree,
  artifactsRoot = '(the configured PDF Lab artifact root)',
}: RetrievalEvidenceViewProps) {
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
        <p>The retrieval result is missing valid original-page evidence, a section path, or a complete provenance chain.</p>
        <strong data-testid="page-image-error">
          Original page images, section path, and provenance are mandatory.
        </strong>
        <p>The server looked under <code>{artifactsRoot}</code>.</p>
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

      <section aria-label="Source evidence">
        {answer.evidenceGroups.map((group) => (
          <RetrievalEvidenceGroup key={`${group.page}:${group.pageImage.sha256}`} group={group} />
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
  artifactsRoot?: string
}

export function RetrievalEvidenceRoute({
  resultUrl,
  pageImageIndexUrl,
  sectionTreeUrl,
  fetchImpl = fetch,
  artifactsRoot = '(the configured PDF Lab artifact root)',
}: RetrievalEvidenceRouteProps) {
  const [result, setResult] = useState<RetrievalAnswerInput | null>(null)
  const [pageImageIndex, setPageImageIndex] = useState<PageImageIndex | null>(null)
  const [sectionTree, setSectionTree] = useState<SectionTree | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setResult(null)
    setError(null)
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
      setPageImageIndex(rawImages ? parsePageImageIndex(rawImages, { indexUrl: pageImageIndexUrl }) : null)
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
        <h1>Retrieval evidence needs attention</h1>
        <p>PDF Lab found a retrieval result but could not read it safely. Check the retrieval_result.json and its page_images_v1.json.</p>
        <p>The server looked under <code>{artifactsRoot}</code>.</p>
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
  return (
    <RetrievalEvidenceView
      result={result}
      pageImageIndex={pageImageIndex}
      sectionTree={sectionTree}
      artifactsRoot={artifactsRoot}
    />
  )
}
