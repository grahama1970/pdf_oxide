import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react'
import type { PageImageRef } from '../../adapters/pageImageRefs'
import './PdfDocumentCanvas.css'

export type CanvasPoint = [number, number]
export type CanvasBbox = [number, number, number, number]
export type LabelAnchor = 'top-outside' | 'top-inside' | 'bottom-inside' | 'bottom-outside'

export interface CanvasRegion {
  id: string
  bbox: CanvasBbox
  label: string
  color?: string
  labelAnchor?: LabelAnchor
  editable?: boolean
  /** Ghost-layer context region: dashed, muted, tooltip-only (operator spec 6). */
  ghost?: boolean
}

export interface CanvasThumbnail {
  id: string
  pageImage: PageImageRef
  label?: string
}

export interface PdfDocumentCanvasProps {
  pageImage: PageImageRef
  regions?: readonly CanvasRegion[]
  selectedRegionId?: string | null
  onSelectedRegionChange?: (id: string | null) => void
  onRegionsChange?: (regions: CanvasRegion[]) => void
  drawLabel?: string
  drawColor?: string
  allowDraw?: boolean
  thumbnails?: readonly CanvasThumbnail[]
  selectedThumbnailId?: string
  onThumbnailSelect?: (thumbnail: CanvasThumbnail) => void
  alt?: string
  compact?: boolean
  actionQualifier?: string
}

interface DragState {
  startX: number
  startY: number
  curX: number
  curY: number
}

type RegionDragMode = 'move' | 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw'

interface RegionDragState {
  regionId: string
  mode: RegionDragMode
  startClientX: number
  startClientY: number
  startBbox: CanvasBbox
}

const LABEL_ANCHOR_CYCLE: LabelAnchor[] = [
  'top-outside', 'top-inside', 'bottom-inside', 'bottom-outside',
]

const ZOOM_MIN = 0.25
const ZOOM_MAX = 8

function nextAnchor(anchor: LabelAnchor | undefined): LabelAnchor {
  const index = LABEL_ANCHOR_CYCLE.indexOf(anchor ?? 'top-outside')
  return LABEL_ANCHOR_CYCLE[(index + 1) % LABEL_ANCHOR_CYCLE.length]
}

// Lifted verbatim from PdfLabLabelingPage: normalized canvas geometry is the
// shared contract, independent of image resolution and current zoom.
function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value))
}

function normalizeRect(x0: number, y0: number, x1: number, y1: number): CanvasBbox {
  const a = Math.min(x0, x1), b = Math.max(x0, x1)
  const c = Math.min(y0, y1), d = Math.max(y0, y1)
  return [clamp01(a), clamp01(c), clamp01(b), clamp01(d)]
}

function qualifier(value: string): string {
  return value.trim().replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'canvas'
}

function canvasActionAttributes(actionQualifier: string, action: string, label: string) {
  const normalized = qualifier(actionQualifier)
  return {
    'aria-label': label,
    'data-qid': `pdf-document-canvas:${normalized}:${action}`,
    'data-qs-action': `PDF_DOCUMENT_CANVAS_${action.replaceAll('-', '_').toUpperCase()}`,
    title: label,
  }
}

