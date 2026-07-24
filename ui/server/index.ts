import express from 'express'
import { createHash } from 'crypto'
import { appendFileSync, createReadStream, existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, statSync, writeFileSync } from 'fs'
import { cp, mkdir, readFile, readdir, stat, writeFile } from 'fs/promises'
import { basename, dirname, relative, resolve } from 'path'
import { fileURLToPath } from 'url'
import {
  PAGE_IMAGE_NAMING,
  type CalibrationPageImageIndex,
  assertCalibrationPageImageIndex,
} from '../src/adapters/pageImageRefs'
import { nearestArtifactMount } from '../src/adapters/artifactMountPairing'
import {
  ContractError,
  appendAnnotationDecision,
  appendCalibrationEvent,
  appendTimingEvent,
  getAnnotationDecisions,
  getCalibrationContract,
  prioritizedQueueResponse,
  verifyAnnotationQueueManifest,
} from './beforeMainContracts'
import {
  ensurePageImage,
  resolveContentAddressedPageImage,
  verifyDocumentMountManifest,
} from './pageImageService'

type JsonRecord = Record<string, unknown>

const __dirname = dirname(fileURLToPath(import.meta.url))
const app = express()
const PORT = Number(process.env.PDF_LAB_API_PORT || 3013)

const PDF_LAB_UI_ROOT = resolve(__dirname, '..')
const PDF_LAB_SKILL_ROOT = resolve(PDF_LAB_UI_ROOT, '..')
const DIST_ROOT = resolve(PDF_LAB_UI_ROOT, 'dist')
// Self-contained defaults: artifacts live inside the pdf_oxide repo
// (ui/ is <repo>/ui, so PDF_LAB_SKILL_ROOT is the repo root). Legacy
// operator locations remain reachable via env overrides.
const REPO_ARTIFACTS_ROOT = resolve(PDF_LAB_SKILL_ROOT, 'artifacts', 'pdf-lab')
const LEGACY_UX_LAB_PUBLIC_ROOT = `${process.env.HOME ?? ''}/workspace/experiments/pi-mono/packages/ux-lab/public`
const PUBLIC_ROOT = resolve(
  process.env.PDF_LAB_PUBLIC_ROOT
    ?? (existsSync(resolve(PDF_LAB_SKILL_ROOT, 'artifacts', 'public'))
      ? resolve(PDF_LAB_SKILL_ROOT, 'artifacts', 'public')
      : LEGACY_UX_LAB_PUBLIC_ROOT),
)
const ARTIFACTS_ROOT = resolve(
  process.env.PDF_LAB_ARTIFACTS_ROOT
    ?? (existsSync(REPO_ARTIFACTS_ROOT) ? REPO_ARTIFACTS_ROOT : '/mnt/storage12tb/pi-mono/artifacts/pdf-lab'),
)
const LOOP_RUNS_ROOT = resolve(process.env.PDF_LAB_LOOP_RUNS_ROOT ?? resolve(ARTIFACTS_ROOT, 'loop-runs'))
const SIGNOFFS_DIR = resolve(process.env.PDF_LAB_SIGNOFFS_DIR ?? '/tmp/pdf-lab-ui/signoffs')
const SIGNOFFS_PATH = resolve(SIGNOFFS_DIR, 'current.json')
const IN_PROGRESS_PATH = resolve(SIGNOFFS_DIR, 'in_progress.json')
const REVIEW_SAVE_DIR = resolve(process.env.PDF_LAB_REVIEW_SAVE_DIR ?? '/tmp/pdf-lab-ui/review-saves')
const CALIBRATION_DIR = resolve(ARTIFACTS_ROOT, 'calibration')
const CALIBRATION_SAMPLE_PATH = resolve(CALIBRATION_DIR, 'sample_v1.jsonl')
const CALIBRATION_PAGE_IMAGES_PATH = resolve(CALIBRATION_DIR, 'page_images_v1.json')
const CALIBRATION_LABELS_PATH = resolve(CALIBRATION_DIR, 'labels_v1.jsonl')
const CALIBRATION_EVENTS_PATH = resolve(CALIBRATION_DIR, 'events_v1.jsonl')
const ANNOTATION_QUEUE_MANIFEST_PATH = resolve(ARTIFACTS_ROOT, 'annotation_queue_manifest_v1.json')
const ANNOTATION_DECISIONS_PATH = resolve(ARTIFACTS_ROOT, 'annotation_decisions_v1.jsonl')
const UX_TIMING_EVENTS_PATH = resolve(ARTIFACTS_ROOT, 'ux_timing_event_v1.jsonl')
const DOCUMENT_MOUNTS_PATH = resolve(ARTIFACTS_ROOT, 'document_mount_manifest_v1.json')
const PAGE_IMAGE_CACHE_ROOT = resolve(ARTIFACTS_ROOT, 'page-image-cache')

app.use(express.json({ limit: '25mb' }))

function sendContractError(res: express.Response, error: unknown): void {
  if (error instanceof ContractError) {
    res.status(error.status).json({ ok: false, error: error.code, detail: error.message })
    return
  }
  res.status(500).json({ ok: false, error: 'internal_contract_error', detail: String(error) })
}

function isPathInside(root: string, candidate: string): boolean {
  const relative = resolve(candidate).slice(resolve(root).length)
  return resolve(candidate) === resolve(root) || (relative.startsWith('/') && !relative.includes('..'))
}

function allowedStaticRoot(root: string): string | null {
  try {
    if (!existsSync(root) || !statSync(root).isDirectory()) return null
    return realpathSync(root)
  } catch {
    return null
  }
}

