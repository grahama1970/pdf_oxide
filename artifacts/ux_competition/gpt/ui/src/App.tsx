import React, { useEffect, useMemo, useState } from 'react'
import { AnnotationQueueRoute } from './components/annotation/AnnotationQueueRoute'
import { CalibrateRoute } from './components/calibration/CalibrateRoute'
import { RetrievalEvidenceRoute } from './components/retrieval/RetrievalEvidenceView'
import './components/verification/VerificationUx.css'

const PdfLabView = React.lazy(() =>
  import('./components/pdf-lab/PdfLabView').then((module) => ({ default: module.PdfLabView })),
)

type VerificationRoute = 'calibrate' | 'annotations' | 'evidence'

interface HashLocation {
  route: string
  subpath: string | undefined
  params: URLSearchParams
}

function readHashLocation(): HashLocation {
  const hash = window.location.hash.replace(/^#/, '')
  const [rawRoute = 'pdf-lab', rawQuery = ''] = hash.split('?', 2)
  const route = rawRoute || 'pdf-lab'
  const subpath = route.startsWith('pdf-lab/') ? route.slice('pdf-lab/'.length) || undefined : undefined
  return {
    route,
    subpath,
    params: new URLSearchParams(rawQuery || window.location.search),
  }
}

function useHashLocation(): HashLocation {
  const [location, setLocation] = useState<HashLocation>(() => readHashLocation())
  useEffect(() => {
    const update = () => setLocation(readHashLocation())
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  return location
}

function VerificationNav({ active }: { active?: VerificationRoute }) {
  const links: Array<{ route: VerificationRoute; label: string }> = [
    { route: 'annotations', label: 'Annotation queue' },
    { route: 'calibrate', label: 'Calibrate' },
    { route: 'evidence', label: 'Retrieval evidence' },
  ]
  return (
    <nav className="pdf-verify-mode-nav" aria-label="PDF Lab verification modes">
      <a href="#pdf-lab/loop">Loop viewer</a>
      {links.map((link) => (
        <a key={link.route} className={active === link.route ? 'is-active' : ''} href={`#pdf-lab/${link.route}`}>
          {link.label}
        </a>
      ))}
    </nav>
  )
}

function MissingRouteInput({ title, detail, example }: { title: string; detail: string; example: string }) {
  return (
    <main className="pdf-verify-route pdf-verify-route--center" data-confidence-hidden="true">
      <h1>{title}</h1>
      <p>{detail}</p>
      <code>{example}</code>
    </main>
  )
}

export default function App() {
  const location = useHashLocation()
  const route = location.subpath as VerificationRoute | undefined
  const pdfUrl = location.params.get('pdf') || undefined
  const extractionUrl = location.params.get('extraction') || undefined

  const verificationView = useMemo(() => {
    switch (route) {
      case 'calibrate':
        return (
          <CalibrateRoute
            sampleUrl={location.params.get('sample') || undefined}
            pageImageIndexUrl={location.params.get('pageImages') || undefined}
            labelsEndpoint={location.params.get('labelsEndpoint') || undefined}
          />
        )
      case 'annotations':
        return (
          <AnnotationQueueRoute
            callsUrl={location.params.get('calls') || undefined}
            pageImageIndexUrl={location.params.get('pageImages') || undefined}
          />
        )
      case 'evidence': {
        const resultUrl = location.params.get('result')
        if (!resultUrl) {
          return (
            <MissingRouteInput
              title="Retrieval result URL required"
              detail="The evidence view fails closed until an answer artifact is supplied."
              example="#pdf-lab/evidence?result=/artifacts/pdf-lab/retrieval_result.json&pageImages=/artifacts/pdf-lab/page_images/index.json&tree=/artifacts/pdf-lab/section_tree_v2.json"
            />
          )
        }
        return (
          <RetrievalEvidenceRoute
            resultUrl={resultUrl}
            pageImageIndexUrl={location.params.get('pageImages') || undefined}
            sectionTreeUrl={location.params.get('tree') || undefined}
          />
        )
      }
      default:
        return null
    }
  }, [location.params, route])

  if (verificationView) {
    return (
      <div className="pdf-lab-app-shell">
        <VerificationNav active={route} />
        {verificationView}
      </div>
    )
  }

  return (
    <div className="pdf-lab-app-shell">
      <VerificationNav />
      <React.Suspense fallback={<div className="pdf-lab-loading">Loading PDF Lab…</div>}>
        <PdfLabView
          initialSubpath={location.subpath}
          pdfUrl={pdfUrl}
          extractionUrl={extractionUrl}
        />
      </React.Suspense>
    </div>
  )
}
