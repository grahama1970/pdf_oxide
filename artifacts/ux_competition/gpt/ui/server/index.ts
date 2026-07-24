import express from 'express'
import {
  createReadStream,
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  statSync,
  writeFileSync,
} from 'fs'
import { appendFile, cp, mkdir, readFile, readdir, stat, writeFile } from 'fs/promises'
import { dirname, isAbsolute, relative, resolve } from 'path'
import { fileURLToPath } from 'url'
import { isCalibrationLabelRow, type CalibrationLabelRow } from '../src/adapters/calibration'

type JsonRecord = Record<string, unknown>

const __dirname = dirname(fileURLToPath(import.meta.url))
const app = express()
const PORT = Number(process.env.PDF_LAB_API_PORT || 3013)

const PDF_LAB_UI_ROOT = resolve(__dirname, '..')
const PDF_LAB_SKILL_ROOT = resolve(PDF_LAB_UI_ROOT, '..')
const DIST_ROOT = resolve(PDF_LAB_UI_ROOT, 'dist')
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
const CALIBRATION_LABELS_PATH = resolve(
  process.env.PDF_LAB_CALIBRATION_LABELS_PATH
    ?? resolve(ARTIFACTS_ROOT, 'calibration', 'labels_v1.jsonl'),
)

app.use(express.json({ limit: '25mb' }))

function isPathInside(root: string, candidate: string): boolean {
  const pathFromRoot = relative(resolve(root), resolve(candidate))
  return pathFromRoot === '' || (!pathFromRoot.startsWith('..') && !isAbsolute(pathFromRoot))
}

function allowedStaticRoot(root: string): string | null {
  try {
    if (!existsSync(root) || !statSync(root).isDirectory()) return null
    return realpathSync(root)
  } catch {
    return null
  }
}

const staticRoots = [allowedStaticRoot(PUBLIC_ROOT), allowedStaticRoot(ARTIFACTS_ROOT)]
  .filter((root): root is string => Boolean(root))