const staticRoots = [allowedStaticRoot(PUBLIC_ROOT), allowedStaticRoot(ARTIFACTS_ROOT)].filter((root): root is string => Boolean(root))

function resolveArtifactPath(relativeName: string): string | null {
  const cleanName = relativeName.replace(/^\/+/, '')
  for (const root of staticRoots) {
    const candidate = resolve(root, cleanName)
    if (isPathInside(root, candidate) && existsSync(candidate)) return candidate
  }
  return null
}

function readJsonIfExists<T = JsonRecord>(relativeName: string): T | null {
  const path = resolveArtifactPath(relativeName)
  if (!path) return null
  return JSON.parse(readFileSync(path, 'utf-8')) as T
}

function pdfLabPublicUrl(relativeName: string): string {
  return `/artifacts/pdf-lab/${relativeName.replace(/^\/+/, '').split('/').map(encodeURIComponent).join('/')}`
}

function missingArtifact(relativeName: string): JsonRecord {
  return {
    ok: false,
    error: 'missing_artifact',
    artifact: relativeName,
    searched_roots: staticRoots,
  }
}

function summarizeArtifact(root: string | null, label: string): JsonRecord {
  if (!root) return { label, ok: false, path: null, reason: 'missing_directory' }
  return { label, ok: true, path: root }
}

function sortBlocks(blocks: JsonRecord[]): JsonRecord[] {
  return blocks.slice().sort((left, right) => {
    const pageDelta = Number(left.page ?? 0) - Number(right.page ?? 0)
    if (pageDelta !== 0) return pageDelta
    const leftBox = Array.isArray(left.bbox) ? left.bbox : [0, 0]
    const rightBox = Array.isArray(right.bbox) ? right.bbox : [0, 0]
    return Number(leftBox[1] ?? 0) - Number(rightBox[1] ?? 0) || Number(leftBox[0] ?? 0) - Number(rightBox[0] ?? 0)
  })
}

function safeKey(value: string): string {
  return value.replace(/[^a-zA-Z0-9._:-]+/g, '_').slice(0, 180)
}

function artifactFiles(root: string): string[] {
  if (!existsSync(root) || !statSync(root).isDirectory()) return []
  const files: string[] = []
  const pending = [root]
  while (pending.length > 0) {
    const current = pending.pop()
    if (!current) continue
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const path = resolve(current, entry.name)
      if (entry.isDirectory()) pending.push(path)
      else if (entry.isFile()) files.push(path)
    }
  }
  return files.sort()
}

function artifactUrl(path: string): string {
  return pdfLabPublicUrl(relative(ARTIFACTS_ROOT, path))
}

function jsonRecordAt(path: string): JsonRecord | null {
  try {
    const value: unknown = JSON.parse(readFileSync(path, 'utf-8'))
    return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : null
  } catch {
    return null
  }
}

interface DiscoveredPageImageIndex {
  path: string
  url: string
  documentIds: string[]
  pdfSha256s: string[]
  pageCount: number
}

interface DiscoveredSectionTree {
  path: string
  url: string
  documentIds: string[]
  pdfSha256s: string[]
}

function summarizePageImageIndex(path: string): DiscoveredPageImageIndex {
  const value = jsonRecordAt(path)
  const documentIds = new Set<string>()
  const pdfSha256s = new Set<string>()
  const pages = new Set<string>()
  const rows = Array.isArray(value?.pages)
    ? value.pages
    : Array.isArray(value?.page_images)
      ? value.page_images
      : []
  for (const rowValue of rows) {
    if (!rowValue || typeof rowValue !== 'object' || Array.isArray(rowValue)) continue
    const row = rowValue as JsonRecord
    const doc = typeof row.doc === 'string'
      ? row.doc
      : typeof row.document === 'string'
        ? row.document
        : ''
    const page = Number(row.page ?? row.page_index)
    const pdfSha256 = typeof row.pdf_sha256 === 'string' ? row.pdf_sha256 : ''
    if (doc) documentIds.add(doc)
    if (pdfSha256) pdfSha256s.add(pdfSha256)
    if (Number.isInteger(page) && page >= 0) pages.add(`${doc}::${page}`)
  }
  const documents = value?.documents
  if (documents && typeof documents === 'object' && !Array.isArray(documents)) {
    for (const [doc, manifestValue] of Object.entries(documents)) {
      if (!manifestValue || typeof manifestValue !== 'object' || Array.isArray(manifestValue)) continue
      const manifest = manifestValue as JsonRecord
      documentIds.add(doc)
      if (typeof manifest.pdf_sha256 === 'string') pdfSha256s.add(manifest.pdf_sha256)
      if (Array.isArray(manifest.images)) {
        for (const imageValue of manifest.images) {
          if (!imageValue || typeof imageValue !== 'object' || Array.isArray(imageValue)) continue
          const page = Number((imageValue as JsonRecord).page)
          if (Number.isInteger(page) && page >= 0) pages.add(`${doc}::${page}`)
        }
      }
    }
  }
  return {
    path,
    url: artifactUrl(path),
    documentIds: [...documentIds].sort(),
    pdfSha256s: [...pdfSha256s].sort(),
    pageCount: pages.size,
  }
}

function summarizeSectionTree(path: string): DiscoveredSectionTree {
  const value = jsonRecordAt(path)
  const documentIds = new Set<string>()
  const pdfSha256s = new Set<string>()
  if (typeof value?.doc === 'string') documentIds.add(value.doc)
  if (typeof value?.document_id === 'string') documentIds.add(value.document_id)
  if (typeof value?.pdf_sha256 === 'string') pdfSha256s.add(value.pdf_sha256)
  return {
    path,
    url: artifactUrl(path),
    documentIds: [...documentIds].sort(),
    pdfSha256s: [...pdfSha256s].sort(),
  }
}

