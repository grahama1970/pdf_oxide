import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from 'react'
import { Check, ChevronLeft, ChevronRight, Download, Loader2, OctagonX, SquareDashed, Tags } from 'lucide-react'
import {
  buildCalibrationLabelRow,
  calibrationItemSha,
  isCalibrationLabelRow,
  parseCalibrationSample,
  serializeLabelRows,
  type CalibrationLabel,
  type CalibrationLabelRow,
  type CalibrationSampleItem,
} from '../../adapters/calibration'
import {
  assertOriginalPageImages,
  lookupPageImageRefs,
  normalizePageImageRefs,
  parsePageImageIndex,
  type PageImageIndex,
  type PageImageRef,
} from '../../adapters/pageImageRefs'
import { NormalizedPageOverlay } from '../verification/NormalizedPageOverlay'
import '../verification/VerificationUx.css'

export interface CalibrateRouteProps {
  sampleUrl?: string
  pageImageIndexUrl?: string
  labelsEndpoint?: string
  initialRows?: readonly CalibrationSampleItem[]
  initialPageImageIndex?: PageImageIndex
  fetchImpl?: typeof fetch
  persistLabel?: (row: CalibrationLabelRow) => Promise<void>
}

const DEFAULT_SAMPLE_URL = '/artifacts/pdf-lab/calibration/sample_v1.jsonl'
const DEFAULT_PAGE_IMAGE_INDEX_URL = '/artifacts/pdf-lab/calibration/page_images_v1.json'
const DEFAULT_LABELS_ENDPOINT = '/api/pdf-lab/calibration/labels'

