import { useEffect, useState } from 'react'
import { bboxStyle, type BboxXywh, type PageImageRef } from '../../adapters/pageImageRefs'
import { useRegisterAction } from '../../hooks/useRegisterAction'

export type LabelAnchor = 'top-outside' | 'top-inside' | 'bottom-inside' | 'bottom-outside'

const LABEL_ANCHORS: readonly LabelAnchor[] = [
  'top-outside',
  'top-inside',
  'bottom-inside',
  'bottom-outside',
]

function nextAnchor(anchor: LabelAnchor): LabelAnchor {
  const index = LABEL_ANCHORS.indexOf(anchor)
  return LABEL_ANCHORS[(index + 1) % LABEL_ANCHORS.length]
}

export interface NormalizedPageOverlayProps {
  pageImage: PageImageRef
  bbox?: BboxXywh
  overlays?: readonly { bbox: BboxXywh; label: string }[]
  label?: string
  labelAnchor?: LabelAnchor
  alt?: string
  imageTestId?: string
  overlayTestId?: string
  actionQualifier?: string
  compact?: boolean
}

export function NormalizedPageOverlay({
  pageImage,
  bbox,
  overlays,
  label,
  labelAnchor = 'top-outside',
  alt,
  imageTestId = 'page-image',
  overlayTestId = 'bbox-overlay',
  actionQualifier,
  compact = false,
}: NormalizedPageOverlayProps) {
  const qualifier = (actionQualifier ?? pageImage.sha256.slice(0, 16))
    .trim()
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'unknown'
  const labelQid = `normalized-page-overlay:label:${qualifier}`
  useRegisterAction(labelQid, {
    app: 'pdf-lab',
    action: 'NORMALIZED_PAGE_OVERLAY_MOVE_LABEL',
    label: 'Move evidence label',
    description: 'Move the evidence label to the next anchored position around its normalized bounds',
  })
  const [anchor, setAnchor] = useState<LabelAnchor>(labelAnchor)
  const [imageError, setImageError] = useState(false)

  useEffect(() => {
    setImageError(false)
  }, [pageImage.href])

  return (
    <figure
      className={`pdf-verify-page ${compact ? 'is-compact' : ''}`}
      data-confidence-hidden="true"
      data-page-sha256={pageImage.sha256}
    >
      <div className="pdf-verify-page__stage">
        {imageError ? (
          <div className="pdf-verify-contract-blocker" data-testid="page-image-error" role="alert">
            <strong>Original page image unavailable</strong>
            <p>The evidence view failed closed because the content-addressed source image could not be loaded.</p>
            <code>{pageImage.filename}</code>
          </div>
        ) : (
          <img
            data-testid={imageTestId}
            src={pageImage.href}
            alt={alt ?? `Original PDF page${pageImage.page == null ? '' : ` ${pageImage.page}`}`}
            draggable={false}
            onError={() => setImageError(true)}
          />
        )}
        {!imageError && (overlays ?? (bbox ? [{ bbox, label: label ?? '' }] : [])).map((overlay, index) => (
            <div
              key={`${overlay.label}-${index}`}
              className="pdf-verify-page__bbox"
              data-testid={index === 0 ? overlayTestId : `${overlayTestId}-${index + 1}`}
              style={bboxStyle(overlay.bbox)}
              aria-label={overlay.label ? `Evidence bounds: ${overlay.label}` : 'Evidence bounds'}
            >
              {overlay.label && (
                <button
                  type="button"
                  className={`pdf-verify-page__tag is-${anchor}`}
                  onClick={() => setAnchor((current) => nextAnchor(current))}
                  title="Move label to the next anchored position"
                  data-qid={labelQid}
                  data-qs-action="NORMALIZED_PAGE_OVERLAY_MOVE_LABEL"
                >
                  {overlay.label}
                </button>
              )}
            </div>
          ))}
      </div>
      <figcaption>
        <span>Original PDF page image</span>
        <code>{pageImage.filename}</code>
      </figcaption>
    </figure>
  )
}