app.get('/api/pdf-lab/mounts', (_req, res) => {
  const files = artifactFiles(ARTIFACTS_ROOT)
  const pageImageIndexes = files
    .filter((path) => basename(path).endsWith('page_images_v1.json'))
    .map(summarizePageImageIndex)
  const sectionTrees = files
    .filter((path) => basename(path).endsWith('section_tree.json'))
    .map(summarizeSectionTree)
  const indexedDocuments = new Set(pageImageIndexes.flatMap((index) => index.documentIds))

  const annotationCalls = files
    .filter((path) => /(?:^|\/)annotation-calls\/[^/]+\/annotation_call\.json$/.test(path))
    .flatMap((path) => {
      const value = jsonRecordAt(path)
      if (!value || !Array.isArray(value.items)) return []
      const reasons: Record<string, number> = {}
      for (const itemValue of value.items) {
        if (!itemValue || typeof itemValue !== 'object' || Array.isArray(itemValue)) continue
        const reason = String((itemValue as JsonRecord).reason ?? 'unspecified')
        reasons[reason] = (reasons[reason] ?? 0) + 1
      }
      return [{
        url: artifactUrl(path),
        doc_id: basename(dirname(path)),
        item_count: value.items.length,
        reasons,
      }]
    })
    .sort((left, right) => {
      return Number(!indexedDocuments.has(left.doc_id)) - Number(!indexedDocuments.has(right.doc_id))
        || left.doc_id.localeCompare(right.doc_id)
    })

  const retrievalResults = files
    .filter((path) => basename(path).endsWith('retrieval_result.json'))
    .map((path) => {
      const value = jsonRecordAt(path)
      const pdfSha256 = typeof value?.pdf_sha256 === 'string' ? value.pdf_sha256 : undefined
      const evidence = Array.isArray(value?.evidence) ? value.evidence : []
      const documentIds = [...new Set(evidence.flatMap((row) => (
        row && typeof row === 'object' && !Array.isArray(row) && typeof (row as JsonRecord).doc === 'string'
          ? [String((row as JsonRecord).doc)]
          : []
      )))]
      const pageImageIndex = nearestArtifactMount(path, pageImageIndexes, { documentIds, pdfSha256 })
      const sectionTree = nearestArtifactMount(path, sectionTrees, { documentIds, pdfSha256 })
      return {
        url: value?.schema === 'pdf_oxide.retrieval_answer.v1'
          ? `/api/pdf-lab/retrieval-answers/${encodeURIComponent(basename(path))}`
          : artifactUrl(path),
        label: relative(ARTIFACTS_ROOT, path),
        ...(pageImageIndex ? { page_image_index_url: pageImageIndex.url } : {}),
        ...(sectionTree ? { section_tree_url: sectionTree.url } : {}),
      }
    })

  const calibrationSamples = files
    .filter((path) => /(?:^|\/)calibration\/sample_v1\.jsonl$/.test(path))
    .map((path) => {
      const rows = readFileSync(path, 'utf-8').split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
      const documents = new Set<string>()
      for (const row of rows) {
        try {
          const value: unknown = JSON.parse(row)
          if (value && typeof value === 'object' && !Array.isArray(value) && typeof (value as JsonRecord).doc === 'string') {
            documents.add(String((value as JsonRecord).doc))
          }
        } catch {
          // Discovery reports the mount; the calibration route remains fail-closed.
        }
      }
      const pageImageIndex = nearestArtifactMount(path, pageImageIndexes, {
        documentIds: [...documents],
      })
      return {
        url: artifactUrl(path),
        item_count: rows.length,
        ...(pageImageIndex ? { page_image_index_url: pageImageIndex.url } : {}),
        labels_endpoint: '/api/pdf-lab/calibration/events',
      }
    })

  res.json({
    artifacts_root: ARTIFACTS_ROOT,
    annotation_calls: annotationCalls,
    page_image_indexes: pageImageIndexes.map((index) => ({
      url: index.url,
      document_ids: index.documentIds,
      page_count: index.pageCount,
    })),
    retrieval_results: retrievalResults,
    calibration_samples: calibrationSamples,
  })
})

app.get('/api/pdf-lab/status', (_req, res) => {
  res.json({
    ok: true,
    skillRoot: PDF_LAB_SKILL_ROOT,
    publicRoot: summarizeArtifact(allowedStaticRoot(PUBLIC_ROOT), 'public'),
    artifactsRoot: summarizeArtifact(allowedStaticRoot(ARTIFACTS_ROOT), 'artifacts'),
    servedRoots: staticRoots,
    signoffsPath: SIGNOFFS_PATH,
  })
})

function calibrationSampleEntries(): Array<{ item_sha: string; item: JsonRecord }> {
  if (!existsSync(CALIBRATION_SAMPLE_PATH)) return []
  return readFileSync(CALIBRATION_SAMPLE_PATH, 'utf-8')
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      const item = JSON.parse(line) as JsonRecord
      const required = ['doc', 'quintile', 'page', 'bbox', 'type', 'confidence', 'text', 'label']
      if (!required.every(field => Object.hasOwn(item, field)) || item.label !== null) {
        throw new Error(`invalid calibration sample row ${index + 1}`)
      }
      return {
        item_sha: createHash('sha256').update(line).digest('hex'),
        item,
      }
    })
}