function downloadLabels(rows: readonly CalibrationLabelRow[]): void {
  const blob = new Blob([serializeLabelRows(rows)], { type: 'application/x-ndjson' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'labels_v1.jsonl'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

function pageImageForItem(
  item: CalibrationSampleItem,
  index: PageImageIndex | null,
): PageImageRef | null {
  const directRefs = item.pageImageRefs == null
    ? []
    : normalizePageImageRefs(item.pageImageRefs, { doc: item.doc, page: item.page })
  const refs = directRefs.length > 0 ? directRefs : [...lookupPageImageRefs(index, item.doc, item.page)]
  try {
    assertOriginalPageImages(refs, `${item.doc} page ${item.page}`)
    return refs[0]
  } catch {
    return null
  }
}

function nextUnlabeledIndex(
  current: number,
  rows: readonly CalibrationSampleItem[],
  itemShas: readonly (string | null)[],
  labels: ReadonlyMap<string, CalibrationLabelRow>,
): number {
  for (let offset = 1; offset <= rows.length; offset += 1) {
    const candidate = (current + offset) % rows.length
    const sha = itemShas[candidate]
    if (!sha || !labels.has(sha)) return candidate
  }
  return current
}

export function CalibrateRoute({
  sampleUrl = DEFAULT_SAMPLE_URL,
  pageImageIndexUrl = DEFAULT_PAGE_IMAGE_INDEX_URL,
  labelsEndpoint = DEFAULT_LABELS_ENDPOINT,
  initialRows,
  initialPageImageIndex,
  fetchImpl = fetch,
  persistLabel,
}: CalibrateRouteProps) {
  const [rows, setRows] = useState<CalibrationSampleItem[]>(initialRows ? [...initialRows] : [])
  const [pageImages, setPageImages] = useState<PageImageIndex | null>(initialPageImageIndex ?? null)
  const [itemShas, setItemShas] = useState<(string | null)[]>([])
  const [labels, setLabels] = useState<Map<string, CalibrationLabelRow>>(new Map())
  const [index, setIndex] = useState(0)
  const [correctedType, setCorrectedType] = useState('')
  const [loading, setLoading] = useState(!initialRows)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)

  useEffect(() => {
    if (initialRows) return
    let cancelled = false
    setLoading(true)
    setError(null)
    void Promise.all([
      fetchImpl(sampleUrl).then(async (response) => {
        if (!response.ok) throw new Error(`sample_v1.jsonl returned HTTP ${response.status}`)
        return parseCalibrationSample(await response.text())
      }),
      fetchImpl(pageImageIndexUrl).then(async (response) => {
        if (!response.ok) throw new Error(`page image index returned HTTP ${response.status}`)
        return parsePageImageIndex(await response.json(), {
          baseUrl: '/artifacts/pdf-lab/calibration',
        })
      }),
      fetchImpl(labelsEndpoint).then(async (response) => {
        if (response.status === 404) return [] as CalibrationLabelRow[]
        if (!response.ok) throw new Error(`labels_v1.jsonl returned HTTP ${response.status}`)
        const payload = await response.json() as { rows?: unknown[] }
        return (payload.rows ?? []).filter(isCalibrationLabelRow)
      }).catch(() => [] as CalibrationLabelRow[]),
    ]).then(([loadedRows, loadedImages, loadedLabels]) => {
      if (cancelled) return
      setRows(loadedRows)
      setPageImages(loadedImages)
      setLabels(new Map(loadedLabels.map((row) => [row.item_sha, row])))
    }).catch((loadError) => {
      if (!cancelled) setError(loadError instanceof Error ? loadError.message : String(loadError))
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [fetchImpl, initialRows, labelsEndpoint, pageImageIndexUrl, sampleUrl])

  useEffect(() => {
    let cancelled = false
    void Promise.all(rows.map((row) => calibrationItemSha(row))).then((shas) => {
      if (!cancelled) setItemShas(shas)
    }).catch((shaError) => {
      if (!cancelled) setError(shaError instanceof Error ? shaError.message : String(shaError))
    })
    return () => { cancelled = true }
  }, [rows])

  useEffect(() => {
    setCorrectedType('')
    setStatus(null)
  }, [index])

  const current = rows[index] ?? null
  const currentSha = itemShas[index] ?? null
  const currentLabel = currentSha ? labels.get(currentSha) ?? null : null
  const currentPageImage = useMemo(
    () => current ? pageImageForItem(current, pageImages) : null,
    [current, pageImages],
  )
  const complete = labels.size
  const remaining = Math.max(0, rows.length - complete)

  const persist = useCallback(async (row: CalibrationLabelRow) => {
    if (persistLabel) {
      await persistLabel(row)
      return
    }
    const response = await fetchImpl(labelsEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(row),
    })
    if (!response.ok) {
      const detail = await response.text()
      throw new Error(`label write failed (${response.status}): ${detail.slice(0, 240)}`)
    }
  }, [fetchImpl, labelsEndpoint, persistLabel])

  const adjudicate = useCallback(async (label: CalibrationLabel) => {
    if (!current || !currentSha || !currentPageImage || saving) return
    setSaving(true)
    setError(null)
    try {
      const row = buildCalibrationLabelRow(
        currentSha,
        label,
        label === 'wrong_type' ? correctedType : undefined,
      )
      await persist(row)
      setLabels((previous) => new Map(previous).set(row.item_sha, row))
      setStatus(`Saved ${label.replaceAll('_', ' ')} for ${current.doc} page ${current.page}`)
      setIndex((currentIndex) => nextUnlabeledIndex(
        currentIndex,
        rows,
        itemShas,
        new Map(labels).set(row.item_sha, row),
      ))
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : String(saveError))
    } finally {
      setSaving(false)
    }
  }, [correctedType, current, currentPageImage, currentSha, itemShas, labels, persist, rows, saving])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return
      if (event.key === 'ArrowLeft') setIndex((value) => Math.max(0, value - 1))
      if (event.key === 'ArrowRight') setIndex((value) => Math.min(rows.length - 1, value + 1))
      if (event.key === '1') void adjudicate('correct')
      if (event.key === '2') void adjudicate('wrong_type')
      if (event.key === '3') void adjudicate('wrong_bounds')
      if (event.key === '4') void adjudicate('not_an_element')
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [adjudicate, rows.length])

  if (loading) {
    return (
      <main className="pdf-verify-route pdf-verify-route--center" data-confidence-hidden="true">
        <Loader2 className="pdf-verify-spin" aria-hidden="true" />
        <p>Loading frozen calibration sample and original page images…</p>
      </main>
    )
  }

  if (error && rows.length === 0) {
    return (
      <main className="pdf-verify-route pdf-verify-route--center" data-confidence-hidden="true">
        <OctagonX aria-hidden="true" />
        <h1>Calibration input failed closed</h1>
        <p>{error}</p>
      </main>
    )
  }

  if (!current) {
    return (
      <main className="pdf-verify-route pdf-verify-route--center" data-confidence-hidden="true">
        <OctagonX aria-hidden="true" />
        <h1>No calibration rows</h1>
        <p>The sample must contain at least one valid sample_v1.jsonl row.</p>
      </main>
    )
  }

  return (
    <main className="pdf-verify-route pdf-verify-calibrate" data-testid="calibrate-route" data-confidence-hidden="true" data-item-sha-ready={currentSha ? "true" : "false"}>
      <header className="pdf-verify-header">
        <div>
          <span className="pdf-verify-kicker">Calibration · blinded review</span>
          <h1>Is this extracted element correct?</h1>
          <p>Confidence is intentionally hidden. Judge only the original page and the proposed bounds/type.</p>
        </div>
        <div className="pdf-verify-progress" aria-label={`${complete} of ${rows.length} adjudicated`}>
          <strong>{complete}/{rows.length}</strong>
          <span>{remaining} remaining</span>
          <div><i style={{ width: `${rows.length ? complete / rows.length * 100 : 0}%` }} /></div>
        </div>
      </header>

      <section className="pdf-verify-workbench">
        <div className="pdf-verify-canvas-panel">
          {!currentPageImage ? (
            <div className="pdf-verify-contract-blocker" data-testid="missing-page-image">
              <OctagonX aria-hidden="true" />
              <h2>Original page image missing</h2>
              <p>Adjudication is disabled because verification without the source page would violate the retrieval contract.</p>
              <code>{current.doc} · page {current.page}</code>
            </div>
          ) : (
            <NormalizedPageOverlay
              pageImage={currentPageImage}
              bbox={current.bbox}
              label={current.type}
              alt={`Original page ${current.page} from ${current.doc}`}
            />
          )}
        </div>

        <aside className="pdf-verify-inspector">
          <div className="pdf-verify-item-meta">
            <span>{current.doc}</span>
            <strong>Page index {current.page}</strong>
            <em>Sample stratum {current.quintile}</em>
          </div>

          <div className="pdf-verify-proposal">
            <span>Proposed element type</span>
            <strong>{current.type}</strong>
            <p>{current.text || '(No extracted text. Judge the visible bounds.)'}</p>
          </div>

          <label className="pdf-verify-field">
            <span>Corrected type (required for wrong type)</span>
            <input
              value={correctedType}
              onChange={(event: ChangeEvent<HTMLInputElement>) => setCorrectedType(event.target.value)}
              placeholder="e.g. figure, table, caption"
              autoComplete="off"
            />
          </label>

          <div className="pdf-verify-decision-grid">
            <button type="button" onClick={() => void adjudicate('correct')} disabled={!currentPageImage || saving}>
              <Check aria-hidden="true" />
              <span><kbd>1</kbd> Correct</span>
            </button>
            <button type="button" onClick={() => void adjudicate('wrong_type')} disabled={!currentPageImage || saving || !correctedType.trim()}>
              <Tags aria-hidden="true" />
              <span><kbd>2</kbd> Wrong type</span>
            </button>
            <button type="button" onClick={() => void adjudicate('wrong_bounds')} disabled={!currentPageImage || saving}>
              <SquareDashed aria-hidden="true" />
              <span><kbd>3</kbd> Wrong bounds</span>
            </button>
            <button type="button" onClick={() => void adjudicate('not_an_element')} disabled={!currentPageImage || saving}>
              <OctagonX aria-hidden="true" />
              <span><kbd>4</kbd> Not an element</span>
            </button>
          </div>

          {saving && <div className="pdf-verify-status"><Loader2 className="pdf-verify-spin" /> Writing labels_v1.jsonl…</div>}
          {status && <div className="pdf-verify-status is-success" role="status">{status}</div>}
          {error && <div className="pdf-verify-status is-error" role="alert">{error}</div>}
          {currentLabel && (
            <div className="pdf-verify-existing-label">
              Previously labeled <strong>{currentLabel.label.replaceAll('_', ' ')}</strong>
              {currentLabel.corrected_type ? ` → ${currentLabel.corrected_type}` : ''}
            </div>
          )}

          <footer className="pdf-verify-inspector__footer">
            <div className="pdf-verify-pager">
              <button type="button" onClick={() => setIndex((value) => Math.max(0, value - 1))} disabled={index === 0} aria-label="Previous calibration item">
                <ChevronLeft />
              </button>
              <span>{index + 1} / {rows.length}</span>
              <button type="button" onClick={() => setIndex((value) => Math.min(rows.length - 1, value + 1))} disabled={index >= rows.length - 1} aria-label="Next calibration item">
                <ChevronRight />
              </button>
            </div>
            <button type="button" className="pdf-verify-secondary" onClick={() => downloadLabels([...labels.values()])} disabled={labels.size === 0}>
              <Download /> Export labels_v1.jsonl
            </button>
          </footer>
        </aside>
      </section>
    </main>
  )
}
