import { useState } from 'react'
import { bboxStyle, type BboxXywh, type PageImageRef } from '../../adapters/pageImageRefs'

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
  label?: string
  labelAnchor?: LabelAnchor
  alt?: string
  imageTestId?: string
  overlayTestId?: string
  compact?: boolean
}

export function NormalizedPageOverlay({
  pageImage,
  bbox,
  label,
  labelAnchor = 'top-outside',
  alt,
  imageTestId = 'page-image',
  overlayTestId = 'bbox-overlay',
  compact = false,
}: NormalizedPageOverlayProps) {
  const [anchor, setAnchor] = useState<LabelAnchor>(labelAnchor)

  return (
    <figure
      className={`pdf-verify-page ${compact ? 'is-compact' : ''}`}
      data-confidence-hidden="true"
      data-page-sha256={pageImage.sha256}
    >
      <div className="pdf-verify-page__stage">
        <img
          data-testid={imageTestId}
          src={pageImage.href}
          alt={alt ?? `Original PDF page${pageImage.page == null ? '' : ` ${pageImage.page}`}`}
          draggable={false}
        />
        {bbox && (
          <div
            className="pdf-verify-page__bbox"
            data-testid={overlayTestId}
            style={bboxStyle(bbox)}
            aria-label={label ? `Evidence bounds: ${label}` : 'Evidence bounds'}
          >
            {label && (
              <button
                type="button"
                className={`pdf-verify-page__tag is-${anchor}`}
                onClick={() => setAnchor((current) => nextAnchor(current))}
                title="Move label to the next anchored position"
              >
                {label}
              </button>
            )}
          </div>
        )}
      </div>
      <figcaption>
        <span>Original PDF page image</span>
        <code>{pageImage.filename}</code>
      </figcaption>
    </figure>
  )
}