function verifiedCalibrationPageImages(
  entries: Array<{ item_sha: string; item: JsonRecord }>,
): CalibrationPageImageIndex {
  const value: unknown = JSON.parse(readFileSync(CALIBRATION_PAGE_IMAGES_PATH, 'utf-8'))
  assertCalibrationPageImageIndex(value)
  const requestedPages = new Map<string, Set<number>>()
  for (const entry of entries) {
    if (typeof entry.item.doc !== 'string' || !Number.isInteger(entry.item.page)) {
      throw new Error(`calibration sample item ${entry.item_sha} has invalid doc/page`)
    }
    const pages = requestedPages.get(entry.item.doc) ?? new Set<number>()
    pages.add(Number(entry.item.page))
    requestedPages.set(entry.item.doc, pages)
  }

  const documents: CalibrationPageImageIndex['documents'] = {}
  for (const [doc, pages] of requestedPages) {
    const manifest = value.documents[doc]
    if (!manifest) throw new Error(`missing page-image manifest for ${doc}`)
    const requestedImages = manifest.images.filter(image => pages.has(image.page))
    if (requestedImages.length !== pages.size) {
      throw new Error(`page-image manifest does not cover every sampled page for ${doc}`)
    }
    for (const image of requestedImages) {
      const imagePath = resolve(CALIBRATION_DIR, manifest.directory, image.filename)
      if (!isPathInside(CALIBRATION_DIR, imagePath) || !existsSync(imagePath) || !statSync(imagePath).isFile()) {
        throw new Error(`missing page image for ${doc} page ${image.page}`)
      }
      const imageBytes = readFileSync(imagePath)
      const byteSha256 = createHash('sha256').update(imageBytes).digest('hex')
      if (byteSha256 !== image.byte_sha256) {
        throw new Error(`page-image byte hash mismatch for ${doc} page ${image.page}`)
      }
      // Keep keys in Python's sort_keys=True order; see
      // pipeline_page_images.py::canonical_page_image_filename.
      const canonicalInputs = JSON.stringify({
        dpi: manifest.dpi,
        format: manifest.format,
        page_index: image.page,
        pdf_sha256: manifest.pdf_sha256,
        schema: manifest.schema,
      })
      const canonicalSha256 = createHash('sha256')
        .update(canonicalInputs)
        .update(Buffer.from([0]))
        .update(imageBytes)
        .digest('hex')
      if (image.filename !== `${canonicalSha256}.png` || manifest.naming !== PAGE_IMAGE_NAMING) {
        throw new Error(`page-image content identity mismatch for ${doc} page ${image.page}`)
      }
    }
    documents[doc] = { ...manifest, images: requestedImages }
  }
  return { schema: value.schema, documents }
}

app.get('/api/pdf-lab/calibration/sample', (_req, res) => {
  if (!existsSync(CALIBRATION_SAMPLE_PATH)) {
    res.status(404).json({ ok: false, error: 'missing_calibration_sample', path: CALIBRATION_SAMPLE_PATH })
    return
  }
  if (!existsSync(CALIBRATION_PAGE_IMAGES_PATH)) {
    res.status(404).json({ ok: false, error: 'missing_calibration_page_images', path: CALIBRATION_PAGE_IMAGES_PATH })
    return
  }
  try {
    const entries = calibrationSampleEntries()
    res.json({
      schema: 'pdf_oxide.calibration_sample_response.v1',
      items: entries,
      page_images: verifiedCalibrationPageImages(entries),
    })
  } catch (err) {
    res.status(422).json({ ok: false, error: 'invalid_calibration_contract', detail: String(err) })
  }
})

app.get('/api/pdf-lab/calibration/events', (_req, res) => {
  try {
    res.json(getCalibrationContract(
      CALIBRATION_EVENTS_PATH,
      CALIBRATION_LABELS_PATH,
      calibrationSampleEntries(),
    ))
  } catch (error) {
    sendContractError(res, error)
  }
})

app.post('/api/pdf-lab/calibration/events', (req, res) => {
  try {
    const result = appendCalibrationEvent(
      req.body,
      CALIBRATION_EVENTS_PATH,
      CALIBRATION_LABELS_PATH,
      calibrationSampleEntries(),
    )
    res.status(result.duplicate ? 200 : 201).json({
      ok: true,
      duplicate: result.duplicate,
      event: result.event,
      ...result.response,
    })
  } catch (error) {
    sendContractError(res, error)
  }
})

app.get('/api/pdf-lab/calibration/labels', (_req, res) => {
  try {
    const contract = getCalibrationContract(
      CALIBRATION_EVENTS_PATH,
      CALIBRATION_LABELS_PATH,
      calibrationSampleEntries(),
    )
    res.json({ schema: 'pdf_oxide.calibration_labels_response.v1', rows: contract.labels })
  } catch (error) {
    sendContractError(res, error)
  }
})

app.get('/api/pdf-lab/annotation-queue', (_req, res) => {
  try {
    res.json(prioritizedQueueResponse(ARTIFACTS_ROOT, ANNOTATION_QUEUE_MANIFEST_PATH))
  } catch (error) {
    sendContractError(res, error)
  }
})

app.get('/api/pdf-lab/annotation-decisions', (_req, res) => {
  try {
    res.json(getAnnotationDecisions(ANNOTATION_DECISIONS_PATH))
  } catch (error) {
    sendContractError(res, error)
  }
})

