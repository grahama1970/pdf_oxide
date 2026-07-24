import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { spawn } from 'node:child_process'
import { cp, mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import net from 'node:net'

import { chromium } from 'playwright'

const UI_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)))
const REPO_ROOT = resolve(UI_ROOT, '..')
const EVIDENCE_CALLS_ROOT = resolve(
  process.env.PDF_LAB_EVIDENCE_CALLS_ROOT
    ?? '/home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls',
)
const ROUND2_LIVE_ROOT = resolve(REPO_ROOT, 'artifacts/ux_competition/round2/live')
const SCREENSHOT_ROOT = resolve(REPO_ROOT, 'artifacts/ux_competition/round3')

const DOCUMENTS = [
  {
    id: 'NIST_SP_800-53r5',
    count: 1219,
    reasons: { low_confidence: 1219 },
  },
  {
    id: 'NIST.SP.800-53Ar5',
    count: 315,
    reasons: { low_confidence: 268, char_parity_deficit: 47 },
  },
  {
    id: '1512.03385v1',
    count: 23,
    reasons: { low_confidence: 17, char_parity_deficit: 1, reviewer_flagged: 5 },
  },
  {
    id: 'NASA_SP-2016-6105',
    count: 604,
    reasons: { low_confidence: 598, char_parity_deficit: 6 },
  },
]
const TOTAL_ITEMS = DOCUMENTS.reduce((total, document) => total + document.count, 0)
const TOTAL_CHAR_PARITY = DOCUMENTS.reduce(
  (total, document) => total + (document.reasons.char_parity_deficit ?? 0),
  0,
)

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

function normalizedTopLeftXyxy(pdfBbox, width, height) {
  assert(Array.isArray(pdfBbox) && pdfBbox.length === 4)
  const [x, y, boxWidth, boxHeight] = pdfBbox.map(Number)
  assert([x, y, boxWidth, boxHeight, width, height].every(Number.isFinite))
  assert(x >= 0 && y >= 0 && boxWidth > 0 && boxHeight > 0)
  const top = height - y - boxHeight
  return [x / width, top / height, (x + boxWidth) / width, (top + boxHeight) / height]
}

function assertLabelRow(row, expected) {
  assert(row && typeof row === 'object' && !Array.isArray(row))
  assert.match(row.item_sha, /^[0-9a-f]{64}$/)
  assert.equal(row.item_sha, expected.item_sha)
  assert.equal(row.label, expected.label)
  assert.equal(new Date(row.ts).toISOString(), row.ts)
  if (expected.label === 'wrong_type') {
    assert.deepEqual(Object.keys(row).sort(), ['corrected_type', 'item_sha', 'label', 'ts'])
    assert.equal(row.corrected_type, expected.corrected_type)
  } else {
    assert.deepEqual(Object.keys(row).sort(), ['item_sha', 'label', 'ts'])
    assert.equal(Object.hasOwn(row, 'corrected_type'), false)
  }
}

async function readLabelRows(path) {
  try {
    const text = (await readFile(path, 'utf8')).trim()
    return text ? text.split(/\r?\n/).map(line => JSON.parse(line)) : []
  } catch (error) {
    if (error && error.code === 'ENOENT') return []
    throw error
  }
}

async function waitFor(url, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {
      // The child server is still starting.
    }
    await new Promise(resolveWait => setTimeout(resolveWait, 150))
  }
  throw new Error(`timed out waiting for ${url}`)
}

async function waitForLabelCount(path, count, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const rows = await readLabelRows(path)
    if (rows.length === count) return rows
    await new Promise(resolveWait => setTimeout(resolveWait, 50))
  }
  throw new Error(`timed out waiting for ${count} persisted label rows`)
}

async function freePort() {
  return await new Promise((resolvePort, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (!address || typeof address === 'string') {
        server.close()
        reject(new Error('failed to allocate a local test port'))
        return
      }
      server.close(error => error ? reject(error) : resolvePort(address.port))
    })
  })
}