function cleanArtifactName(name: string): string {
  return name
    .replace(/^https?:\/\/[^/]+/i, '')
    .replace(/^\/+/, '')
    .replace(/^artifacts\/pdf-lab\//, '')
}

function resolveArtifactPath(relativeName: string): string | null {
  const cleanName = cleanArtifactName(relativeName)
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
  return `/artifacts/pdf-lab/${cleanArtifactName(relativeName).split('/').map(encodeURIComponent).join('/')}`
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
  return root ? { label, ok: true, path: root } : { label, ok: false, path: null, reason: 'missing_directory' }
}

function sortBlocks(blocks: JsonRecord[]): JsonRecord[] {
  return blocks.slice().sort((left, right) => {
    const pageDelta = Number(left.page ?? 0) - Number(right.page ?? 0)
    if (pageDelta !== 0) return pageDelta
    const leftBox = Array.isArray(left.bbox) ? left.bbox : [0, 0]
    const rightBox = Array.isArray(right.bbox) ? right.bbox : [0, 0]
    return Number(leftBox[1] ?? 0) - Number(rightBox[1] ?? 0)
      || Number(leftBox[0] ?? 0) - Number(rightBox[0] ?? 0)
  })
}

function safeKey(value: string): string {
  return value.replace(/[^a-zA-Z0-9._:-]+/g, '_').slice(0, 180)
}

function parseJsonLines(content: string): unknown[] {
  return content
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
    .map((line, index) => {
      try {
        return JSON.parse(line) as unknown
      } catch (error) {
        throw new Error(`invalid JSONL line ${index + 1}: ${error instanceof Error ? error.message : String(error)}`)
      }
    })
}

app.get('/api/pdf-lab/status', (_req, res) => {
  res.json({
    ok: true,
    skillRoot: PDF_LAB_SKILL_ROOT,
    publicRoot: summarizeArtifact(allowedStaticRoot(PUBLIC_ROOT), 'public'),
    artifactsRoot: summarizeArtifact(allowedStaticRoot(ARTIFACTS_ROOT), 'artifacts'),
    servedRoots: staticRoots,
    signoffsPath: SIGNOFFS_PATH,
    calibrationLabelsPath: CALIBRATION_LABELS_PATH,
  })
})

// Calibration writes only the frozen labels_v1 row contract. The sample and
// content-addressed page images remain read-only static artifacts.
let calibrationWriteChain: Promise<void> = Promise.resolve()

app.get('/api/pdf-lab/calibration/labels', async (_req, res) => {
  res.setHeader('Cache-Control', 'no-store')
  if (!existsSync(CALIBRATION_LABELS_PATH)) {
    res.json({ ok: true, rows: [], path: CALIBRATION_LABELS_PATH })
    return
  }
  try {
    const rows = parseJsonLines(await readFile(CALIBRATION_LABELS_PATH, 'utf-8'))
    const invalidIndex = rows.findIndex((row) => !isCalibrationLabelRow(row))
    if (invalidIndex >= 0) {
      res.status(500).json({ ok: false, error: 'invalid_labels_v1_row', row: invalidIndex + 1 })
      return
    }
    res.json({ ok: true, rows, path: CALIBRATION_LABELS_PATH })
  } catch (error) {
    res.status(500).json({ ok: false, error: 'labels_v1_read_failed', detail: String(error) })
  }
})

app.post('/api/pdf-lab/calibration/labels', async (req, res) => {
  if (!isCalibrationLabelRow(req.body)) {
    res.status(400).json({
      ok: false,
      error: 'invalid_labels_v1_row',
      required: ['item_sha', 'label', 'ts'],
      allowed_labels: ['correct', 'wrong_type', 'wrong_bounds', 'not_an_element'],
    })
    return
  }
  const row = req.body as CalibrationLabelRow
  try {
    calibrationWriteChain = calibrationWriteChain.then(async () => {
      await mkdir(dirname(CALIBRATION_LABELS_PATH), { recursive: true })
      await appendFile(CALIBRATION_LABELS_PATH, `${JSON.stringify(row)}\n`, 'utf-8')
    })
    await calibrationWriteChain
    res.status(201).json({ ok: true, row, path: CALIBRATION_LABELS_PATH })
  } catch (error) {
    res.status(500).json({ ok: false, error: 'labels_v1_write_failed', detail: String(error) })
  }
})

// Transparent Tau loop artifacts.
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
  const runs = entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort().reverse()
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
    if (!existsSync(candidate)) {
      artifacts[key] = null
      continue
    }
    try {
      artifacts[key] = JSON.parse(await readFile(candidate, 'utf-8'))
    } catch (error) {
      artifacts[key] = { ok: false, error: 'unreadable_artifact', detail: String(error) }
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
    for (const [target, filename] of [[terminalLedgers, 'terminal_ledger.json'], [repairReceipts, 'repair_receipt.json']] as const) {
      const path = resolve(pageRoot, filename)
      if (!existsSync(path)) continue
      try {
        target[pageDir] = JSON.parse(await readFile(path, 'utf-8'))
      } catch {
        target[pageDir] = null
      }
    }
    pageImages[pageDir] = (await readdir(pageRoot, { withFileTypes: true }))
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

app.get('/api/pdf-lab/loop-runs/:runId/file', (req, res) => {
  const root = loopRunsRoot()
  const requestedPath = String(req.query.path ?? '')
  if (!root || !requestedPath) {
    res.status(400).json({ ok: false, error: 'missing_root_or_path' })
    return
  }
  const runDir = resolve(root, safeKey(req.params.runId))
  const candidate = resolve(runDir, requestedPath.replace(/^\/+/, ''))
  if (!isPathInside(root, runDir) || !isPathInside(runDir, candidate) || !existsSync(candidate) || !statSync(candidate).isFile()) {
    res.status(404).json({ ok: false, error: 'unknown_loop_run_file' })
    return
  }
  if (/\.png$/i.test(candidate)) res.type('image/png')
  else if (/\.(jpg|jpeg)$/i.test(candidate)) res.type('image/jpeg')
  else if (/\.json$/i.test(candidate)) res.type('application/json')
  else res.type('text/plain')
  createReadStream(candidate).pipe(res)
})

app.get('/api/pdf-lab/nico-qa-report', (_req, res) => {
  const report = readJsonIfExists<JsonRecord>('pdf-lab-memory-qa-report.json')
    ?? readJsonIfExists<JsonRecord>('pdf-lab-nico-qa-report.json')
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
    res.status(404).json({ ...missingArtifact('pdf-lab-nist-full-extraction.json'), answer: null, citations: [] })
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
    warnings: selected.some((element) => !Array.isArray(element.page_image_refs))
      ? ['retrieval contract incomplete: one or more elements lack page_image_refs']
      : [],
    citations: selected.map((element) => ({
      element_id: String(element.id ?? element.element_id ?? ''),
      page: element.page,
      type: element.type,
      bbox: element.bbox,
      text: String(element.text ?? '').slice(0, 500),
      page_image_refs: element.page_image_refs,
      section_id: element.section_id,
      section_path: element.section_path,
      pdf_sha256: element.pdf_sha256 ?? extraction.pdf_sha256,
    })),
    extracted_json_fragments: selected,
    source_extraction: pdfLabPublicUrl('pdf-lab-nist-full-extraction.json'),
    similar_elements: [],
    pdf_sha256: extraction.pdf_sha256,
  })
})