export function PdfDocumentCanvas({
  pageImage,
  regions = [],
  selectedRegionId = null,
  onSelectedRegionChange,
  onRegionsChange,
  drawLabel = 'region',
  drawColor = '#fbbf24',
  allowDraw = false,
  thumbnails,
  selectedThumbnailId,
  onThumbnailSelect,
  alt,
  compact = false,
  actionQualifier = 'page',
}: PdfDocumentCanvasProps) {
  const [imageNaturalSize, setImageNaturalSize] = useState<{ w: number; h: number } | null>(
    pageImage.width && pageImage.height ? { w: pageImage.width, h: pageImage.height } : null,
  )
  const [zoom, setZoom] = useState(1)
  const [fitMode, setFitMode] = useState<'page' | 'width'>('page')
  const [panMode, setPanMode] = useState(false)
  const [labelAnchors, setLabelAnchors] = useState<Record<string, LabelAnchor>>({})
  const [drag, setDrag] = useState<DragState | null>(null)
  const [regionDrag, setRegionDrag] = useState<RegionDragState | null>(null)
  const [panDrag, setPanDrag] = useState<{
    startClientX: number
    startClientY: number
    startScrollLeft: number
    startScrollTop: number
  } | null>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const imgWrapperRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setImageNaturalSize(pageImage.width && pageImage.height
      ? { w: pageImage.width, h: pageImage.height }
      : null)
    setLabelAnchors({})
  }, [pageImage.height, pageImage.href, pageImage.width])

  const fitCanvas = useCallback((mode: 'page' | 'width' = fitMode) => {
    if (!canvasRef.current || !imageNaturalSize) return
    const rect = canvasRef.current.getBoundingClientRect()
    const padding = 32
    const thumbnailWidth = (thumbnails?.length ?? 0) > 0 ? 86 : 0
    const widthRatio = (rect.width - padding - thumbnailWidth) / imageNaturalSize.w
    const heightRatio = (rect.height - padding) / imageNaturalSize.h
    const nextZoom = mode === 'page' ? Math.min(widthRatio, heightRatio) : widthRatio
    setZoom(Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, nextZoom)))
  }, [fitMode, imageNaturalSize, thumbnails?.length])

  useEffect(() => {
    const animationFrame = window.requestAnimationFrame(() => fitCanvas(fitMode))
    return () => window.cancelAnimationFrame(animationFrame)
  }, [fitCanvas, fitMode, pageImage.href])

  const zoomIn = useCallback(() => setZoom((current) => Math.min(ZOOM_MAX, current * 1.25)), [])
  const zoomOut = useCallback(() => setZoom((current) => Math.max(ZOOM_MIN, current / 1.25)), [])

  /** Convert a client-pixel point to normalized image coords. Uses the
   * scaled image wrapper rect so the mapping stays correct at any zoom. */
  const clientToNormalized = useCallback((clientX: number, clientY: number): CanvasPoint | null => {
    const element = imgWrapperRef.current
    if (!element) return null
    const rect = element.getBoundingClientRect()
    if (rect.width <= 0 || rect.height <= 0) return null
    return [clamp01((clientX - rect.left) / rect.width), clamp01((clientY - rect.top) / rect.height)]
  }, [])

  const replaceRegion = useCallback((regionId: string, bbox: CanvasBbox) => {
    onRegionsChange?.(regions.map((region) => region.id === regionId ? { ...region, bbox } : region))
  }, [onRegionsChange, regions])

  const handleMouseDown = useCallback((event: ReactMouseEvent) => {
    if (panMode) {
      const canvas = canvasRef.current
      if (!canvas) return
      event.preventDefault()
      setPanDrag({
        startClientX: event.clientX,
        startClientY: event.clientY,
        startScrollLeft: canvas.scrollLeft,
        startScrollTop: canvas.scrollTop,
      })
      return
    }
    if (!allowDraw) {
      onSelectedRegionChange?.(null)
      return
    }
    const point = clientToNormalized(event.clientX, event.clientY)
    if (!point) return
    onSelectedRegionChange?.(null)
    setDrag({ startX: point[0], startY: point[1], curX: point[0], curY: point[1] })
  }, [allowDraw, clientToNormalized, onSelectedRegionChange, panMode])

  const handleMouseMove = useCallback((event: ReactMouseEvent) => {
    if (panDrag) {
      const canvas = canvasRef.current
      if (!canvas) return
      canvas.scrollLeft = panDrag.startScrollLeft - (event.clientX - panDrag.startClientX)
      canvas.scrollTop = panDrag.startScrollTop - (event.clientY - panDrag.startClientY)
      return
    }
    if (regionDrag && imageNaturalSize) {
      // Lifted from PdfLabLabelingPage. Pixel delta is divided by the scaled
      // natural image size so move/resize behaves identically at every zoom.
      const dxN = (event.clientX - regionDrag.startClientX) / (imageNaturalSize.w * zoom)
      const dyN = (event.clientY - regionDrag.startClientY) / (imageNaturalSize.h * zoom)
      const [sx0, sy0, sx1, sy1] = regionDrag.startBbox
      let nx0 = sx0, ny0 = sy0, nx1 = sx1, ny1 = sy1
      switch (regionDrag.mode) {
        case 'move': {
          let tx = dxN, ty = dyN
          if (sx0 + tx < 0) tx = -sx0
          if (sy0 + ty < 0) ty = -sy0
          if (sx1 + tx > 1) tx = 1 - sx1
          if (sy1 + ty > 1) ty = 1 - sy1
          nx0 = sx0 + tx; nx1 = sx1 + tx
          ny0 = sy0 + ty; ny1 = sy1 + ty
          break
        }
        case 'n': ny0 = clamp01(sy0 + dyN); break
        case 's': ny1 = clamp01(sy1 + dyN); break
        case 'e': nx1 = clamp01(sx1 + dxN); break
        case 'w': nx0 = clamp01(sx0 + dxN); break
        case 'nw': nx0 = clamp01(sx0 + dxN); ny0 = clamp01(sy0 + dyN); break
        case 'ne': nx1 = clamp01(sx1 + dxN); ny0 = clamp01(sy0 + dyN); break
        case 'sw': nx0 = clamp01(sx0 + dxN); ny1 = clamp01(sy1 + dyN); break
        case 'se': nx1 = clamp01(sx1 + dxN); ny1 = clamp01(sy1 + dyN); break
      }
      const bbox: CanvasBbox = [
        Math.min(nx0, nx1), Math.min(ny0, ny1),
        Math.max(nx0, nx1), Math.max(ny0, ny1),
      ]
      replaceRegion(regionDrag.regionId, bbox)
      return
    }
    if (!drag) return
    const point = clientToNormalized(event.clientX, event.clientY)
    if (!point) return
    setDrag({ ...drag, curX: point[0], curY: point[1] })
  }, [clientToNormalized, drag, imageNaturalSize, panDrag, regionDrag, replaceRegion, zoom])

  const handleMouseUp = useCallback(() => {
    if (panDrag) {
      setPanDrag(null)
      return
    }
    if (regionDrag) {
      setRegionDrag(null)
      return
    }
    if (!drag) return
    const bbox = normalizeRect(drag.startX, drag.startY, drag.curX, drag.curY)
    const width = bbox[2] - bbox[0], height = bbox[3] - bbox[1]
    setDrag(null)
    if (width < 0.005 || height < 0.005) return
    const id = `canvas_${Date.now().toString(36)}`
    const nextRegion: CanvasRegion = { id, bbox, label: drawLabel, color: drawColor, editable: true }
    onRegionsChange?.([...regions, nextRegion])
    onSelectedRegionChange?.(id)
  }, [drag, drawColor, drawLabel, onRegionsChange, onSelectedRegionChange, panDrag, regionDrag, regions])

  const handleRegionMouseDown = useCallback((
    event: ReactMouseEvent,
    regionId: string,
    mode: RegionDragMode = 'move',
  ) => {
    if (panMode) return
    event.stopPropagation()
    event.preventDefault()
    const region = regions.find((candidate) => candidate.id === regionId)
    if (!region || region.editable === false) return
    onSelectedRegionChange?.(regionId)
    setRegionDrag({
      regionId,
      mode,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startBbox: [...region.bbox] as CanvasBbox,
    })
  }, [onSelectedRegionChange, panMode, regions])

  const handleWheel = useCallback((event: ReactWheelEvent) => {
    if (!(event.ctrlKey || event.metaKey)) return
    event.preventDefault()
    setZoom((current) => {
      const factor = event.deltaY < 0 ? 1.1 : 1 / 1.1
      return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, current * factor))
    })
  }, [])

  const previewRect = useMemo(
    () => drag ? normalizeRect(drag.startX, drag.startY, drag.curX, drag.curY) : null,
    [drag],
  )
  const visibleThumbnails = thumbnails?.length
    ? thumbnails
    : [{ id: String(pageImage.page ?? pageImage.sha256), pageImage, label: `Page ${pageImage.page ?? ''}` }]

  return (
    <figure
      className={`pdf-document-canvas ${compact ? 'is-compact' : ''}`}
      data-testid="pdf-document-canvas"
      data-zoom={zoom.toFixed(4)}
      data-fit-mode={fitMode}
      data-pan-mode={panMode ? 'true' : 'false'}
      data-page-sha256={pageImage.sha256}
    >
      <div className="pdf-document-canvas__toolbar" role="toolbar" aria-label="Document canvas controls">
        <button
          type="button"
          onClick={zoomOut}
          {...canvasActionAttributes(actionQualifier, 'zoom-out', 'Zoom out')}
        >
          −
        </button>
        <button
          type="button"
          onClick={zoomIn}
          {...canvasActionAttributes(actionQualifier, 'zoom-in', 'Zoom in')}
        >
          +
        </button>
        <button
          type="button"
          onClick={() => {
            const next = fitMode === 'page' ? 'width' : 'page'
            setFitMode(next)
            fitCanvas(next)
          }}
          {...canvasActionAttributes(
            actionQualifier,
            'full-page-toggle',
            fitMode === 'page' ? 'Fit page width' : 'Show full page',
          )}
        >
          {fitMode === 'page' ? 'Fit width' : 'Full page'}
        </button>
        <button
          type="button"
          className={panMode ? 'is-active' : ''}
          aria-pressed={panMode}
          onClick={() => setPanMode((current) => !current)}
          {...canvasActionAttributes(actionQualifier, 'pan', 'Toggle canvas pan tool')}
        >
          Pan
        </button>
        <output aria-label="Canvas zoom">{Math.round(zoom * 100)}%</output>
      </div>

      <div className="pdf-document-canvas__body">
        <nav className="pdf-document-canvas__thumbnails" aria-label="Page thumbnails">
          {visibleThumbnails.map((thumbnail) => (
            <button
              type="button"
              key={thumbnail.id}
              className={(selectedThumbnailId ?? visibleThumbnails[0]?.id) === thumbnail.id ? 'is-selected' : ''}
              onClick={() => onThumbnailSelect?.(thumbnail)}
              {...canvasActionAttributes(
                actionQualifier,
                `thumbnail-${thumbnail.id}`,
                `Open ${thumbnail.label ?? `page ${thumbnail.pageImage.page ?? ''}`}`,
              )}
            >
              <img src={thumbnail.pageImage.href} alt="" draggable={false} />
              <span>{thumbnail.label ?? `p${thumbnail.pageImage.page ?? ''}`}</span>
            </button>
          ))}
        </nav>

        <div
          ref={canvasRef}
          className={`pdf-document-canvas__viewport ${panMode ? 'is-panning' : ''}`}
          data-testid="pdf-document-canvas-viewport"
          onWheel={handleWheel}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          <div
            ref={imgWrapperRef}
            className="pdf-document-canvas__page"
            style={imageNaturalSize ? {
              width: `${imageNaturalSize.w * zoom}px`,
              height: `${imageNaturalSize.h * zoom}px`,
            } : undefined}
            onMouseDown={handleMouseDown}
          >
            <img
              src={pageImage.href}
              alt={alt ?? `Original PDF page ${pageImage.page ?? ''}`}
              draggable={false}
              data-testid="page-image"
              onLoad={(event) => {
                const image = event.currentTarget
                setImageNaturalSize({ w: image.naturalWidth, h: image.naturalHeight })
              }}
            />
            {regions.map((region, regionIndex) => {
              const selected = selectedRegionId === region.id
              const editable = region.editable !== false && !region.ghost
              const labelAnchor = labelAnchors[region.id] ?? region.labelAnchor ?? 'top-outside'
              return (
                <div
                  key={region.id}
                  className={`pdf-document-canvas__region ${selected ? 'is-selected' : ''} ${region.ghost ? 'is-ghost' : ''}`}
                  data-testid={regionIndex === 0 ? 'bbox-overlay' : `bbox-overlay-${regionIndex + 1}`}
                  data-region-id={region.id}
                  role={editable ? 'button' : undefined}
                  tabIndex={editable ? 0 : undefined}
                  aria-label={`Bounds for ${region.label}`}
                  data-qid={`pdf-document-canvas:${qualifier(actionQualifier)}:region-${qualifier(region.id)}`}
                  data-qs-action={editable ? 'PDF_DOCUMENT_CANVAS_MOVE_REGION' : 'PDF_DOCUMENT_CANVAS_VIEW_REGION'}
                  title={editable ? `Drag to move ${region.label} bounds` : `${region.label} bounds`}
                  onMouseDown={editable ? (event) => handleRegionMouseDown(event, region.id, 'move') : undefined}
                  style={{
                    left: `${region.bbox[0] * 100}%`,
                    top: `${region.bbox[1] * 100}%`,
                    width: `${(region.bbox[2] - region.bbox[0]) * 100}%`,
                    height: `${(region.bbox[3] - region.bbox[1]) * 100}%`,
                    borderColor: region.color ?? '#fbbf24',
                  }}
                >
                  <button
                    type="button"
                    className={`pdf-document-canvas__tag is-${labelAnchor}`}
                    style={{ ['--canvas-region-color' as string]: region.color ?? '#fbbf24' }}
                    onMouseDown={(event) => {
                      event.stopPropagation()
                      event.preventDefault()
                    }}
                    onClick={() => {
                      const anchor = nextAnchor(labelAnchor)
                      setLabelAnchors((current) => ({ ...current, [region.id]: anchor }))
                      onRegionsChange?.(regions.map((candidate) => (
                        candidate.id === region.id
                          ? { ...candidate, labelAnchor: anchor }
                          : candidate
                      )))
                    }}
                    {...canvasActionAttributes(
                      actionQualifier,
                      `label-${region.id}`,
                      `Move ${region.label} label to the next anchored position`,
                    )}
                  >
                    {region.label}
                  </button>
                  {selected && editable && (['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const).map((direction) => (
                    <button
                      type="button"
                      key={direction}
                      className={`pdf-document-canvas__handle h-${direction}`}
                      onMouseDown={(event) => handleRegionMouseDown(event, region.id, direction)}
                      {...canvasActionAttributes(
                        actionQualifier,
                        `resize-${region.id}-${direction}`,
                        `Resize ${region.label} bounds from ${direction}`,
                      )}
                    />
                  ))}
                </div>
              )
            })}
            {previewRect && (
              <div
                className="pdf-document-canvas__region is-preview"
                data-testid="bbox-draw-preview"
                style={{
                  left: `${previewRect[0] * 100}%`,
                  top: `${previewRect[1] * 100}%`,
                  width: `${(previewRect[2] - previewRect[0]) * 100}%`,
                  height: `${(previewRect[3] - previewRect[1]) * 100}%`,
                  borderColor: drawColor,
                }}
              />
            )}
          </div>
        </div>
      </div>
      <figcaption>
        <span>Original page {pageImage.page ?? ''}</span>
        <code title={pageImage.sha256}>{pageImage.filename}</code>
      </figcaption>
    </figure>
  )
}
