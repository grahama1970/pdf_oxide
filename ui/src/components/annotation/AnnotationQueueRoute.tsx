import { Fragment, useDeferredValue, useEffect, useMemo, useRef, useState, type ChangeEvent, type UIEvent } from 'react'
import {
  AlertTriangle,
  Ban,
  BoxSelect,
  Check,
  ChevronRight,
  Clock3,
  FileWarning,
  Filter,
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Tags,
  Type,
} from 'lucide-react'
import {
  ANNOTATION_CALL_SCHEMA,
  annotationReasonLabel,
  flattenAnnotationItems,
  normalizeAnnotationCall,
  normalizeAnnotationCallCollection,
  type AnnotationKind,
  type AnnotationQueueItem,
  type AnnotationReason,
  type NormalizedAnnotationCall,
} from '../../adapters/annotationCall'
import {
  lookupPageImageRefs,
  mergePageImageIndexes,
  normalizePdfBboxXywh,
  normalizePageImageRefs,
  parsePageImageIndex,
  type PageImageIndex,
  type PageImageRef,
} from '../../adapters/pageImageRefs'
import {
  ELEMENT_TYPES,
  buildAnnotationDecisionInput,
  isAnnotationDecisionEvent,
  type AnnotationDecision,
  type AnnotationDecisionEvent,
  type ElementType,
} from '../../adapters/annotationDecision'
import { useRegisterAction } from '../../hooks/useRegisterAction'
import {
  PdfDocumentCanvas,
  type CanvasRegion,
  type CanvasThumbnail,
} from '../canvas'
import '../verification/VerificationUx.css'

export interface AnnotationQueueRouteProps {
  callsUrl?: string
  pageImageIndexUrl?: string
  initialCalls?: readonly NormalizedAnnotationCall[]
  initialPageImageIndex?: PageImageIndex
  fetchImpl?: typeof fetch
  artifactsRoot?: string
}

const DEFAULT_CALLS_URL = '/artifacts/pdf-lab/annotation_call.json'
const DECISIONS_ENDPOINT = '/api/pdf-lab/annotation-decisions'
const TIMING_ENDPOINT = '/api/pdf-lab/ux-timing-events'
const QUEUE_STATE_KEY = 'pdf-oxide.annotation-queue-state.v1'
const ROW_HEIGHT = 82
const OVERSCAN = 8

type WorkbenchAction = 'fix_text' | 'fix_type' | 'fix_bounds' | null

function interactiveAttributes(qid: string, action: string, label: string) {
  return {
    'aria-label': label,
    'data-qid': qid,
    'data-qs-action': action,
    title: label,
  }
}

function hasExtractionData(item: AnnotationQueueItem): boolean {
  return Boolean(item.currentType || item.textExcerpt || item.bbox)
}