app.post('/api/pdf-lab/review-save', async (req, res) => {
  const body = req.body && typeof req.body === 'object' ? req.body as JsonRecord : {}
  const extractionUrl = typeof body.extractionUrl === 'string' ? body.extractionUrl : ''
  const updatedBlocks = Array.isArray(body.updatedBlocks) ? body.updatedBlocks as JsonRecord[] : []
  const deletedBlockIds = Array.isArray(body.deletedBlockIds) ? body.deletedBlockIds.map(String) : []
  const sourcePath = extractionUrl ? resolveArtifactPath(extractionUrl) : null
  if (!sourcePath) {
    res.status(404).json(missingArtifact(extractionUrl || 'extractionUrl'))
    return
  }

  const existing = JSON.parse(await readFile(sourcePath, 'utf-8')) as JsonRecord
  const blocks = Array.isArray(existing.blocks) ? existing.blocks as JsonRecord[] : []
  const blockMap = new Map<string, JsonRecord>()
  for (const block of blocks) if (typeof block.id === 'string') blockMap.set(block.id, block)
  for (const block of updatedBlocks) if (typeof block.id === 'string') blockMap.set(block.id, block)
  for (const blockId of deletedBlockIds) blockMap.delete(blockId)

  await mkdir(REVIEW_SAVE_DIR, { recursive: true })
  const outputPath = resolve(REVIEW_SAVE_DIR, `${safeKey(extractionUrl || 'review')}.json`)
  const nextExtraction = {
    ...existing,
    blocks: sortBlocks([...blockMap.values()]),
    reviewMode: body.reviewMode ?? existing.reviewMode ?? 'reviewed',
    reviewSummary: body.reviewSummary ?? existing.reviewSummary ?? null,
    humanEdits: {
      updatedAt: new Date().toISOString(),
      updatedBlocks: updatedBlocks.length,
      deletedBlocks: deletedBlockIds.length,
    },
  }
  await writeFile(outputPath, `${JSON.stringify(nextExtraction, null, 2)}\n`, 'utf-8')
  res.json({ saved: true, outputPath, updatedBlocks: updatedBlocks.length, deletedBlocks: deletedBlockIds.length, extraction: nextExtraction })
})

app.post('/api/pdf-lab/reextract-table-region', (_req, res) => {
  res.status(501).json({
    ok: false,
    error: 'table_reextract_not_configured',
    detail: 'Configure a pdf_oxide/Camelot bridge before using table-region re-extraction.',
  })
})

app.get('/api/pdf-lab/jobs/latest', (_req, res) => res.json({ ok: true, job: null }))
app.get('/api/pdf-lab/jobs/:jobId', (req, res) => res.status(404).json({ ok: false, error: 'job_not_found', detail: req.params.jobId }))

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
    if (info.isFile() && /\.(json|jsonl|png|jpg|jpeg|pdf)$/i.test(entry)) {
      await cp(source, resolve(ARTIFACTS_ROOT, entry))
    }
  }
  res.json({ ok: true, outputDir, promotedTo: ARTIFACTS_ROOT })
})

for (const route of ['/api/pdf-lab/commit-sweep-to-run', '/api/pdf-lab/bulk-repair-rerun', '/api/pdf-lab/eject-mismatches-to-triage']) {
  app.post(route, (_req, res) => {
    res.status(501).json({ ok: false, error: 'runtime_bridge_not_configured', detail: `Standalone route ${route} does not launch pdf_oxide.` })
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
  await appendFile(decisionsPath, `${JSON.stringify({ ...body, updated_at: new Date().toISOString() })}\n`, 'utf-8')
  res.json({ ok: true, taskId, decisionsPath })
})

app.post('/api/pdf-lab/gemini-review-bundle', (_req, res) => {
  res.status(501).json({ ok: false, error: 'review_bundle_not_configured', detail: 'Legacy Gemini bundle generation is not owned by this standalone UI.' })
})

app.get('/api/pdf-lab/evidence-asset', (req, res) => {
  const rawPath = typeof req.query.path === 'string' ? req.query.path : ''
  if (!rawPath) {
    res.status(400).json({ error: 'path query parameter required' })
    return
  }
  let filePath = resolveArtifactPath(rawPath)
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
  if (lowerPath.endsWith('.pdf')) res.type('application/pdf')
  else if (/\.(jpg|jpeg)$/.test(lowerPath)) res.type('image/jpeg')
  else if (lowerPath.endsWith('.png')) res.type('image/png')
  else res.type('application/octet-stream')
  createReadStream(filePath).pipe(res)
})

app.get('/pdf-lab-api/signoffs/load', (_req, res) => {
  res.type('application/json')
  res.send(existsSync(SIGNOFFS_PATH)
    ? readFileSync(SIGNOFFS_PATH, 'utf-8')
    : JSON.stringify({ schema_version: 'pdf_lab.signoff_export.v1', signoffs: {} }))
})

app.get('/pdf-lab-api/signoffs/load-in-progress', (_req, res) => {
  res.type('application/json')
  res.send(existsSync(IN_PROGRESS_PATH)
    ? readFileSync(IN_PROGRESS_PATH, 'utf-8')
    : JSON.stringify({ schema_version: 'pdf_lab.in_progress.v1', entries: {} }))
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
  app.get('*', (_req, res) => res.sendFile(resolve(DIST_ROOT, 'index.html')))
}

app.listen(PORT, '127.0.0.1', () => {
  console.log(`PDF Lab API bridge listening on http://127.0.0.1:${PORT}`)
  console.log(`  skill root: ${PDF_LAB_SKILL_ROOT}`)
  console.log(`  public root: ${PUBLIC_ROOT}`)
  console.log(`  artifacts root: ${ARTIFACTS_ROOT}`)
  console.log(`  calibration labels: ${CALIBRATION_LABELS_PATH}`)
  if (existsSync(DIST_ROOT)) console.log(`  ui dist: ${DIST_ROOT}`)
})
