import React, { useMemo } from 'react'

const PdfLabLabelingPage = React.lazy(() =>
  import('./components/pdf-lab/PdfLabLabelingPage').then((module) => ({ default: module.PdfLabLabelingPage })),
)
const TauLoopView = React.lazy(() =>
  import('./components/pdf-lab/TauLoopView').then((module) => ({ default: module.TauLoopView })),
)
const PdfLabEvidenceQA = React.lazy(() =>
  import('./components/pdf-lab/PdfLabEvidenceQA').then((module) => ({ default: module.PdfLabEvidenceQA })),
)

function resolvePdfLabSubpath(): string | undefined {
  const hash = window.location.hash.replace(/^#/, '')
  const [route] = hash.split('?')
  if (!route || route === 'pdf-lab') return undefined
  if (!route.startsWith('pdf-lab/')) return undefined
  return route.slice('pdf-lab/'.length) || undefined
}

export default function App() {
  const initialSubpath = useMemo(() => resolvePdfLabSubpath(), [])
  const content = initialSubpath === 'loop' || initialSubpath === 'tau-loop'
    ? <TauLoopView />
    : initialSubpath === 'evidence-qa' || initialSubpath === 'nico-qa'
      ? <PdfLabEvidenceQA />
      : <PdfLabLabelingPage mode={initialSubpath === 'calibrate' ? 'calibrate' : 'label'} />

  return (
    <div className="pdf-lab-app-shell">
      <React.Suspense fallback={<div className="pdf-lab-loading">Loading PDF Lab...</div>}>
        {content}
      </React.Suspense>
    </div>
  )
}