function rawText(item: AnnotationQueueItem, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = item.raw[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

function visibleMissingCharacter(character: string): string {
  return character.codePointAt(0)! < 0x20 ? '×' : character
}

function oracleEvidenceWindow(oracleExcerpt: string | null, missingText: string | null): string | null {
  if (!oracleExcerpt || !missingText) return oracleExcerpt
  const firstMissingIndex = [...missingText]
    .map((character) => oracleExcerpt.indexOf(character))
    .filter((index) => index >= 0)
    .sort((a, b) => a - b)[0]
  if (firstMissingIndex === undefined) return oracleExcerpt
  const start = Math.max(0, firstMissingIndex - 36)
  const end = Math.min(oracleExcerpt.length, firstMissingIndex + 320)
  return `${start > 0 ? '…' : ''}${oracleExcerpt.slice(start, end)}${end < oracleExcerpt.length ? '…' : ''}`
}

function highlightedOracleText(oracleExcerpt: string | null, missingText: string | null) {
  if (!oracleExcerpt) return <span className="pdf-verify-absent">Oracle excerpt was not supplied.</span>
  if (!missingText) return <span>{oracleExcerpt}</span>
  const remaining = new Map<string, number>()
  for (const character of missingText) remaining.set(character, (remaining.get(character) ?? 0) + 1)
  return (
    <>
      {[...oracleExcerpt].map((character, index) => {
        const count = remaining.get(character) ?? 0
        if (count <= 0) return <Fragment key={`${index}-${character}`}>{character}</Fragment>
        remaining.set(character, count - 1)
        return (
          <mark
            key={`${index}-${character}`}
            title={`Missing from pdf-oxide extraction: U+${character.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')}`}
          >
            {visibleMissingCharacter(character)}
          </mark>
        )
      })}
    </>
  )
}

const TASK_BANNERS: Record<string, { title: string; description: string; tone: string }> = {
  char_parity_deficit: {
    title: 'Missing Characters Detected',
    description: 'The extracted text is missing characters compared to the original PDF. Please apply the suggested fix or type the missing text into the editor.',
    tone: 'is-danger',
  },
  low_confidence: {
    title: 'Low Engine Confidence',
    description: 'The extraction engine is unsure about the highlighted region. Please verify it against the original document.',
    tone: 'is-warning',
  },
  reviewer_flagged: {
    title: 'Reviewer Flagged',
    description: 'A second-pass reviewer flagged this element. Read the finding and verify against the original page.',
    tone: 'is-info',
  },
  unadjudicated_residual: {
    title: 'Unresolved Residual',
    description: 'A prior sweep left this element unresolved. Verify it against the original page and decide.',
    tone: 'is-info',
  },
}

function TaskSummaryBanner({ reason }: { reason: string }) {
  const banner = TASK_BANNERS[reason] ?? TASK_BANNERS.low_confidence
  return (
    <div className={`pdf-verify-task-banner ${banner.tone}`} data-testid="task-summary-banner" role="note">
      <AlertTriangle aria-hidden="true" />
      <div>
        <strong>{banner.title}</strong>
        <p>{banner.description}</p>
      </div>
    </div>
  )
}

function EvidencePanel({ item }: { item: AnnotationQueueItem }) {
  if (item.reason === 'char_parity_deficit') {
    return (
      <div className="pdf-verify-evidence-diff" data-testid="char-parity-evidence">
        <div>
          <span>pdf-oxide</span>
          <p>{item.textExcerpt || <em className="pdf-verify-absent">text_excerpt was not supplied.</em>}</p>
        </div>
        <div>
          <span>oracle</span>
          <p>{highlightedOracleText(oracleEvidenceWindow(item.oracleExcerpt, item.missingText), item.missingText)}</p>
        </div>
        <small>
          Missing text: {item.missingText
            ? <code data-testid="missing-text-highlight">{[...item.missingText].map(visibleMissingCharacter).join('')}</code>
            : item.missingTextDerivationError ?? 'missing_text was not supplied.'}
        </small>
      </div>
    )
  }
  if (item.reason === 'reviewer_flagged') {
    const finding = rawText(item, 'finding', 'reviewer_finding', 'reviewer_flag', 'review_notes')
    return <p>{finding ?? <span className="pdf-verify-absent">Reviewer finding text was not supplied.</span>}</p>
  }
  if (item.reason === 'low_confidence') {
    return (
      <dl className="pdf-verify-signal-list">
        <div><dt>Signal</dt><dd>Engine raised a low-confidence classification.</dd></div>
        <div><dt>Basis</dt><dd>{item.accuracyBasis || 'Accuracy basis was not supplied.'}</dd></div>
        <div><dt>Type</dt><dd>{item.currentType || 'No proposed type was supplied.'}</dd></div>
        <div><dt>Privacy</dt><dd>The raw confidence value remains blinded.</dd></div>
      </dl>
    )
  }
  return (
    <p>{rawText(item, 'finding', 'detail', 'message')
      ?? <span className="pdf-verify-absent">No additional residual evidence was supplied.</span>}</p>
  )
}

function qidQualifier(value: string): string {
  return value.trim().replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'unknown'
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

async function loadAnnotationCallsFromUrl(url: string, fetchImpl: typeof fetch): Promise<NormalizedAnnotationCall[]> {
  const response = await fetchImpl(url)
  if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`)
  const raw = await response.json() as unknown
  const record = asRecord(raw)
  if (record?.schema === ANNOTATION_CALL_SCHEMA) {
    const sourceUrl = new URL(response.url || url, window.location.href)
    const pathParts = sourceUrl.pathname.split('/').filter(Boolean).map(decodeURIComponent)
    const filenameIndex = pathParts.lastIndexOf('annotation_call.json')
    const sourceName = filenameIndex > 0 ? pathParts[filenameIndex - 1] : url
    return [normalizeAnnotationCall(raw, sourceName)]
  }

  const callEntries = record?.calls ?? record?.annotation_calls
  if (Array.isArray(callEntries) && callEntries.every((entry) => typeof entry === 'string')) {
    return (await Promise.all(callEntries.map((entry) => loadAnnotationCallsFromUrl(new URL(entry, response.url || url).toString(), fetchImpl)))).flat()
  }
  return normalizeAnnotationCallCollection(raw)
}

function selectedPageImage(item: AnnotationQueueItem, index: PageImageIndex | null): PageImageRef | null {
  try {
    const direct = item.pageImageRefs == null
      ? []
      : normalizePageImageRefs(item.pageImageRefs, {
          doc: item.documentId,
          page: item.page,
          pdfSha256: item.pdfSha256,
        })
    const indexed = lookupPageImageRefs(index, item.documentId, item.page)[0]
      ?? index?.all.find((candidate) => (
        candidate.page === item.page
        && candidate.pdfSha256 === item.pdfSha256
      ))
      ?? null
    if (direct[0] && indexed && direct[0].sha256 === indexed.sha256) {
      return {
        ...indexed,
        ...direct[0],
        width: direct[0].width ?? indexed.width,
        height: direct[0].height ?? indexed.height,
      }
    }
    return direct[0] ?? indexed
  } catch {
    return null
  }
}

interface VirtualRowsProps {
  rows: readonly AnnotationQueueItem[]
  selectedId: string | null
  decisions: ReadonlyMap<string, AnnotationDecisionEvent>
  onSelect: (item: AnnotationQueueItem) => void
}

interface AnnotationRowProps {
  item: AnnotationQueueItem
  rowIndex: number
  selected: boolean
  decision?: AnnotationDecisionEvent
  onSelect: (item: AnnotationQueueItem) => void
}

function AnnotationRow({ item, rowIndex, selected, decision, onSelect }: AnnotationRowProps) {
  const qid = `annotation-queue:row:${qidQualifier(item.id)}`
  useRegisterAction(qid, {
    app: 'pdf-lab',
    action: 'ANNOTATION_QUEUE_SELECT_ROW',
    label: `Select ${item.documentId} page ${item.page}`,
    description: 'Select an annotation call and show its extraction evidence in the detail inspector',
  })

  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      data-testid="annotation-row"
      data-qid={qid}
      data-qs-action="ANNOTATION_QUEUE_SELECT_ROW"
      title={`Inspect annotation call for ${item.documentId} page ${item.page}`}
      className={`pdf-verify-annotation-row ${selected ? 'is-selected' : ''}`}
      style={{ transform: `translateY(${rowIndex * ROW_HEIGHT}px)`, height: ROW_HEIGHT }}
      onClick={() => onSelect(item)}
    >
      <span className={`pdf-verify-reason-dot is-${item.reason}`} aria-hidden="true" />
      <span className="pdf-verify-annotation-row__body">
        <strong>{item.documentId}</strong>
        <em>Page {item.page} · {item.kind}{item.currentType ? ` · ${item.currentType}` : ''}</em>
        <small>{item.textExcerpt || annotationReasonLabel(item.reason)}</small>
        {decision && (
          <small data-testid="queue-decision-badge">
            {decision.decision.replaceAll('_', ' ')}
          </small>
        )}
      </span>
      <ChevronRight aria-hidden="true" />
    </button>
  )
}

function VirtualRows({ rows, selectedId, decisions, onSelect }: VirtualRowsProps) {
  const [scrollTop, setScrollTop] = useState(0)
  const viewportRef = useRef<HTMLDivElement>(null)
  const viewportHeight = 640
  const visibleCount = Math.ceil(viewportHeight / ROW_HEIGHT)
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)
  const end = Math.min(rows.length, start + visibleCount + OVERSCAN * 2)
  const visible = rows.slice(start, end)

  useEffect(() => {
    setScrollTop(0)
    if (viewportRef.current) viewportRef.current.scrollTop = 0
  }, [rows])

  return (
    <div
      ref={viewportRef}
      className="pdf-verify-virtual"
      style={{ height: viewportHeight }}
      onScroll={(event: UIEvent<HTMLDivElement>) => setScrollTop(event.currentTarget.scrollTop)}
      role="listbox"
      aria-label="Annotation calls"
      data-testid="annotation-virtual-list"
    >
      <div className="pdf-verify-virtual__spacer" style={{ height: rows.length * ROW_HEIGHT }}>
        {visible.map((item, offset) => {
          const rowIndex = start + offset
          return (
            <AnnotationRow
              key={item.id}
              item={item}
              rowIndex={rowIndex}
              selected={selectedId === item.id}
              decision={decisions.get(item.id)}
              onSelect={onSelect}
            />
          )
        })}
      </div>
    </div>
  )
}

export function AnnotationQueueRoute({
  callsUrl = DEFAULT_CALLS_URL,
  pageImageIndexUrl,
  initialCalls,
  initialPageImageIndex,
  fetchImpl = fetch,
  artifactsRoot = '(the configured PDF Lab artifact root)',
}: AnnotationQueueRouteProps) {
  useRegisterAction('annotation-queue:search:query', {
    app: 'pdf-lab',
    action: 'ANNOTATION_QUEUE_SEARCH',
    label: 'Search annotation calls',
    description: 'Filter annotation calls by page, type, reason, or extracted text',
  })
  useRegisterAction('annotation-queue:filter:document', {
    app: 'pdf-lab',
    action: 'ANNOTATION_QUEUE_FILTER_DOCUMENT',
    label: 'Filter by document',
    description: 'Show annotation calls for one source document',
  })
  useRegisterAction('annotation-queue:filter:reason', {
    app: 'pdf-lab',
    action: 'ANNOTATION_QUEUE_FILTER_REASON',
    label: 'Filter by reason',
    description: 'Show annotation calls for one engine-raised reason',
  })
  useRegisterAction('annotation-queue:filter:kind', {
    app: 'pdf-lab',
    action: 'ANNOTATION_QUEUE_FILTER_KIND',
    label: 'Filter by element kind',
    description: 'Show annotation calls for block, region, or page elements',
  })
  const [calls, setCalls] = useState<NormalizedAnnotationCall[]>(initialCalls ? [...initialCalls] : [])
  const [pageImages, setPageImages] = useState<PageImageIndex | null>(initialPageImageIndex ?? null)
  const [decisions, setDecisions] = useState<Map<string, AnnotationDecisionEvent>>(new Map())
  const [loading, setLoading] = useState(!initialCalls)
  const [error, setError] = useState<string | null>(null)
  const cachedState = useMemo(() => {
    try {
      return JSON.parse(localStorage.getItem(QUEUE_STATE_KEY) ?? '{}') as Record<string, unknown>
    } catch {
      return {}
    }
  }, [])
  const [documentFilter, setDocumentFilter] = useState(
    typeof cachedState.documentFilter === 'string' ? cachedState.documentFilter : '*',
  )
  const [reasonFilter, setReasonFilter] = useState<'*' | AnnotationReason>(
    typeof cachedState.reasonFilter === 'string' ? cachedState.reasonFilter as '*' | AnnotationReason : '*',
  )
  const [kindFilter, setKindFilter] = useState<'*' | AnnotationKind>(
    typeof cachedState.kindFilter === 'string' ? cachedState.kindFilter as '*' | AnnotationKind : '*',
  )
  const [searchText, setSearchText] = useState(
    typeof cachedState.searchText === 'string' ? cachedState.searchText : '',
  )
  const deferredSearch = useDeferredValue(searchText.trim().toLowerCase())
  const [selectedId, setSelectedId] = useState<string | null>(
    typeof cachedState.selectedId === 'string' ? cachedState.selectedId : null,
  )
  const [status, setStatus] = useState<string | null>(
    typeof cachedState.status === 'string' ? cachedState.status : null,
  )
  const [saving, setSaving] = useState(false)
  const [correctedType, setCorrectedType] = useState<ElementType>('Body')
  const [correctedText, setCorrectedText] = useState('')
  const [correctedBounds, setCorrectedBounds] = useState<[string, string, string, string]>(['', '', '', ''])
  const [workbenchAction, setWorkbenchAction] = useState<WorkbenchAction>(null)
  const [railCollapsed, setRailCollapsed] = useState(false)
  const [canvasRegions, setCanvasRegions] = useState<CanvasRegion[]>([])
  const [showContext, setShowContext] = useState(true)
  const [contextItems, setContextItems] = useState<Array<{ page: number; type: string; bbox: number[]; label: string; table_data?: string[][] }> | null>(null)

  useEffect(() => {
    if (initialCalls) return
    let cancelled = false
    setLoading(true)
    setError(null)
    const urls = callsUrl.split(',').map((url) => url.trim()).filter(Boolean)
    void Promise.all([
      Promise.all(urls.map((url) => loadAnnotationCallsFromUrl(url, fetchImpl))).then((groups) => groups.flat()),
      pageImageIndexUrl
        ? Promise.all(pageImageIndexUrl.split(',').map((url) => url.trim()).filter(Boolean).map(async (url) => {
            const response = await fetchImpl(url)
            if (!response.ok) throw new Error('page image index unavailable')
            return parsePageImageIndex(await response.json(), { indexUrl: url })
          })).then(mergePageImageIndexes)
        : Promise.resolve(null),
      fetchImpl(DECISIONS_ENDPOINT).then(async (response) => {
        if (response.status === 404) return [] as AnnotationDecisionEvent[]
        if (!response.ok) throw new Error(`annotation decisions returned HTTP ${response.status}`)
        const payload = await response.json() as { active?: unknown[] }
        return (payload.active ?? []).filter(isAnnotationDecisionEvent)
      }),
    ]).then(([loadedCalls, loadedImages, loadedDecisions]) => {
      if (cancelled) return
      setCalls(loadedCalls)
      setPageImages(loadedImages)
      setDecisions(new Map(loadedDecisions.map((event) => [event.item_id, event])))
    }).catch((loadError) => {
      if (!cancelled) setError(loadError instanceof Error ? loadError.message : String(loadError))
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [callsUrl, fetchImpl, initialCalls, pageImageIndexUrl])

  useEffect(() => {
    localStorage.setItem(QUEUE_STATE_KEY, JSON.stringify({
      documentFilter,
      reasonFilter,
      kindFilter,
      searchText,
      selectedId,
      status,
    }))
  }, [documentFilter, kindFilter, reasonFilter, searchText, selectedId, status])

  const allItems = useMemo(() => flattenAnnotationItems(calls), [calls])
  const documents = useMemo(() => [...new Set(allItems.map((item) => item.documentId))].sort(), [allItems])
  const filtered = useMemo(() => allItems.filter((item) => {
    if (documentFilter !== '*' && item.documentId !== documentFilter) return false
    if (reasonFilter !== '*' && item.reason !== reasonFilter) return false
    if (kindFilter !== '*' && item.kind !== kindFilter) return false
    if (deferredSearch) {
      const haystack = [item.documentId, item.currentType, item.textExcerpt, item.reason, item.kind, String(item.page)]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      if (!haystack.includes(deferredSearch)) return false
    }
    return true
  }), [allItems, deferredSearch, documentFilter, kindFilter, reasonFilter])

  useEffect(() => {
    if (loading || filtered.length === 0) return
    if (!filtered.some((item) => item.id === selectedId)) setSelectedId(filtered[0]?.id ?? null)
  }, [filtered, loading, selectedId])

  const selected = filtered.find((item) => item.id === selectedId) ?? null
  const pageImage = useMemo(
    () => selected ? selectedPageImage(selected, pageImages) : null,
    [pageImages, selected],
  )
  const bbox = useMemo(() => {
    if (!selected?.bbox) return undefined
    if (selected.normalizedBbox) return selected.normalizedBbox
    if (!pageImage) return undefined
    try {
      return normalizePdfBboxXywh(selected.bbox, pageImage)
    } catch {
      return undefined
    }
  }, [pageImage, selected])

  useEffect(() => {
    setCorrectedText(selected?.textExcerpt ?? '')
    setCorrectedType(
      selected?.currentType && ELEMENT_TYPES.includes(selected.currentType as ElementType)
        ? selected.currentType as ElementType
        : 'Body',
    )
    setCorrectedBounds(selected?.bbox ? selected.bbox.map(String) as [string, string, string, string] : ['', '', '', ''])
    setWorkbenchAction(selected?.reason === 'char_parity_deficit' ? 'fix_text' : null)
    setCanvasRegions(bbox ? [{
      id: selected?.id ?? 'selected',
      bbox: [bbox[0], bbox[1], bbox[0] + bbox[2], bbox[1] + bbox[3]],
      label: selected?.currentType || selected?.kind || 'extraction',
      color: selected?.reason === 'char_parity_deficit' ? '#fb7185' : '#58b9ff',
      editable: true,
    }] : [])
  }, [bbox, selected])

  const thumbnails = useMemo<CanvasThumbnail[]>(() => {
    if (!selected) return []
    if (!pageImages) return pageImage
      ? [{ id: `${selected.documentId}:${selected.page}`, pageImage, label: `p${selected.page}` }]
      : []
    return pageImages.all
      .filter((candidate) => (
        candidate.pdfSha256 === selected.pdfSha256
        && (candidate.doc == null || candidate.doc === selected.documentId)
      ))
      .sort((a, b) => (a.page ?? 0) - (b.page ?? 0))
      .map((candidate) => ({
        id: `${selected.documentId}:${candidate.page ?? candidate.sha256}`,
        pageImage: candidate,
        label: `p${candidate.page ?? ''}`,
      }))
  }, [pageImage, pageImages, selected])

  const ghostRegions = useMemo<CanvasRegion[]>(() => {
    if (!selected || !contextItems || !pageImage?.width || !pageImage.height) return []
    const pageWidthPt = pageImage.width * (72 / 96)
    const pageHeightPt = pageImage.height * (72 / 96)
    return contextItems
      .filter((item) => item.page === selected.page && Array.isArray(item.bbox) && item.bbox.length === 4)
      .map((item, index) => ({
        id: `ghost-${index}`,
        // extraction context bboxes are top-left-origin xyxy points
        bbox: [
          Math.max(0, item.bbox[0] / pageWidthPt),
          Math.max(0, item.bbox[1] / pageHeightPt),
          Math.min(1, item.bbox[2] / pageWidthPt),
          Math.min(1, item.bbox[3] / pageHeightPt),
        ] as CanvasRegion['bbox'],
        label: `${item.type}${item.label ? ` · ${item.label}` : ''}`,
        ghost: true,
        editable: false,
      }))
  }, [contextItems, pageImage, selected])

  const extractedTable = useMemo(() => {
    if (!selected?.bbox || !contextItems || !pageImage?.height) return null
    const pageHeightPt = pageImage.height * (72 / 96)
    const [ix, iy, iw, ih] = selected.bbox
    const itemTop = pageHeightPt - iy - ih
    const itemBottom = pageHeightPt - iy
    const tables = contextItems.filter((entry) => entry.page === selected.page && entry.type === 'Table' && entry.table_data?.length)
    for (const table of tables) {
      const [tx0, ty0, tx1, ty1] = table.bbox
      const xOverlap = Math.min(ix + iw, tx1) - Math.max(ix, tx0)
      const yOverlap = Math.min(itemBottom, ty1) - Math.max(itemTop, ty0)
      if (xOverlap > 0 && yOverlap > 0) return table
    }
    return null
  }, [contextItems, pageImage, selected])

  const bugReportMarkdown = useMemo(() => {
    if (!selected) return ''
    const lines = [
      `## Bug Report: ${annotationReasonLabel(selected.reason)} — ${selected.documentId} page ${selected.page}`,
      `- Engine: ${selected.engineName ?? 'pdf-oxide'} ${selected.engineVersion ?? ''} (commit ${selected.engineCommit ?? 'unknown'})`,
      `- PDF sha256: ${selected.pdfSha256}`,
      `- Kind: ${selected.kind}${selected.bbox ? ` · bbox [${selected.bbox.map((value) => value.toFixed(1)).join(', ')}] (pdf points, bottom-left origin)` : ''}`,
      selected.missingText
        ? `- Missing characters (${[...selected.missingText].length}): ${[...new Set([...selected.missingText])].map((c) => `U+${c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, '0')}`).join(' ')}`
        : null,
      selected.textExcerpt ? `- Engine extracted: ${selected.textExcerpt.slice(0, 200)}` : '- Engine extracted: (nothing supplied)',
      selected.oracleExcerpt ? `- Oracle (pdftotext) saw: ${selected.oracleExcerpt.slice(0, 200)}` : null,
      `- Queue item: ${selected.id}`,
    ].filter(Boolean)
    return lines.join('\n')
  }, [selected])

  const updateCanvasRegions = (nextRegions: CanvasRegion[]) => {
    const next = nextRegions[nextRegions.length - 1]
    if (!next || !pageImage?.width || !pageImage.height) {
      setCanvasRegions(nextRegions)
      return
    }
    const [x0, y0, x1, y1] = next.bbox
    const bounds: [number, number, number, number] = [
      x0 * pageImage.width,
      (1 - y1) * pageImage.height,
      (x1 - x0) * pageImage.width,
      (y1 - y0) * pageImage.height,
    ]
    setCorrectedBounds(bounds.map((value) => value.toFixed(3)) as [string, string, string, string])
    setCanvasRegions([{ ...next, id: selected?.id ?? next.id }])
  }

  const saveDecision = async (
    decision: AnnotationDecision,
    options: { correctedText?: string; correctedBounds?: [number, number, number, number] } = {},
  ) => {
    if (!selected || !pageImage || saving) return
    setSaving(true)
    setError(null)
    const startedAt = new Date()
    try {
      const bounds = correctedBounds.map(Number) as [number, number, number, number]
      const input = buildAnnotationDecisionInput(selected, decision, {
        ...(decision === 'correct_type' ? { correctedType } : {}),
        ...(decision === 'correct_bounds' ? { correctedBounds: options.correctedBounds ?? bounds } : {}),
        ...(options.correctedText !== undefined ? { correctedText: options.correctedText } : {}),
        ...(decisions.get(selected.id) ? { revisionOf: decisions.get(selected.id)?.event_id } : {}),
      })
      const response = await fetchImpl(DECISIONS_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      })
      if (!response.ok) throw new Error(`decision write failed (${response.status}): ${(await response.text()).slice(0, 240)}`)
      const payload = await response.json() as { event?: unknown; active?: unknown[] }
      if (!isAnnotationDecisionEvent(payload.event)) throw new Error('decision response omitted its event')
      const nextDecisions = payload.active
        ? new Map(payload.active.filter(isAnnotationDecisionEvent).map((event) => [event.item_id, event]))
        : new Map(decisions).set(selected.id, payload.event)
      setDecisions(nextDecisions)
      const displayDecision = decision.replaceAll('_', ' ')
      setStatus(`Saved ${displayDecision}`)

      const params = new URLSearchParams(window.location.hash.split('?', 2)[1] ?? window.location.search)
      const workloadId = params.get('workload')
      const fixtureSha256 = params.get('fixtureHash')
      const uiCommit = params.get('uiCommit')
      if (workloadId && fixtureSha256 && uiCommit) {
        const completedAt = new Date()
        const seed = JSON.stringify({
          workloadId,
          itemId: selected.id,
          decision,
          completedAt: completedAt.toISOString(),
        })
        const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(seed))
        const eventId = [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
        const timingResponse = await fetchImpl(TIMING_ENDPOINT, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            event_id: eventId,
            workload_id: workloadId,
            fixture_sha256: fixtureSha256,
            ui_commit: uiCommit,
            item_id: selected.id,
            action: decision,
            started_at: startedAt.toISOString(),
            completed_at: completedAt.toISOString(),
            duration_ms: Math.max(0, completedAt.getTime() - startedAt.getTime()),
          }),
        })
        if (!timingResponse.ok) {
          throw new Error(`timing write failed (${timingResponse.status}): ${(await timingResponse.text()).slice(0, 240)}`)
        }
      }

      const selectedIndex = filtered.findIndex((item) => item.id === selected.id)
      const next = filtered.find((item, index) => index > selectedIndex && !nextDecisions.has(item.id))
        ?? filtered.find((item) => !nextDecisions.has(item.id))
      if (next) setSelectedId(next.id)
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : String(saveError))
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    if (!selected) return
    let cancelled = false
    fetch(`/artifacts/pdf-lab/extractions/${encodeURIComponent(selected.documentId)}.json`)
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (cancelled) return
        setContextItems(payload && Array.isArray(payload.items) ? payload.items : null)
      })
      .catch(() => { if (!cancelled) setContextItems(null) })
    return () => { cancelled = true }
  }, [selected?.documentId])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) return
      if (!selected || saving) return
      if (event.key === '1' && hasExtractionData(selected)) void saveDecision('accept')
      if (event.key === '2') setWorkbenchAction('fix_text')
      if (event.key === '3') setWorkbenchAction('fix_type')
      if (event.key === '4') setWorkbenchAction('fix_bounds')
      if (event.key === '5') void saveDecision('not_an_element')
      if (event.key === '6') void saveDecision('defer')
      if (event.key === 'v' || event.key === 'V') setShowContext((current) => !current)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  })

  if (loading) {
    return (
      <main className="pdf-verify-route pdf-verify-route--center" data-confidence-hidden="true">
        <Loader2 className="pdf-verify-spin" />
        <p>Loading annotation calls…</p>
      </main>
    )
  }

  if (error) {
    return (
      <main className="pdf-verify-route pdf-verify-route--center" data-confidence-hidden="true">
        <AlertTriangle />
        <h1>Annotation files need attention</h1>
        <p>PDF Lab found annotation data but could not read it safely. Confirm each annotation_call.json and page_images_v1.json is valid and readable.</p>
        <p>The server looked under <code>{artifactsRoot}</code>.</p>
      </main>
    )
  }

  if (allItems.length === 0) {
    return (
      <main className="pdf-verify-route pdf-verify-route--center" data-confidence-hidden="true" data-testid="annotation-guided-empty">
        <FileWarning />
        <h1>No annotation calls are mounted</h1>
        <p>Add annotation-calls/&lt;document&gt;/annotation_call.json beneath the artifact root, then reload this route.</p>
        <p>The server looked under <code>{artifactsRoot}</code>.</p>
      </main>
    )
  }

  return (
    <>
    <main
      className="pdf-verify-route pdf-verify-queue pdf-verify-adjudication"
      data-confidence-hidden="true"
      data-testid="annotation-queue-route"
      data-selected-id={selectedId ?? undefined}
    >
      <header className="pdf-verify-header pdf-verify-adjudication__header">
        <div>
          <span className="pdf-verify-kicker">Salvaged canvas · final 5%</span>
          <h1>Extraction adjudication workbench</h1>
          <p>{allItems.length.toLocaleString()} engine-raised items. Compare the original page, pdf-oxide extraction, and exact flag evidence before writing a decision.</p>
        </div>
        <div className="pdf-verify-proof-chip"><FileWarning aria-hidden="true" /> {filtered.length.toLocaleString()} open</div>
      </header>

      <section className={`pdf-verify-adjudication__layout ${railCollapsed ? 'is-rail-collapsed' : ''}`}>
        <aside className="pdf-verify-item-rail" data-testid="annotation-item-rail">
          <div className="pdf-verify-item-rail__head">
            {!railCollapsed && <strong>Items</strong>}
            <button
              type="button"
              onClick={() => setRailCollapsed((current) => !current)}
              aria-expanded={!railCollapsed}
              {...interactiveAttributes(
                'annotation-queue:rail:toggle',
                'ANNOTATION_QUEUE_TOGGLE_RAIL',
                railCollapsed ? 'Expand item rail' : 'Collapse item rail',
              )}
              data-testid="annotation-rail-toggle"
            >
              {railCollapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
            </button>
          </div>
          {railCollapsed ? (
            <span className="pdf-verify-item-rail__collapsed-count">{filtered.length}</span>
          ) : (
            <>
              <div className="pdf-verify-item-rail__filters">
                <label className="pdf-verify-search">
                  <Search aria-hidden="true" />
                  <input
                    value={searchText}
                    onChange={(event: ChangeEvent<HTMLInputElement>) => setSearchText(event.target.value)}
                    placeholder="Search items"
                    aria-label="Search annotation calls"
                    data-qid="annotation-queue:search:query"
                    data-qs-action="ANNOTATION_QUEUE_SEARCH"
                    title="Search annotation calls"
                  />
                </label>
                <label>
                  <Filter aria-hidden="true" />
                  <select
                    value={documentFilter}
                    onChange={(event: ChangeEvent<HTMLSelectElement>) => setDocumentFilter(event.target.value)}
                    aria-label="Filter by document"
                    data-qid="annotation-queue:filter:document"
                    data-qs-action="ANNOTATION_QUEUE_FILTER_DOCUMENT"
                    title="Filter annotation calls by document"
                  >
                    <option value="*">All documents</option>
                    {documents.map((document) => <option key={document} value={document}>{document}</option>)}
                  </select>
                </label>
                <label>
                  <select
                    value={reasonFilter}
                    onChange={(event: ChangeEvent<HTMLSelectElement>) => setReasonFilter(event.target.value as '*' | AnnotationReason)}
                    aria-label="Filter by reason"
                    data-qid="annotation-queue:filter:reason"
                    data-qs-action="ANNOTATION_QUEUE_FILTER_REASON"
                    title="Filter annotation calls by flag"
                  >
                    <option value="*">All flags</option>
                    <option value="low_confidence">Low confidence</option>
                    <option value="char_parity_deficit">Char parity deficit</option>
                    <option value="unadjudicated_residual">Residual</option>
                    <option value="reviewer_flagged">Reviewer flagged</option>
                  </select>
                </label>
                <label>
                  <select
                    value={kindFilter}
                    onChange={(event: ChangeEvent<HTMLSelectElement>) => setKindFilter(event.target.value as '*' | AnnotationKind)}
                    aria-label="Filter by kind"
                    data-qid="annotation-queue:filter:kind"
                    data-qs-action="ANNOTATION_QUEUE_FILTER_KIND"
                    title="Filter annotation calls by kind"
                  >
                    <option value="*">All kinds</option>
                    <option value="block">Block</option>
                    <option value="region">Region</option>
                    <option value="page">Page</option>
                  </select>
                </label>
              </div>
              {filtered.length > 0 ? (
                <VirtualRows
                  rows={filtered}
                  selectedId={selectedId}
                  decisions={decisions}
                  onSelect={(item) => setSelectedId(item.id)}
                />
              ) : (
                <div className="pdf-verify-empty">No items match.</div>
              )}
            </>
          )}
        </aside>

        <section className="pdf-verify-adjudication__workspace">
          {!selected ? (
            <div className="pdf-verify-empty">Select an annotation call.</div>
          ) : (
            <>
              <div className="pdf-verify-adjudication__meta">
                <div>
                  <strong>{selected.documentId}</strong>
                  <span>Page {selected.page} · {selected.kind} · {annotationReasonLabel(selected.reason)}</span>
                </div>
                <span>{filtered.findIndex((item) => item.id === selected.id) + 1} / {filtered.length}</span>
              </div>

              <div className="pdf-verify-adjudication__columns">
                <section className="pdf-verify-workbench-panel is-canvas" aria-labelledby="original-page-heading">
                  <header>
                    <span>01</span><h2 id="original-page-heading">Original page</h2>
                    <button
                      type="button"
                      className={`pdf-verify-context-toggle ${showContext ? 'is-on' : ''}`}
                      onClick={() => setShowContext((current) => !current)}
                      aria-pressed={showContext}
                      data-qid="annotation-queue:canvas:context-toggle"
                      data-qs-action="ANNOTATION_QUEUE_TOGGLE_CONTEXT"
                      title="Toggle surrounding extracted regions (Hotkey: V)"
                      data-testid="context-toggle"
                    >
                      {showContext ? 'Context on' : 'Context off'}
                    </button>
                  </header>
                  {pageImage && !bbox && (
                    <p className="pdf-verify-canvas-note" data-testid="no-region-note">
                      No region localized for this flag yet — drag on the page to draw the annotation yourself.
                    </p>
                  )}
                  {pageImage ? (
                    <PdfDocumentCanvas
                      pageImage={pageImage}
                      regions={ghostRegions.length && showContext ? [...ghostRegions, ...canvasRegions] : canvasRegions}
                      selectedRegionId={canvasRegions[0]?.id ?? null}
                      onSelectedRegionChange={() => undefined}
                      onRegionsChange={updateCanvasRegions}
                      allowDraw
                      drawLabel={workbenchAction === 'fix_bounds' ? 'corrected bounds' : 'annotation'}
                      drawColor="#a8ff57"
                      thumbnails={thumbnails}
                      selectedThumbnailId={`${selected.documentId}:${selected.page}`}
                      onThumbnailSelect={(thumbnail) => {
                        const candidate = allItems.find((item) => (
                          item.documentId === selected.documentId
                          && item.page === thumbnail.pageImage.page
                        ))
                        if (candidate) setSelectedId(candidate.id)
                      }}
                      alt={`Original PDF page ${selected.page} for ${selected.documentId}`}
                      actionQualifier={`annotation-${qidQualifier(selected.id)}`}
                    />
                  ) : (
                    <div className="pdf-verify-contract-blocker is-small">
                      <AlertTriangle aria-hidden="true" />
                      <strong>Original page image unavailable</strong>
                      <p>Visual adjudication is disabled until this page image is mounted.</p>
                    </div>
                  )}
                </section>

                <section className="pdf-verify-workbench-panel is-extraction" aria-labelledby="extraction-heading">
                  <header><span>02</span><h2 id="extraction-heading">Pdf-oxide extracted</h2></header>
                  <dl className="pdf-verify-extraction-record">
                    <div><dt>Type</dt><dd>{selected.currentType || <span className="pdf-verify-absent">No type supplied</span>}</dd></div>
                    <div><dt>Text</dt><dd>{selected.textExcerpt || <span className="pdf-verify-absent">No text_excerpt supplied</span>}</dd></div>
                    <div><dt>Bounds</dt><dd><code>{selected.bbox ? `[${selected.bbox.join(', ')}]` : 'No bbox supplied'}</code></dd></div>
                    <div>
                      <dt>Engine</dt>
                      <dd>
                        <span title={`Engine commit ${selected.engineCommit}`} data-testid="engine-attribution">
                          {selected.engineName || 'Engine name missing'} {selected.engineVersion || 'version missing'}
                        </span>
                      </dd>
                    </div>
                  </dl>

                  {extractedTable && (
                    <div className="pdf-verify-extracted-table" data-testid="extracted-table">
                      <span>Extracted table ({extractedTable.label})</span>
                      <div className="pdf-verify-extracted-table__scroll">
                        <table>
                          <tbody>
                            {extractedTable.table_data!.map((row, rowIndex) => (
                              <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}</tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                  {(selected.reason === 'char_parity_deficit' || workbenchAction === 'fix_text') && (
                    <label className="pdf-verify-field pdf-verify-corrected-text">
                      <span>Corrected text</span>
                      <textarea
                        value={correctedText}
                        onChange={(event) => setCorrectedText(event.target.value)}
                        rows={6}
                        aria-label="Corrected extraction text"
                        data-qid="annotation-queue:input:corrected-text"
                        data-qs-action="ANNOTATION_QUEUE_SET_CORRECTED_TEXT"
                        title="Edit the extraction text that will be written to corrected_text"
                        data-testid="annotation-corrected-text"
                      />
                      <button
                        type="button"
                        onClick={() => void saveDecision('accept', { correctedText })}
                        disabled={!pageImage || saving || !correctedText.trim()}
                        {...interactiveAttributes(
                          'annotation-queue:save:corrected-text',
                          'ANNOTATION_QUEUE_SAVE_CORRECTED_TEXT',
                          'Save corrected text to the decision ledger',
                        )}
                        data-testid="annotation-save-text"
                      >
                        Save corrected_text
                      </button>
                    </label>
                  )}

                  {workbenchAction === 'fix_type' && (
                    <label className="pdf-verify-field">
                      <span>Corrected type</span>
                      <select
                        value={correctedType}
                        onChange={(event) => setCorrectedType(event.target.value as ElementType)}
                        aria-label="Corrected element type"
                        data-qid="annotation-queue:input:corrected-type"
                        data-qs-action="ANNOTATION_QUEUE_SET_CORRECTED_TYPE"
                        title="Choose the corrected element type"
                        data-testid="annotation-corrected-type"
                      >
                        {ELEMENT_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
                      </select>
                      <button
                        type="button"
                        onClick={() => void saveDecision('correct_type')}
                        disabled={!pageImage || saving}
                        {...interactiveAttributes(
                          'annotation-queue:save:corrected-type',
                          'ANNOTATION_QUEUE_SAVE_CORRECTED_TYPE',
                          'Save corrected element type',
                        )}
                        data-testid="annotation-save-type"
                      >
                        Save type
                      </button>
                    </label>
                  )}

                  <fieldset className="pdf-verify-bounds-fields" disabled={workbenchAction !== 'fix_bounds' || saving}>
                    <legend>Bounds · PDF points</legend>
                    {(['x', 'y', 'width', 'height'] as const).map((name, boundIndex) => (
                      <label key={name}>
                        <span>{name}</span>
                        <input
                          type="number"
                          min={0}
                          value={correctedBounds[boundIndex]}
                          onChange={(event) => setCorrectedBounds((previous) => {
                            const next = [...previous] as [string, string, string, string]
                            next[boundIndex] = event.target.value
                            return next
                          })}
                          aria-label={`Corrected bounds ${name}`}
                          data-qid={`annotation-queue:input:bound-${name}`}
                          data-qs-action="ANNOTATION_QUEUE_SET_CORRECTED_BOUNDS"
                          title={`Set corrected bounds ${name} in PDF points`}
                          data-testid={`annotation-bound-${name}`}
                        />
                      </label>
                    ))}
                    {workbenchAction === 'fix_bounds' && (
                      <button
                        type="button"
                        onClick={() => void saveDecision('correct_bounds')}
                        disabled={!pageImage || saving || correctedBounds.some((value) => !value)}
                        {...interactiveAttributes(
                          'annotation-queue:save:corrected-bounds',
                          'ANNOTATION_QUEUE_SAVE_CORRECTED_BOUNDS',
                          'Save corrected bounds to the decision ledger',
                        )}
                        data-testid="annotation-save-bounds"
                      >
                        Save bounds
                      </button>
                    )}
                  </fieldset>
                </section>

                <section className="pdf-verify-workbench-panel is-evidence" aria-labelledby="flag-evidence-heading">
                  <header><span>03</span><h2 id="flag-evidence-heading">Agent analysis</h2></header>
                  <TaskSummaryBanner reason={selected.reason} />
                  <EvidencePanel item={selected} />
                  <dl className="pdf-verify-details">
                    <div><dt>Reason</dt><dd>{selected.reason}</dd></div>
                    <div><dt>Oracle</dt><dd>{selected.oracleExcerpt ? 'Supplied' : 'Not supplied'}</dd></div>
                    <div>
                      <dt>Missing text</dt>
                      <dd>{selected.missingText
                        ? [...selected.missingText].map(visibleMissingCharacter).join('')
                        : 'Not supplied'}</dd>
                    </div>
                    <div><dt>Queue ID</dt><dd><code>{selected.id}</code></dd></div>
                  </dl>
                  <div className="pdf-verify-discrepancy" data-testid="discrepancy-comment">
                    <strong>Discrepancy</strong>
                    <p>
                      {selected.reason === 'char_parity_deficit' && selected.missingText
                        ? `The agent oracle found ${[...selected.missingText].length} character(s) in this region that pdf-oxide's extraction dropped${extractedTable ? ' — inspect the extracted table cells against the original page' : ''}.`
                        : selected.reason === 'reviewer_flagged'
                          ? 'A second-pass reviewer disputed this extraction; compare the finding with the original page.'
                          : selected.reason === 'low_confidence'
                            ? 'The engine itself is uncertain about this extraction; no oracle disagreement is recorded.'
                            : 'A prior sweep left this element unresolved.'}
                    </p>
                    <button
                      type="button"
                      onClick={() => { void navigator.clipboard.writeText(bugReportMarkdown); setStatus('Bug report copied to clipboard.') }}
                      data-qid="annotation-queue:analysis:copy-bug-report"
                      data-qs-action="ANNOTATION_QUEUE_COPY_BUG_REPORT"
                      title="Copy a pre-filled bug report for this discrepancy"
                      data-testid="copy-bug-report"
                    >
                      Copy bug report
                    </button>
                  </div>
                  {status && <div className="pdf-verify-status is-success" role="status">{status}</div>}
                  {error && <div className="pdf-verify-status is-error" role="alert">{error}</div>}
                </section>
              </div>

              <footer className="pdf-verify-action-bar" aria-label="Adjudication actions">
                <button
                  type="button"
                  onClick={() => void saveDecision('accept')}
                  disabled={!pageImage || saving || !hasExtractionData(selected)}
                  {...interactiveAttributes('annotation-queue:action:accept', 'ANNOTATION_QUEUE_ACCEPT', 'Accept extraction')}
                  data-testid="annotation-accept"
                >
                  <Check aria-hidden="true" /><span><kbd>1</kbd> Accept</span>
                </button>
                <button
                  type="button"
                  className={workbenchAction === 'fix_text' ? 'is-active' : ''}
                  onClick={() => setWorkbenchAction('fix_text')}
                  disabled={!pageImage || saving}
                  {...interactiveAttributes('annotation-queue:action:fix-text', 'ANNOTATION_QUEUE_FIX_TEXT', 'Fix extraction text')}
                >
                  <Type aria-hidden="true" /><span><kbd>2</kbd> Fix text</span>
                </button>
                <button
                  type="button"
                  className={workbenchAction === 'fix_type' ? 'is-active' : ''}
                  onClick={() => setWorkbenchAction('fix_type')}
                  disabled={!pageImage || saving}
                  {...interactiveAttributes('annotation-queue:action:fix-type', 'ANNOTATION_QUEUE_FIX_TYPE', 'Fix element type')}
                >
                  <Tags aria-hidden="true" /><span><kbd>3</kbd> Fix type</span>
                </button>
                <button
                  type="button"
                  className={workbenchAction === 'fix_bounds' ? 'is-active' : ''}
                  onClick={() => setWorkbenchAction('fix_bounds')}
                  disabled={!pageImage || saving}
                  {...interactiveAttributes('annotation-queue:action:fix-bounds', 'ANNOTATION_QUEUE_FIX_BOUNDS', 'Fix element bounds')}
                >
                  <BoxSelect aria-hidden="true" /><span><kbd>4</kbd> Fix bounds</span>
                </button>
                <button
                  type="button"
                  onClick={() => void saveDecision('not_an_element')}
                  disabled={!pageImage || saving}
                  {...interactiveAttributes('annotation-queue:action:not-element', 'ANNOTATION_QUEUE_NOT_ELEMENT', 'Mark as not an element')}
                >
                  <Ban aria-hidden="true" /><span><kbd>5</kbd> Not an element</span>
                </button>
                <button
                  type="button"
                  onClick={() => void saveDecision('defer')}
                  disabled={!pageImage || saving}
                  {...interactiveAttributes('annotation-queue:action:defer', 'ANNOTATION_QUEUE_DEFER', 'Defer annotation item')}
                  data-testid="annotation-defer"
                >
                  <Clock3 aria-hidden="true" /><span><kbd>6</kbd> Defer</span>
                </button>
              </footer>
            </>
          )}
        </section>
      </section>
    </main>
    </>
  )
}