app.post('/api/pdf-lab/annotation-decisions', (req, res) => {
  try {
    const result = appendAnnotationDecision(
      req.body,
      ANNOTATION_DECISIONS_PATH,
      ARTIFACTS_ROOT,
      ANNOTATION_QUEUE_MANIFEST_PATH,
    )
    res.status(result.duplicate ? 200 : 201).json({
      ok: true,
      duplicate: result.duplicate,
      event: result.event,
      ...result.response,
    })
  } catch (error) {
    sendContractError(res, error)
  }
})

app.post('/api/pdf-lab/ux-timing-events', (req, res) => {
  try {
    res.status(201).json({ ok: true, event: appendTimingEvent(req.body, UX_TIMING_EVENTS_PATH) })
  } catch (error) {
    sendContractError(res, error)
  }
})

app.get('/api/pdf-lab/document-mounts', (_req, res) => {
  try {
    const documents = verifyDocumentMountManifest(DOCUMENT_MOUNTS_PATH)
    res.json({ schema: 'pdf_oxide.document_mounts_response.v1', documents })
  } catch (error) {
    sendContractError(res, error)
  }
})

app.get('/api/pdf-lab/page-images/:pdfSha256/:page', (req, res) => {
  try {
    const result = ensurePageImage(
      DOCUMENT_MOUNTS_PATH,
      PAGE_IMAGE_CACHE_ROOT,
      req.params.pdfSha256,
      Number(req.params.page),
      Number(req.query.dpi ?? 150),
    )
    res.setHeader('Content-Type', 'image/png')
    res.setHeader('Content-Location', `/api/pdf-lab/page-image-content/${result.manifest.filename}`)
    res.setHeader('Content-SHA256', result.manifest.byte_sha256)
    res.sendFile(result.path)
  } catch (error) {
    sendContractError(res, error)
  }
})

app.get('/api/pdf-lab/page-image-content/:filename', (req, res) => {
  try {
    const result = resolveContentAddressedPageImage(PAGE_IMAGE_CACHE_ROOT, req.params.filename)
    res.setHeader('Content-Type', 'image/png')
    res.setHeader('Content-SHA256', result.manifest.byte_sha256)
    res.sendFile(result.path)
  } catch (error) {
    sendContractError(res, error)
  }
})

app.get('/api/pdf-lab/retrieval-answers/:filename', (req, res) => {
  try {
    if (!/^[A-Za-z0-9._-]+_retrieval_result\.json$/.test(req.params.filename)) {
      throw new ContractError(400, 'invalid_retrieval_answer_filename')
    }
    const answerPath = resolve(ARTIFACTS_ROOT, req.params.filename)
    if (!isPathInside(ARTIFACTS_ROOT, answerPath) || !existsSync(answerPath)) {
      throw new ContractError(422, 'retrieval_answer_missing')
    }
    const raw: unknown = JSON.parse(readFileSync(answerPath, 'utf-8'))
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new ContractError(422, 'invalid_retrieval_answer')
    }
    const answer = raw as JsonRecord
    if (answer.schema !== 'pdf_oxide.retrieval_answer.v1' || !Array.isArray(answer.evidence_groups)) {
      throw new ContractError(422, 'invalid_retrieval_answer')
    }
    for (const [groupIndex, groupValue] of answer.evidence_groups.entries()) {
      if (!groupValue || typeof groupValue !== 'object' || Array.isArray(groupValue)) {
        throw new ContractError(422, 'invalid_retrieval_evidence_group')
      }
      const image = (groupValue as JsonRecord).page_image
      if (!image || typeof image !== 'object' || Array.isArray(image)) {
        throw new ContractError(422, 'retrieval_page_image_missing')
      }
      const imageRecord = image as JsonRecord
      if (
        imageRecord.verified !== true
        || typeof imageRecord.href !== 'string'
        || typeof imageRecord.sha256 !== 'string'
        || typeof imageRecord.byte_sha256 !== 'string'
      ) {
        throw new ContractError(422, 'retrieval_page_image_unverified')
      }
      const prefix = '/artifacts/pdf-lab/'
      if (!imageRecord.href.startsWith(prefix)) throw new ContractError(422, 'retrieval_page_image_href_invalid')
      const imagePath = resolve(ARTIFACTS_ROOT, decodeURIComponent(imageRecord.href.slice(prefix.length)))
      if (!isPathInside(ARTIFACTS_ROOT, imagePath) || !existsSync(imagePath)) {
        throw new ContractError(422, 'retrieval_page_image_missing', `group ${groupIndex}`)
      }
      const byteSha256 = createHash('sha256').update(readFileSync(imagePath)).digest('hex')
      if (
        byteSha256 !== imageRecord.byte_sha256
        || basename(imagePath) !== `${imageRecord.sha256}.png`
      ) {
        throw new ContractError(422, 'retrieval_page_image_hash_mismatch', `group ${groupIndex}`)
      }
    }
    const answerBytes = readFileSync(answerPath)
    res.setHeader('Content-SHA256', createHash('sha256').update(answerBytes).digest('hex'))
    res.type('application/json').send(answerBytes)
  } catch (error) {
    sendContractError(res, error)
  }
})

app.get('/api/pdf-lab/annotation-queue-manifest', (_req, res) => {
  try {
    res.json(verifyAnnotationQueueManifest(ARTIFACTS_ROOT, ANNOTATION_QUEUE_MANIFEST_PATH).manifest)
  } catch (error) {
    sendContractError(res, error)
  }
})

// --- Transparent tau-loop artifacts (read-only) -------------------------
// One loop run directory holds the evidence chain the loop viewer renders:
// comparison.json (pdf-lab.comparison.v2), pdf-lab-second-pass-backlog.json,
// human_triage_queue.json, page_*/terminal_ledger.json,
// gs001-ticket-projection.json and gs001-closure-report.json (tau audit).