function startApi(port, artifactRoot) {
  const child = spawn('npm', ['run', 'dev:api'], {
    cwd: UI_ROOT,
    env: {
      ...process.env,
      PDF_LAB_API_PORT: String(port),
      PDF_LAB_ARTIFACTS_ROOT: artifactRoot,
      PDF_LAB_PUBLIC_ROOT: artifactRoot,
    },
    detached: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let output = ''
  child.stdout.on('data', chunk => { output += chunk })
  child.stderr.on('data', chunk => { output += chunk })
  return { child, output: () => output }
}

async function stop(child) {
  if (!child.killed) {
    try {
      process.kill(-child.pid, 'SIGTERM')
    } catch {
      // Process already exited.
    }
  }
  await new Promise(resolveWait => setTimeout(resolveWait, 200))
}

async function mountLiveCalls(artifactMount) {
  const mounted = []
  for (const expected of DOCUMENTS) {
    const source = join(EVIDENCE_CALLS_ROOT, expected.id, 'annotation_call.json')
    const bytes = await readFile(source)
    const payload = JSON.parse(bytes)
    assert.equal(payload.schema, 'pdf_oxide.annotation_call.v1')
    assert.equal(payload.items.length, expected.count)

    const reasonCounts = Object.fromEntries(
      payload.items.reduce((counts, item) => {
        counts.set(item.reason, (counts.get(item.reason) ?? 0) + 1)
        return counts
      }, new Map()),
    )
    assert.deepEqual(reasonCounts, expected.reasons)

    const destination = join(artifactMount, 'annotation-calls', expected.id, 'annotation_call.json')
    await mkdir(resolve(destination, '..'), { recursive: true })
    await cp(source, destination)
    const mountedBytes = await readFile(destination)
    assert.equal(sha256(mountedBytes), sha256(bytes), `${expected.id} mount differs from live source`)
    mounted.push({
      ...expected,
      source,
      destination,
      sha256: sha256(bytes),
      payload,
      url: `/artifacts/pdf-lab/annotation-calls/${expected.id}/annotation_call.json`,
    })
  }
  assert.equal(mounted.reduce((total, document) => total + document.payload.items.length, 0), TOTAL_ITEMS)
  return mounted
}

async function prepareTwoRealCalibrationRows(artifactMount) {
  const annotationCall = JSON.parse(await readFile(join(artifactMount, 'annotation_call.json'), 'utf8'))
  const extracted = JSON.parse(await readFile(join(artifactMount, 'extracted.json'), 'utf8'))
  const pageImageIndex = JSON.parse(await readFile(join(artifactMount, 'page_images_v1.json'), 'utf8'))
  const documentId = '1512.03385v1'
  const imageByPage = new Map(pageImageIndex.pages.map(entry => [entry.page, entry.page_image_refs[0]]))
  const candidates = annotationCall.items
    .filter(item => (
      item.kind === 'block'
      && Array.isArray(item.bbox)
      && typeof item.current_type === 'string'
      && typeof item.confidence === 'number'
      && imageByPage.has(item.page)
    ))
    .slice(0, 2)
  assert.equal(candidates.length, 2, 'round-2 arXiv annotation call lacks two real calibration candidates')

  const rows = candidates.map((item, index) => {
    const image = imageByPage.get(item.page)
    return {
      doc: documentId,
      quintile: index,
      page: item.page,
      bbox: normalizedTopLeftXyxy(item.bbox, image.width, image.height),
      type: item.current_type,
      confidence: item.confidence,
      text: item.text_excerpt ?? '',
      label: null,
      page_image_refs: [{
        href: image.href,
        sha256: image.sha256,
        page: item.page,
        width: image.width,
        height: image.height,
        pdf_sha256: annotationCall.pdf_sha256,
      }],
    }
  })
  const sampleLines = rows.map(row => JSON.stringify(row))
  assert.notEqual(sampleLines[0], sampleLines[1])

  const calibrationDir = join(artifactMount, 'calibration')
  const calibrationImagesDir = join(calibrationDir, 'page_images')
  await mkdir(calibrationImagesDir, { recursive: true })
  await rm(join(calibrationDir, 'labels_v1.jsonl'), { force: true })
  await writeFile(join(calibrationDir, 'sample_v1.jsonl'), `${sampleLines.join('\n')}\n`)

  const requestedPages = new Set(rows.map(row => row.page))
  const sourceManifest = extracted.metadata.page_images
  const images = sourceManifest.images.filter(image => requestedPages.has(image.page))
  assert.equal(images.length, requestedPages.size)
  for (const image of images) {
    await cp(
      join(artifactMount, 'page_images', image.filename),
      join(calibrationImagesDir, image.filename),
    )
  }
  await writeFile(join(calibrationDir, 'page_images_v1.json'), `${JSON.stringify({
    schema: 'pdf_oxide.calibration_page_images.v1',
    documents: {
      [documentId]: {
        ...sourceManifest,
        pdf_sha256: annotationCall.pdf_sha256,
        directory: 'page_images',
        images,
      },
    },
  }, null, 2)}\n`)

  return sampleLines.map((line, index) => ({
    item_sha: sha256(Buffer.from(line)),
    row: rows[index],
  }))
}

test('round 3 serves all four live queues at scale and writes two real arXiv labels through the server', { timeout: 90_000 }, async () => {
  assert.equal(TOTAL_ITEMS, 2161)
  assert.equal(TOTAL_CHAR_PARITY, 54)
  const artifactMount = await mkdtemp(join(tmpdir(), 'pdf-oxide-round3-live-mount-'))
  await cp(ROUND2_LIVE_ROOT, artifactMount, { recursive: true })
  await mkdir(SCREENSHOT_ROOT, { recursive: true })
  const mounted = await mountLiveCalls(artifactMount)
  const expectedCalibrationItems = await prepareTwoRealCalibrationRows(artifactMount)
  const labelsPath = join(artifactMount, 'calibration/labels_v1.jsonl')

  const port = await freePort()
  const api = startApi(port, artifactMount)
  let browser
  try {
    const origin = `http://127.0.0.1:${port}`
    await waitFor(`${origin}/api/pdf-lab/status`)

    const servedSampleResponse = await fetch(`${origin}/api/pdf-lab/calibration/sample`)
    const servedSample = await servedSampleResponse.json()
    assert.equal(servedSampleResponse.status, 200, JSON.stringify(servedSample))
    assert.equal(servedSample.items.length, 2)
    assert.deepEqual(
      servedSample.items.map(entry => entry.item_sha),
      expectedCalibrationItems.map(entry => entry.item_sha),
    )

    browser = await chromium.launch({ headless: true })
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    const queueParams = new URLSearchParams({
      calls: mounted.map(document => document.url).join(','),
      pageImages: '/artifacts/pdf-lab/page_images_v1.json',
    })
    await page.goto(`${origin}/#pdf-lab/annotations?${queueParams}`)
    const queueRoute = page.locator('[data-testid="annotation-queue-route"]')
    await queueRoute.waitFor()
    await assert.doesNotReject(page.getByText('2,161 engine-raised items', { exact: false }).waitFor())
    assert.match(await queueRoute.innerText(), /2,161 engine-raised items/)
    assert.match(await queueRoute.innerText(), /2,161 visible/)
    assert.equal(await page.locator('[data-confidence-hidden="true"]').count() > 0, true)
    assert.equal(await page.locator('[data-confidence-value], [data-confidence]:not([data-confidence-hidden])').count(), 0)
    assert.equal((await page.locator('body').innerHTML()).includes('0.44999998807907104'), false)

    const virtualList = page.locator('[data-testid="annotation-virtual-list"]')
    const initialRenderedRows = await page.locator('[data-testid="annotation-row"]').count()
    assert(initialRenderedRows < 60, `virtualization rendered ${initialRenderedRows} initial rows`)
    await page.screenshot({ path: join(SCREENSHOT_ROOT, 'queue-full.png'), fullPage: true })

    await virtualList.evaluate(element => {
      element.scrollTop = element.scrollHeight
      element.dispatchEvent(new Event('scroll', { bubbles: true }))
    })
    await page.waitForFunction(() => {
      const list = document.querySelector('[data-testid="annotation-virtual-list"]')
      return list instanceof HTMLElement && list.scrollTop > 0
    })
    const scrolledRenderedRows = await page.locator('[data-testid="annotation-row"]').count()
    assert(scrolledRenderedRows < 60, `virtualization rendered ${scrolledRenderedRows} rows while scrolled`)

    const documentFilter = page.getByRole('combobox', { name: 'Filter by document' })
    for (const document of DOCUMENTS) {
      await documentFilter.selectOption(document.id)
      await page.getByText(`${document.count.toLocaleString()} visible`, { exact: false }).waitFor()
      assert.match(await queueRoute.innerText(), new RegExp(`${document.count.toLocaleString()} visible`))
      assert.equal(await page.locator('[data-testid="annotation-row"]').count() < 60, true)
    }

    await documentFilter.selectOption('*')
    const reasonFilter = page.getByRole('combobox', { name: 'Filter by reason' })
    await reasonFilter.selectOption('char_parity_deficit')
    await page.getByText('54 visible', { exact: false }).waitFor()
    assert.match(await queueRoute.innerText(), /54 visible/)

    await documentFilter.selectOption('NIST.SP.800-53Ar5')
    await page.getByText('47 visible', { exact: false }).waitFor()
    assert.match(await queueRoute.innerText(), /47 visible/)
    assert.equal(await page.locator('[data-testid="annotation-row"]').count() < 60, true)
    await page.screenshot({ path: join(SCREENSHOT_ROOT, 'queue-filtered.png'), fullPage: true })

    await page.goto(`${origin}/#pdf-lab/calibrate`)
    const calibrateRoute = page.locator('[data-testid="calibrate-route"]')
    await calibrateRoute.waitFor()
    await page.locator('[data-testid="page-image"]').waitFor({ state: 'visible' })
    await page.locator('[data-testid="bbox-overlay"]').waitFor({ state: 'visible' })
    assert.equal((await calibrateRoute.innerText()).includes(String(expectedCalibrationItems[0].row.confidence)), false)
    assert.equal((await readLabelRows(labelsPath)).length, 0)

    await page.getByRole('button', { name: /Correct/ }).click()
    const firstRows = await waitForLabelCount(labelsPath, 1)
    assert.equal(firstRows.length, 1)
    assertLabelRow(firstRows[0], {
      item_sha: servedSample.items[0].item_sha,
      label: 'correct',
    })
    await page.getByLabel(/adjudicated/).filter({ hasText: '1/2' }).waitFor()

    const correctedType = 'figure'
    await page.getByLabel(/Corrected type/).fill(correctedType)
    await page.getByRole('button', { name: /Wrong type/ }).click()
    const finalRows = await waitForLabelCount(labelsPath, 2)
    assert.equal(finalRows.length, 2)
    assertLabelRow(finalRows[0], {
      item_sha: servedSample.items[0].item_sha,
      label: 'correct',
    })
    assertLabelRow(finalRows[1], {
      item_sha: servedSample.items[1].item_sha,
      label: 'wrong_type',
      corrected_type: correctedType,
    })
    await page.getByLabel(/adjudicated/).filter({ hasText: '2/2' }).waitFor()
    await page.screenshot({ path: join(SCREENSHOT_ROOT, 'calibrate-labeled.png'), fullPage: true })

    const labelsText = `${finalRows.map(row => JSON.stringify(row)).join('\n')}\n`
    await writeFile(join(SCREENSHOT_ROOT, 'labels_v1.jsonl'), labelsText)
    for (const screenshot of ['queue-full.png', 'queue-filtered.png', 'calibrate-labeled.png']) {
      assert((await stat(join(SCREENSHOT_ROOT, screenshot))).size > 10_000)
    }

    console.log(`queue total: ${TOTAL_ITEMS}`)
    console.log(`per-doc counts: ${JSON.stringify(Object.fromEntries(mounted.map(document => [document.id, document.count])))}`)
    console.log(`reason counts: ${JSON.stringify({ char_parity_deficit: TOTAL_CHAR_PARITY })}`)
    console.log(`mounted sources: ${JSON.stringify(Object.fromEntries(mounted.map(document => [document.id, {
      source: document.source,
      destination: document.destination,
      sha256: document.sha256,
    }])))}`)
    console.log(`virtual rows while scrolled: ${scrolledRenderedRows}`)
    console.log(`labels_v1.jsonl:\n${labelsText.trim()}`)
  } catch (error) {
    console.error('API output:', api.output())
    throw error
  } finally {
    if (browser) await browser.close()
    await stop(api.child)
    await rm(artifactMount, { recursive: true, force: true })
  }
})
