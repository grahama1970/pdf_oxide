import React, { useEffect, useState } from 'react'
import { AnnotationQueueRoute } from './components/annotation/AnnotationQueueRoute'
import { CalibrateRoute } from './components/calibration/CalibrateRoute'
import { RetrievalEvidenceRoute } from './components/retrieval/RetrievalEvidenceView'
import {
  parsePdfLabMounts,
  type PdfLabMounts,
  type RetrievalResultMount,
} from './adapters/mounts'
import { useRegisterAction } from './hooks/useRegisterAction'
import './components/verification/VerificationUx.css'

const PdfLabView = React.lazy(() =>
  import('./components/pdf-lab/PdfLabView').then((module) => ({ default: module.PdfLabView })),
)

type VerificationRoute = 'calibrate' | 'annotations' | 'evidence'

interface VerificationNavLinkProps {
  active: boolean
  action: string
  description: string
  label: string
  route: string
}

interface HashLocation {
  route: string
  subpath: string | undefined
  params: URLSearchParams
}

function readHashLocation(): HashLocation {
  const hash = window.location.hash.replace(/^#/, '')
  const [rawRoute = '', rawQuery = ''] = hash.split('?', 2)
  const route = rawRoute || 'pdf-lab/annotations'
  let subpath: string | undefined = 'annotations'
  if (route === 'pdf-lab') {
    subpath = 'annotations'
  } else if (route.startsWith('pdf-lab/legacy')) {
    subpath = route.slice('pdf-lab/'.length) || 'legacy'
  } else if (route.startsWith('pdf-lab/')) {
    const requested = route.slice('pdf-lab/'.length)
    subpath = ['annotations', 'calibrate', 'evidence'].includes(requested) ? requested : 'annotations'
  }
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

function VerificationNavLink({
  active,
  action,
  description,
  label,
  route,
}: VerificationNavLinkProps) {
  const qid = `verification-nav:tab:${route}`
  useRegisterAction(qid, {
    app: 'pdf-lab',
    action,
    label,
    description,
  })

  return (
    <a
      className={active ? 'is-active' : ''}
      href={`#pdf-lab/${route}`}
      aria-current={active ? 'page' : undefined}
      data-qid={qid}
      data-qs-action={action}
      title={`Open ${label}`}
    >
      {label}
    </a>
  )
}

function VerificationNav({ active }: { active?: VerificationRoute }) {
  const links: Array<{
    route: VerificationRoute
    label: string
    action: string
    description: string
  }> = [
    {
      route: 'annotations',
      label: 'Annotation queue',
      action: 'VERIFICATION_NAV_OPEN_ANNOTATIONS',
      description: 'Open the PDF Lab extraction uncertainty annotation queue',
    },
    {
      route: 'calibrate',
      label: 'Calibrate',
      action: 'VERIFICATION_NAV_OPEN_CALIBRATE',
      description: 'Open the PDF Lab blinded calibration workflow',
    },
    {
      route: 'evidence',
      label: 'Retrieval evidence',
      action: 'VERIFICATION_NAV_OPEN_EVIDENCE',
      description: 'Open the PDF Lab traceable retrieval evidence view',
    },
  ]
  return (
    <nav className="pdf-verify-mode-nav" aria-label="PDF Lab verification modes">
      {links.map((link) => (
        <VerificationNavLink
          key={link.route}
          active={active === link.route}
          route={link.route}
          label={link.label}
          action={link.action}
          description={link.description}
        />
      ))}
    </nav>
  )
}

function GuidedMountState({
  title,
  detail,
  artifactsRoot,
  testId,
}: {
  title: string
  detail: string
  artifactsRoot: string
  testId: string
}) {
  return (
    <main
      className="pdf-verify-route pdf-verify-route--center"
      data-confidence-hidden="true"
      data-testid={testId}
    >
      <h1>{title}</h1>
      <p>{detail}</p>
      <p>The server looked under <code>{artifactsRoot}</code>.</p>
    </main>
  )
}

function siblingPageImageIndex(sampleUrl: string): string {
  const clean = sampleUrl.split(/[?#]/, 1)[0]
  return `${clean.slice(0, clean.lastIndexOf('/') + 1)}page_images_v1.json`
}

function EvidenceMountedRoute({
  options,
  pageImageIndexOverride,
  sectionTreeUrl,
  artifactsRoot,
}: {
  options: readonly RetrievalResultMount[]
  pageImageIndexOverride?: string
  sectionTreeUrl?: string
  artifactsRoot: string
}) {
  const [selectedUrl, setSelectedUrl] = useState(options[0]?.url ?? '')
  useEffect(() => {
    if (!options.some((option) => option.url === selectedUrl)) setSelectedUrl(options[0]?.url ?? '')
  }, [options, selectedUrl])
  const selected = options.find((option) => option.url === selectedUrl) ?? options[0]
  if (!selected) {
    return (
      <GuidedMountState
        title="No retrieval result is mounted"
        detail="Add a file ending in retrieval_result.json beneath the artifact root, then reload this route."
        artifactsRoot={artifactsRoot}
        testId="evidence-guided-empty"
      />
    )
  }
  return (
    <>
      {options.length > 1 && (
        <label className="pdf-verify-artifact-picker">
          <span>Retrieval result</span>
          <select
            aria-label="Choose retrieval result"
            value={selected.url}
            onChange={(event) => setSelectedUrl(event.target.value)}
          >
            {options.map((option) => (
              <option key={option.url} value={option.url}>{option.label}</option>
            ))}
          </select>
        </label>
      )}
      <RetrievalEvidenceRoute
        resultUrl={selected.url}
        pageImageIndexUrl={pageImageIndexOverride ?? selected.page_image_index_url}
        sectionTreeUrl={sectionTreeUrl}
        artifactsRoot={artifactsRoot}
      />
    </>
  )
}

export default function App() {
  const location = useHashLocation()
  const route = location.subpath as VerificationRoute | `legacy${string}` | undefined
  const pdfUrl = location.params.get('pdf') || undefined
  const extractionUrl = location.params.get('extraction') || undefined
  const [mounts, setMounts] = useState<PdfLabMounts | null>(null)
  const [mountsFailed, setMountsFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    void fetch('/api/pdf-lab/mounts')
      .then(async (response) => {
        if (!response.ok) throw new Error('mount discovery unavailable')
        return parsePdfLabMounts(await response.json())
      })
      .then((value) => {
        if (!cancelled) setMounts(value)
      })
      .catch(() => {
        if (!cancelled) setMountsFailed(true)
      })
    return () => { cancelled = true }
  }, [])

  if (route?.startsWith('legacy')) {
    const legacySubpath = route.replace(/^legacy\/?/, '') || undefined
    return (
      <div className="pdf-lab-app-shell">
        <React.Suspense fallback={<div className="pdf-lab-loading">Loading PDF Lab legacy view…</div>}>
          <PdfLabView
            initialSubpath={legacySubpath}
            pdfUrl={pdfUrl}
            extractionUrl={extractionUrl}
          />
        </React.Suspense>
      </div>
    )
  }

  const verificationRoute: VerificationRoute = route === 'calibrate' || route === 'evidence'
    ? route
    : 'annotations'
  const artifactsRoot = mounts?.artifacts_root ?? '(the configured PDF Lab artifact root)'
  const callsOverride = location.params.get('calls') || undefined
  const pageImagesOverride = location.params.get('pageImages') || undefined
  const sampleOverride = location.params.get('sample') || undefined
  const resultOverride = location.params.get('result') || undefined
  const requiresMounts = (
    (verificationRoute === 'annotations' && !callsOverride)
    || (verificationRoute === 'calibrate' && !sampleOverride)
    || (verificationRoute === 'evidence' && !resultOverride)
  )

  let verificationView: React.ReactNode
  if (requiresMounts && !mounts && !mountsFailed) {
    verificationView = <div className="pdf-lab-loading">Discovering PDF Lab artifacts…</div>
  } else if (mountsFailed && requiresMounts) {
    verificationView = (
      <GuidedMountState
        title="Artifact discovery is unavailable"
        detail="Start the PDF Lab server on port 3013 and confirm GET /api/pdf-lab/mounts is reachable."
        artifactsRoot={artifactsRoot}
        testId="mounts-guided-empty"
      />
    )
  } else if (verificationRoute === 'calibrate') {
    const sample = mounts?.calibration_samples[0]
    const sampleUrl = sampleOverride ?? sample?.url
    const pageImageIndexUrl = pageImagesOverride
      ?? (sampleOverride ? siblingPageImageIndex(sampleOverride) : sample?.page_image_index_url)
    verificationView = sampleUrl && pageImageIndexUrl ? (
      <CalibrateRoute
        sampleUrl={sampleUrl}
        pageImageIndexUrl={pageImageIndexUrl}
        labelsEndpoint={location.params.get('labelsEndpoint') || sample?.labels_endpoint || undefined}
        artifactsRoot={artifactsRoot}
      />
    ) : (
      <GuidedMountState
        title="No complete calibration mount was found"
        detail="Add calibration/sample_v1.jsonl beside calibration/page_images_v1.json, then reload this route."
        artifactsRoot={artifactsRoot}
        testId="calibration-guided-empty"
      />
    )
  } else if (verificationRoute === 'evidence') {
    const options: RetrievalResultMount[] = resultOverride
      ? [{
          url: resultOverride,
          label: resultOverride.split('/').pop() || 'Retrieval result',
          ...(pageImagesOverride ? { page_image_index_url: pageImagesOverride } : {}),
        }]
      : mounts?.retrieval_results ?? []
    verificationView = (
      <EvidenceMountedRoute
        options={options}
        pageImageIndexOverride={pageImagesOverride}
        sectionTreeUrl={location.params.get('tree') || undefined}
        artifactsRoot={artifactsRoot}
      />
    )
  } else {
    verificationView = (
      <AnnotationQueueRoute
        callsUrl={callsOverride ?? mounts?.annotation_calls.map((entry) => entry.url).join(',') ?? ''}
        pageImageIndexUrl={pageImagesOverride ?? mounts?.page_image_indexes.map((entry) => entry.url).join(',')}
        artifactsRoot={artifactsRoot}
      />
    )
  }

  return (
    <div className="pdf-lab-app-shell">
      <VerificationNav active={verificationRoute} />
      {verificationView}
    </div>
  )
}