const LOOP_RUN_ARTIFACTS: Record<string, string> = {
  comparison: 'comparison.json',
  backlog: 'pdf-lab-second-pass-backlog.json',
  human_triage_queue: 'human_triage_queue.json',
  ticket_projection: 'gs001-ticket-projection.json',
  closure_report: 'gs001-closure-report.json',
  regression_verdict: 'regression_verdict.json',
  run_summary: 'run_summary.json',
}

function loopRunsRoot(): string | null {
  return allowedStaticRoot(LOOP_RUNS_ROOT)
}

app.get('/api/pdf-lab/loop-runs', async (_req, res) => {
  const root = loopRunsRoot()
  if (!root) {
    res.status(404).json({ ok: false, error: 'missing_loop_runs_root', path: LOOP_RUNS_ROOT })
    return
  }
  const entries = await readdir(root, { withFileTypes: true })
  const runs = entries
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
    .reverse()
  res.json({ ok: true, root, runs })
})

app.get('/api/pdf-lab/loop-runs/:runId', async (req, res) => {
  const root = loopRunsRoot()
  if (!root) {
    res.status(404).json({ ok: false, error: 'missing_loop_runs_root', path: LOOP_RUNS_ROOT })
    return
  }
  const runDir = resolve(root, safeKey(req.params.runId))
  if (!isPathInside(root, runDir) || !existsSync(runDir)) {
    res.status(404).json({ ok: false, error: 'unknown_loop_run', run: req.params.runId })
    return
  }
  const artifacts: JsonRecord = {}
  for (const [key, filename] of Object.entries(LOOP_RUN_ARTIFACTS)) {
    const candidate = resolve(runDir, filename)
    if (existsSync(candidate)) {
      try {
        artifacts[key] = JSON.parse(await readFile(candidate, 'utf-8'))
      } catch (err) {
        artifacts[key] = { ok: false, error: 'unreadable_artifact', detail: String(err) }
      }
    } else {
      artifacts[key] = null
    }
  }
  const pageDirs = (await readdir(runDir, { withFileTypes: true }))
    .filter((entry) => entry.isDirectory() && /^page_\d+$/.test(entry.name))
    .map((entry) => entry.name)
    .sort()
  const terminalLedgers: JsonRecord = {}
  const repairReceipts: JsonRecord = {}
  const pageImages: Record<string, string[]> = {}
  for (const pageDir of pageDirs) {
    const pageRoot = resolve(runDir, pageDir)
    const ledgerPath = resolve(pageRoot, 'terminal_ledger.json')
    if (existsSync(ledgerPath)) {
      try {
        terminalLedgers[pageDir] = JSON.parse(await readFile(ledgerPath, 'utf-8'))
      } catch {
        terminalLedgers[pageDir] = null
      }
    }
    const receiptPath = resolve(pageRoot, 'repair_receipt.json')
    if (existsSync(receiptPath)) {
      try {
        repairReceipts[pageDir] = JSON.parse(await readFile(receiptPath, 'utf-8'))
      } catch {
        repairReceipts[pageDir] = null
      }
    }
    const pageEntries = await readdir(pageRoot, { withFileTypes: true })
    pageImages[pageDir] = pageEntries
      .filter((entry) => entry.isFile() && /\.(png|jpg|jpeg)$/i.test(entry.name))
      .map((entry) => `${pageDir}/${entry.name}`)
      .sort()
  }
  res.json({
    ok: true,
    run: req.params.runId,
    runDir,
    artifacts,
    terminal_ledgers: terminalLedgers,
    repair_receipts: repairReceipts,
    page_images: pageImages,
    page_dirs: pageDirs,
  })
})

// Serve one file (image, patch, receipt) from inside a loop-run directory.
app.get('/api/pdf-lab/loop-runs/:runId/file', (req, res) => {
  const root = loopRunsRoot()
  const relative = String(req.query.path ?? '')
  if (!root || !relative) {
    res.status(400).json({ ok: false, error: 'missing_root_or_path' })
    return
  }
  const runDir = resolve(root, safeKey(req.params.runId))
  const candidate = resolve(runDir, relative.replace(/^\/+/, ''))
  if (!isPathInside(root, runDir) || !isPathInside(runDir, candidate) || !existsSync(candidate) || !statSync(candidate).isFile()) {
    res.status(404).json({ ok: false, error: 'unknown_loop_run_file' })
    return
  }
  if (/\.(png|jpg|jpeg)$/i.test(candidate)) {
    res.type(candidate.endsWith('.png') ? 'image/png' : 'image/jpeg')
  } else if (candidate.endsWith('.json')) {
    res.type('application/json')
  } else {
    res.type('text/plain')
  }
  createReadStream(candidate).pipe(res)
})

app.get('/api/pdf-lab/nico-qa-report', (_req, res) => {
  const report = readJsonIfExists<JsonRecord>('pdf-lab-memory-qa-report.json') ?? readJsonIfExists<JsonRecord>('pdf-lab-nico-qa-report.json')
  if (!report) {
    res.status(404).json(missingArtifact('pdf-lab-memory-qa-report.json'))
    return
  }
  res.json({ ok: true, report, source: 'artifact-root' })
})

