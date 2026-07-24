import { useDeferredValue, useEffect, useMemo, useRef, useState, type ChangeEvent, type UIEvent } from 'react'
import { AlertTriangle, Check, ChevronRight, FileWarning, Filter, Loader2, Search } from 'lucide-react'
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
import { NormalizedPageOverlay } from '../verification/NormalizedPageOverlay'
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

interface ReasonFilterButtonProps {
  active: boolean
  count: number
  onToggle: () => void
  reason: AnnotationReason
}

function ReasonFilterButton({ active, count, onToggle, reason }: ReasonFilterButtonProps) {
  const qid = `annotation-queue:reason:${qidQualifier(reason)}`
  const label = annotationReasonLabel(reason)
  useRegisterAction(qid, {
    app: 'pdf-lab',
    action: 'ANNOTATION_QUEUE_FILTER_REASON',
    label: `Filter by ${label}`,
    description: `Toggle the ${label} annotation reason filter`,
  })

  return (
    <button
      type="button"
      className={active ? 'is-active' : ''}
      onClick={onToggle}
      data-qid={qid}
      data-qs-action="ANNOTATION_QUEUE_FILTER_REASON"
      title={`Toggle ${label} filter`}
    >
      <span className={`pdf-verify-reason-dot is-${reason}`} />
      <strong>{count.toLocaleString()}</strong>
      <em>{label}</em>
    </button>
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
  const [correctedBounds, setCorrectedBounds] = useState<[string, string, string, string]>(['', '', '', ''])

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
    if (loading || filtered.length === 0) return
    if (!filtered.some((item) => item.id === selectedId)) setSelectedId(filtered[0]?.id ?? null)
  }, [filtered, loading, selectedId])

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

  const saveDecision = async (decision: AnnotationDecision) => {
    if (!selected || !pageImage || saving) return
    setSaving(true)
    setError(null)
    const startedAt = new Date()
    try {
      const bounds = correctedBounds.map(Number) as [number, number, number, number]
      const input = buildAnnotationDecisionInput(selected, decision, {
        ...(decision === 'correct_type' ? { correctedType } : {}),
        ...(decision === 'correct_bounds' ? { correctedBounds: bounds } : {}),
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
    <main
      className="pdf-verify-route pdf-verify-queue"
      data-confidence-hidden="true"
      data-testid="annotation-queue-route"
      data-selected-id={selectedId ?? undefined}
    >
      <header className="pdf-verify-header">
        <div>
          <span className="pdf-verify-kicker">Human annotation calls</span>
          <h1>Extraction uncertainty queue</h1>
          <p>{allItems.length.toLocaleString()} engine-raised items, prioritized for human feedback. Every item remains servable.</p>
        </div>
        <div className="pdf-verify-proof-chip"><FileWarning /> {filtered.length.toLocaleString()} visible</div>
      </header>

      <section className="pdf-verify-reason-strip" aria-label="Reason counts">
        {([...reasonCounts.entries()] as Array<[AnnotationReason, number]>).map(([reason, count]) => (
          <ReasonFilterButton
            key={reason}
            reason={reason}
            count={count}
            active={reasonFilter === reason}
            onToggle={() => setReasonFilter((current) => current === reason ? '*' : reason)}
          />
        ))}
      </section>

      <section className="pdf-verify-queue-layout">
        <div className="pdf-verify-queue-list">
          <div className="pdf-verify-filters">
            <label className="pdf-verify-search">
              <Search aria-hidden="true" />
              <input
                value={searchText}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setSearchText(event.target.value)}
                placeholder="Search page, type, or excerpt"
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
                title="Filter annotation calls by reason"
              >
                <option value="*">All reasons</option>
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
                title="Filter annotation calls by element kind"
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
                  actionQualifier={`annotation-${qidQualifier(selected.id)}`}
                  compact
                />
              ) : (
                <div className="pdf-verify-contract-blocker is-small">
                  <AlertTriangle />
                  <strong>Original page image unavailable</strong>
                  <p>Queue triage may inspect metadata, but no visual adjudication should be accepted without the page image.</p>
                </div>
              )}
              <div className="pdf-verify-decision-grid">
                <button
                  type="button"
                  onClick={() => void saveDecision('accept')}
                  disabled={!pageImage || saving}
                  data-testid="annotation-accept"
                >
                  <Check /> Accept
                </button>
                <button
                  type="button"
                  onClick={() => void saveDecision('defer')}
                  disabled={!pageImage || saving}
                  data-testid="annotation-defer"
                >
                  Defer
                </button>
              </div>
              <label className="pdf-verify-field">
                <span>Corrected type</span>
                <select
                  value={correctedType}
                  onChange={(event) => setCorrectedType(event.target.value as ElementType)}
                  data-testid="annotation-corrected-type"
                >
                  {ELEMENT_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
                </select>
              </label>
              <button
                type="button"
                onClick={() => void saveDecision('correct_type')}
                disabled={!pageImage || saving}
                data-testid="annotation-save-type"
              >
                Save corrected type
              </button>
              <div className="pdf-verify-filters" aria-label="Corrected bounds in PDF points">
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
                      data-testid={`annotation-bound-${name}`}
                    />
                  </label>
                ))}
              </div>
              <button
                type="button"
                onClick={() => void saveDecision('correct_bounds')}
                disabled={!pageImage || saving || correctedBounds.some((value) => !value)}
                data-testid="annotation-save-bounds"
              >
                Save corrected bounds
              </button>
              {status && <div className="pdf-verify-status is-success" role="status">{status}</div>}
              {error && <div className="pdf-verify-status is-error" role="alert">{error}</div>}
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
