import { useDeferredValue, useEffect, useMemo, useRef, useState, type ChangeEvent, type UIEvent } from 'react'
import { AlertTriangle, ChevronRight, FileWarning, Filter, Loader2, Search } from 'lucide-react'
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
  normalizePdfBboxXywh,
  normalizePageImageRefs,
  parsePageImageIndex,
  type PageImageIndex,
  type PageImageRef,
} from '../../adapters/pageImageRefs'
import { NormalizedPageOverlay } from '../verification/NormalizedPageOverlay'
import '../verification/VerificationUx.css'

export interface AnnotationQueueRouteProps {
  callsUrl?: string
  pageImageIndexUrl?: string
  initialCalls?: readonly NormalizedAnnotationCall[]
  initialPageImageIndex?: PageImageIndex
  fetchImpl?: typeof fetch
}

const DEFAULT_CALLS_URL = '/artifacts/pdf-lab/annotation_call.json'
const ROW_HEIGHT = 82
const OVERSCAN = 8

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
  onSelect: (item: AnnotationQueueItem) => void
}

function VirtualRows({ rows, selectedId, onSelect }: VirtualRowsProps) {
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
            <button
              type="button"
              role="option"
              aria-selected={selectedId === item.id}
              data-testid="annotation-row"
              key={item.id}
              className={`pdf-verify-annotation-row ${selectedId === item.id ? 'is-selected' : ''}`}
              style={{ transform: `translateY(${rowIndex * ROW_HEIGHT}px)`, height: ROW_HEIGHT }}
              onClick={() => onSelect(item)}
            >
              <span className={`pdf-verify-reason-dot is-${item.reason}`} aria-hidden="true" />
              <span className="pdf-verify-annotation-row__body">
                <strong>{item.documentId}</strong>
                <em>Page {item.page} · {item.kind}{item.currentType ? ` · ${item.currentType}` : ''}</em>
                <small>{item.textExcerpt || annotationReasonLabel(item.reason)}</small>
              </span>
              <ChevronRight aria-hidden="true" />
            </button>
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
}: AnnotationQueueRouteProps) {
  const [calls, setCalls] = useState<NormalizedAnnotationCall[]>(initialCalls ? [...initialCalls] : [])
  const [pageImages, setPageImages] = useState<PageImageIndex | null>(initialPageImageIndex ?? null)
  const [loading, setLoading] = useState(!initialCalls)
  const [error, setError] = useState<string | null>(null)
  const [documentFilter, setDocumentFilter] = useState('*')
  const [reasonFilter, setReasonFilter] = useState<'*' | AnnotationReason>('*')
  const [kindFilter, setKindFilter] = useState<'*' | AnnotationKind>('*')
  const [searchText, setSearchText] = useState('')
  const deferredSearch = useDeferredValue(searchText.trim().toLowerCase())
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => {
    if (initialCalls) return
    let cancelled = false
    setLoading(true)
    setError(null)
    const urls = callsUrl.split(',').map((url) => url.trim()).filter(Boolean)
    void Promise.all([
      Promise.all(urls.map((url) => loadAnnotationCallsFromUrl(url, fetchImpl))).then((groups) => groups.flat()),
      pageImageIndexUrl
        ? fetchImpl(pageImageIndexUrl).then(async (response) => {
            if (!response.ok) throw new Error(`${pageImageIndexUrl} returned HTTP ${response.status}`)
            return parsePageImageIndex(await response.json())
          })
        : Promise.resolve(null),
    ]).then(([loadedCalls, loadedImages]) => {
      if (cancelled) return
      setCalls(loadedCalls)
      setPageImages(loadedImages)
    }).catch((loadError) => {
      if (!cancelled) setError(loadError instanceof Error ? loadError.message : String(loadError))
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [callsUrl, fetchImpl, initialCalls, pageImageIndexUrl])

  const allItems = useMemo(() => flattenAnnotationItems(calls), [calls])
  const documents = useMemo(() => [...new Set(allItems.map((item) => item.documentId))].sort(), [allItems])
  const reasonCounts = useMemo(() => {
    const counts = new Map<AnnotationReason, number>()
    allItems.forEach((item) => counts.set(item.reason, (counts.get(item.reason) ?? 0) + 1))
    return counts
  }, [allItems])

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
    if (!filtered.some((item) => item.id === selectedId)) setSelectedId(filtered[0]?.id ?? null)
  }, [filtered, selectedId])

  const selected = filtered.find((item) => item.id === selectedId) ?? null
  const pageImage = selected ? selectedPageImage(selected, pageImages) : null
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
        <h1>Annotation queue failed to load</h1>
        <p>{error}</p>
      </main>
    )
  }

  return (
    <main className="pdf-verify-route pdf-verify-queue" data-confidence-hidden="true" data-testid="annotation-queue-route">
      <header className="pdf-verify-header">
        <div>
          <span className="pdf-verify-kicker">Human annotation calls</span>
          <h1>Extraction uncertainty queue</h1>
          <p>{allItems.length.toLocaleString()} engine-raised items. Confidence values remain blinded until calibration is accepted.</p>
        </div>
        <div className="pdf-verify-proof-chip"><FileWarning /> {filtered.length.toLocaleString()} visible</div>
      </header>

      <section className="pdf-verify-reason-strip" aria-label="Reason counts">
        {([...reasonCounts.entries()] as Array<[AnnotationReason, number]>).map(([reason, count]) => (
          <button
            type="button"
            key={reason}
            className={reasonFilter === reason ? 'is-active' : ''}
            onClick={() => setReasonFilter((current) => current === reason ? '*' : reason)}
          >
            <span className={`pdf-verify-reason-dot is-${reason}`} />
            <strong>{count.toLocaleString()}</strong>
            <em>{annotationReasonLabel(reason)}</em>
          </button>
        ))}
      </section>

      <section className="pdf-verify-queue-layout">
        <div className="pdf-verify-queue-list">
          <div className="pdf-verify-filters">
            <label className="pdf-verify-search">
              <Search aria-hidden="true" />
              <input value={searchText} onChange={(event: ChangeEvent<HTMLInputElement>) => setSearchText(event.target.value)} placeholder="Search page, type, or excerpt" />
            </label>
            <label>
              <Filter aria-hidden="true" />
              <select value={documentFilter} onChange={(event: ChangeEvent<HTMLSelectElement>) => setDocumentFilter(event.target.value)} aria-label="Filter by document">
                <option value="*">All documents</option>
                {documents.map((document) => <option key={document} value={document}>{document}</option>)}
              </select>
            </label>
            <label>
              <select value={reasonFilter} onChange={(event: ChangeEvent<HTMLSelectElement>) => setReasonFilter(event.target.value as '*' | AnnotationReason)} aria-label="Filter by reason">
                <option value="*">All reasons</option>
                <option value="low_confidence">Low confidence</option>
                <option value="char_parity_deficit">Char parity deficit</option>
                <option value="unadjudicated_residual">Residual</option>
                <option value="reviewer_flagged">Reviewer flagged</option>
              </select>
            </label>
            <label>
              <select value={kindFilter} onChange={(event: ChangeEvent<HTMLSelectElement>) => setKindFilter(event.target.value as '*' | AnnotationKind)} aria-label="Filter by kind">
                <option value="*">All kinds</option>
                <option value="block">Block</option>
                <option value="region">Region</option>
                <option value="page">Page</option>
              </select>
            </label>
          </div>
          {filtered.length > 0 ? (
            <VirtualRows rows={filtered} selectedId={selectedId} onSelect={(item) => setSelectedId(item.id)} />
          ) : (
            <div className="pdf-verify-empty">No annotation calls match the current filters.</div>
          )}
        </div>

        <aside className="pdf-verify-queue-inspector">
          {!selected ? (
            <div className="pdf-verify-empty">Select an annotation call.</div>
          ) : (
            <>
              <div className="pdf-verify-item-meta">
                <span>{selected.documentId}</span>
                <strong>Page {selected.page} · {selected.kind}</strong>
                <em>{annotationReasonLabel(selected.reason)}</em>
              </div>
              <div className="pdf-verify-proposal">
                <span>Current extraction</span>
                <strong>{selected.currentType || '(untyped)'}</strong>
                <p>{selected.textExcerpt || '(No text excerpt supplied.)'}</p>
              </div>
              {pageImage ? (
                <NormalizedPageOverlay
                  pageImage={pageImage}
                  bbox={bbox}
                  label={selected.currentType || selected.kind}
                  alt={`Original PDF page ${selected.page} for annotation call`}
                  compact
                />
              ) : (
                <div className="pdf-verify-contract-blocker is-small">
                  <AlertTriangle />
                  <strong>Original page image unavailable</strong>
                  <p>Queue triage may inspect metadata, but no visual adjudication should be accepted without the page image.</p>
                </div>
              )}
              <dl className="pdf-verify-details">
                <div><dt>Reason</dt><dd>{selected.reason}</dd></div>
                <div><dt>Engine</dt><dd><code>{selected.engineCommit}</code></dd></div>
                <div><dt>PDF</dt><dd><code>{selected.pdfSha256}</code></dd></div>
                <div><dt>Queue ID</dt><dd><code>{selected.id}</code></dd></div>
              </dl>
            </>
          )}
        </aside>
      </section>
    </main>
  )
}