app.post('/api/pdf-lab/evidence-query', (req, res) => {
  const body = req.body && typeof req.body === 'object' ? req.body as JsonRecord : {}
  const extraction = readJsonIfExists<JsonRecord>('pdf-lab-nist-full-extraction.json')
  if (!extraction || !Array.isArray(extraction.elements)) {
    res.status(404).json({
      ...missingArtifact('pdf-lab-nist-full-extraction.json'),
      answer: null,
      uncertainty: 'missing_artifact',
      citations: [],
    })
    return
  }

  const question = typeof body.question === 'string' ? body.question : ''
  const pageMatch = question.match(/\bpage\s+(\d+)\b/i)
  const requestedPage = typeof body.page === 'number' ? body.page : pageMatch ? Number(pageMatch[1]) : null
  const requestedType = typeof body.elementType === 'string' ? body.elementType : null
  const requestedElementId = typeof body.elementId === 'string' ? body.elementId : null
  let elements = extraction.elements as JsonRecord[]
  if (requestedPage !== null) elements = elements.filter((element) => Number(element.page) === requestedPage)
  if (requestedType) elements = elements.filter((element) => String(element.type ?? '') === requestedType)
  if (requestedElementId) elements = elements.filter((element) => String(element.id ?? element.element_id ?? '') === requestedElementId)
  const selected = elements.slice(0, 12)

  res.json({
    ok: true,
    answer: selected.length
      ? `Found ${selected.length} extracted element${selected.length === 1 ? '' : 's'} in the current PDF Lab artifact set.`
      : 'No matching extracted element was found in the current PDF Lab artifact set.',
    uncertainty: 'artifact_grounded',
    warnings: [],
    citations: selected.map((element) => ({
      element_id: String(element.id ?? element.element_id ?? ''),
      page: element.page,
      type: element.type,
      bbox: element.bbox,
      text: String(element.text ?? '').slice(0, 500),
    })),
    extracted_json_fragments: selected,
    source_extraction: pdfLabPublicUrl('pdf-lab-nist-full-extraction.json'),
    similar_elements: [],
  })
})

app.post('/api/pdf-lab/review-save', async (req, res) => {
  const body = req.body && typeof req.body === 'object' ? req.body as JsonRecord : {}
  const extractionUrl = typeof body.extractionUrl === 'string' ? body.extractionUrl : ''
  const updatedBlocks = Array.isArray(body.updatedBlocks) ? body.updatedBlocks as JsonRecord[] : []
  const deletedBlockIds = Array.isArray(body.deletedBlockIds) ? body.deletedBlockIds.map(String) : []
  const sourcePath = extractionUrl ? resolveArtifactPath(extractionUrl.replace(/^\/+/, '')) : null
  if (!sourcePath) {
    res.status(404).json(missingArtifact(extractionUrl || 'extractionUrl'))
    return
  }

  const existing = JSON.parse(await readFile(sourcePath, 'utf-8')) as JsonRecord
  const blocks = Array.isArray(existing.blocks) ? existing.blocks as JsonRecord[] : []
  const blockMap = new Map<string, JsonRecord>()
  for (const block of blocks) {
    if (typeof block.id === 'string') blockMap.set(block.id, block)
  }
  for (const block of updatedBlocks) {
    if (typeof block.id === 'string') blockMap.set(block.id, block)
  }
  for (const blockId of deletedBlockIds) blockMap.delete(blockId)

  await mkdir(REVIEW_SAVE_DIR, { recursive: true })
  const outputPath = resolve(REVIEW_SAVE_DIR, `${safeKey(extractionUrl || 'review')}.json`)
  const now = new Date().toISOString()
  const nextExtraction = {
    ...existing,
    blocks: sortBlocks(Array.from(blockMap.values())),
    reviewMode: body.reviewMode ?? existing.reviewMode ?? 'reviewed',
    reviewSummary: body.reviewSummary ?? existing.reviewSummary ?? null,
    humanEdits: {
      updatedAt: now,
      updatedBlocks: updatedBlocks.length,
      deletedBlocks: deletedBlockIds.length,
    },
  }
  await writeFile(outputPath, `${JSON.stringify(nextExtraction, null, 2)}\n`, 'utf-8')
  res.json({
    saved: true,
    outputPath,
    updatedBlocks: updatedBlocks.length,
    deletedBlocks: deletedBlockIds.length,
    extraction: nextExtraction,
  })
})

app.post('/api/pdf-lab/reextract-table-region', (_req, res) => {
  res.status(501).json({
    ok: false,
    error: 'table_reextract_not_configured',
    detail: 'Configure a pdf_oxide/Camelot bridge for this standalone UI before using table-region re-extraction.',
  })
})

app.get('/api/pdf-lab/jobs/latest', (_req, res) => {
  res.json({ ok: true, job: null })
})

app.get('/api/pdf-lab/jobs/:jobId', (req, res) => {
  res.status(404).json({ ok: false, error: 'job_not_found', detail: req.params.jobId })
})

app.post('/api/pdf-lab/jobs/promote-output', async (req, res) => {
  const outputDir = typeof req.body?.outputDir === 'string' ? req.body.outputDir : ''
  if (!outputDir.startsWith('/tmp/pdf-lab-') || !existsSync(outputDir)) {
    res.status(400).json({ ok: false, error: 'invalid_output_dir', outputDir })
    return
  }
  await mkdir(ARTIFACTS_ROOT, { recursive: true })
  for (const entry of await readdir(outputDir)) {
    const source = resolve(outputDir, entry)
    const info = await stat(source)
    if (info.isFile() && /\.(json|png|jpg|jpeg|pdf)$/i.test(entry)) {
      await cp(source, resolve(ARTIFACTS_ROOT, entry))
    }
  }
  res.json({ ok: true, outputDir, promotedTo: ARTIFACTS_ROOT })
})

for (const route of ['/api/pdf-lab/commit-sweep-to-run', '/api/pdf-lab/bulk-repair-rerun', '/api/pdf-lab/eject-mismatches-to-triage']) {
  app.post(route, (_req, res) => {
    res.status(501).json({
      ok: false,
      error: 'runtime_bridge_not_configured',
      detail: `Standalone UI route ${route} is present but does not launch pdf_oxide by default.`,
    })
  })
}

app.post('/api/pdf-lab/triage-decision', async (req, res) => {
  const body = req.body && typeof req.body === 'object' ? req.body as JsonRecord : {}
  const taskId = typeof body.taskId === 'string' ? body.taskId : ''
  if (!taskId) {
    res.status(400).json({ ok: false, error: 'taskId is required' })
    return
  }
  const decisionsPath = resolve(SIGNOFFS_DIR, 'triage_decisions.jsonl')
  await mkdir(SIGNOFFS_DIR, { recursive: true })
  writeFileSync(decisionsPath, `${JSON.stringify({ ...body, updated_at: new Date().toISOString() })}\n`, { flag: 'a' })
  res.json({ ok: true, taskId, decisionsPath })
})

app.post('/api/pdf-lab/gemini-review-bundle', (_req, res) => {
  res.status(501).json({
    ok: false,
    error: 'review_bundle_not_configured',
    detail: 'The standalone PDF Lab UI does not own the legacy ux-lab Gemini bundle generator.',
  })
})

app.get('/api/pdf-lab/evidence-asset', (req, res) => {
  const rawPath = typeof req.query.path === 'string' ? req.query.path : ''
  if (!rawPath) {
    res.status(400).json({ error: 'path query parameter required' })
    return
  }
  let filePath = resolveArtifactPath(rawPath.replace(/^\/+/, '').replace(/^artifacts\/pdf-lab\//, ''))
  if (!filePath && rawPath.startsWith('/')) {
    try {
      const realPath = realpathSync(rawPath)
      if (staticRoots.some((root) => isPathInside(root, realPath))) filePath = realPath
    } catch {
      filePath = null
    }
  }
  if (!filePath) {
    res.status(404).json({ error: 'Evidence asset not found or not allowed' })
    return
  }
  const lowerPath = filePath.toLowerCase()
  res.setHeader('Content-Type', lowerPath.endsWith('.pdf') ? 'application/pdf' : lowerPath.endsWith('.jpg') || lowerPath.endsWith('.jpeg') ? 'image/jpeg' : 'image/png')
  createReadStream(filePath).pipe(res)
})

app.get('/pdf-lab-api/signoffs/load', (_req, res) => {
  res.type('application/json')
  res.send(existsSync(SIGNOFFS_PATH) ? readFileSync(SIGNOFFS_PATH, 'utf-8') : JSON.stringify({ schema_version: 'pdf_lab.signoff_export.v1', signoffs: {} }))
})

app.get('/pdf-lab-api/signoffs/load-in-progress', (_req, res) => {
  res.type('application/json')
  res.send(existsSync(IN_PROGRESS_PATH) ? readFileSync(IN_PROGRESS_PATH, 'utf-8') : JSON.stringify({ schema_version: 'pdf_lab.in_progress.v1', entries: {} }))
})

app.post('/pdf-lab-api/signoffs/save', (req, res) => {
  mkdirSync(SIGNOFFS_DIR, { recursive: true })
  writeFileSync(SIGNOFFS_PATH, `${JSON.stringify(req.body ?? {}, null, 2)}\n`, 'utf-8')
  res.json({ ok: true, path: SIGNOFFS_PATH })
})

app.post('/pdf-lab-api/signoffs/save-in-progress', (req, res) => {
  mkdirSync(SIGNOFFS_DIR, { recursive: true })
  const entry = req.body && typeof req.body === 'object' ? req.body as JsonRecord : {}
  const existing = existsSync(IN_PROGRESS_PATH)
    ? JSON.parse(readFileSync(IN_PROGRESS_PATH, 'utf-8')) as { entries?: Record<string, unknown> }
    : { schema_version: 'pdf_lab.in_progress.v1', entries: {} as Record<string, unknown> }
  const projectId = typeof entry.project_id === 'string' ? entry.project_id : 'pdf-lab'
  const pageSlug = typeof entry.page_slug === 'string' ? entry.page_slug : 'unknown'
  const key = `${projectId}::${pageSlug}`
  existing.entries = existing.entries ?? {}
  existing.entries[key] = entry
  writeFileSync(IN_PROGRESS_PATH, `${JSON.stringify({ ...existing, updated_at: new Date().toISOString() }, null, 2)}\n`, 'utf-8')
  res.json({ ok: true, key, path: IN_PROGRESS_PATH })
})

for (const root of staticRoots) {
  app.use('/', express.static(root, { fallthrough: true }))
  app.use('/artifacts/pdf-lab', express.static(root, { fallthrough: true }))
}

if (existsSync(DIST_ROOT)) {
  app.use('/', express.static(DIST_ROOT, { fallthrough: true }))
  app.get('*', (_req, res) => {
    res.sendFile(resolve(DIST_ROOT, 'index.html'))
  })
}

app.listen(PORT, '127.0.0.1', () => {
  console.log(`PDF Lab API bridge listening on http://127.0.0.1:${PORT}`)
  console.log(`  skill root: ${PDF_LAB_SKILL_ROOT}`)
  console.log(`  public root: ${PUBLIC_ROOT}`)
  console.log(`  artifacts root: ${ARTIFACTS_ROOT}`)
  if (existsSync(DIST_ROOT)) console.log(`  ui dist: ${DIST_ROOT}`)
})
