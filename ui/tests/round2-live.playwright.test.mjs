import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { cp, mkdir, mkdtemp, readFile, rm, stat } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import net from 'node:net'

import { chromium } from 'playwright'
import { prepareRound2LiveArtifacts } from './prepare-round2-live-artifacts.mjs'

const UI_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)))
const REPO_ROOT = resolve(UI_ROOT, '..')
const LIVE_SOURCE_ROOT = resolve(REPO_ROOT, 'artifacts/ux_competition/round2/live')
const SCREENSHOT_ROOT = resolve(REPO_ROOT, 'artifacts/ux_competition/round2')

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

test('round 2 drives queue, calibration, retrieval, and missing-image fail-closed against current-engine artifacts', { timeout: 90_000 }, async () => {
  const receipt = await prepareRound2LiveArtifacts(LIVE_SOURCE_ROOT)
  assert.equal(receipt.pdf_sha256, '1e0651b6810ecba34a3dbc5b5b0209226f889004607c1f203540a48d64e5a93a')
  assert.equal(receipt.page_count, 12)
  assert.equal(receipt.annotation_item_count, 17)
  assert.deepEqual(receipt.annotation_reason_counts, { low_confidence: 17 })

  const artifactMount = await mkdtemp(join(tmpdir(), 'pdf-oxide-round2-live-mount-'))
  await cp(LIVE_SOURCE_ROOT, artifactMount, { recursive: true })
  await mkdir(SCREENSHOT_ROOT, { recursive: true })

  const port = await freePort()
  const api = startApi(port, artifactMount)
  let browser
  try {
    const origin = `http://127.0.0.1:${port}`
    await waitFor(`${origin}/api/pdf-lab/status`)
    const validation = await fetch(`${origin}/api/pdf-lab/calibration/sample`)
    const validated = await validation.json()
    assert.equal(validation.status, 200, JSON.stringify(validated))
    assert.equal(validated.items.length, 1)
    assert.equal(validated.page_images.documents['1512.03385v1'].pdf_sha256, receipt.pdf_sha256)

    browser = await chromium.launch({ headless: true })
    let context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    let page = await context.newPage()

    await page.goto(
      `${origin}/#pdf-lab/annotations?calls=/artifacts/pdf-lab/annotation_call.json&pageImages=/artifacts/pdf-lab/page_images_v1.json`,
    )
    await page.locator('[data-testid="annotation-queue-route"]').waitFor()
    await page.locator('[data-testid="page-image"]').waitFor({ state: 'visible' })
    await page.locator('[data-testid="bbox-overlay"]').waitFor({ state: 'visible' })
    assert.match(await page.locator('body').innerText(), /17 engine-raised items/)
    assert.match(await page.locator('body').innerText(), /Low-confidence classification/)
    assert.equal((await page.locator('body').innerText()).includes('0.44999998807907104'), false)
    assert.equal(await page.locator('[data-confidence-hidden="true"]').count() > 0, true)
    await page.screenshot({ path: join(SCREENSHOT_ROOT, 'queue.png'), fullPage: true })

    await page.goto(`${origin}/#pdf-lab/calibrate`)
    await page.locator('[data-testid="calibrate-route"]').waitFor()
    await page.locator('[data-testid="page-image"]').waitFor({ state: 'visible' })
    const calibrationOverlay = page.locator('[data-testid="bbox-overlay"]')
    await calibrationOverlay.waitFor({ state: 'visible' })
    const calibrationBox = await calibrationOverlay.boundingBox()
    assert(calibrationBox && calibrationBox.width > 5 && calibrationBox.height > 5)
    assert.equal((await page.locator('body').innerText()).includes('0.44999998807907104'), false)
    await page.screenshot({ path: join(SCREENSHOT_ROOT, 'calibrate.png'), fullPage: true })
    await page.getByRole('button', { name: /Correct/ }).click()
    await page.getByRole('status').filter({ hasText: 'Saved correct' }).waitFor()

    const retrievalUrl = `${origin}/#pdf-lab/evidence?result=/artifacts/pdf-lab/retrieval_result.json&pageImages=/artifacts/pdf-lab/page_images_v1.json&tree=/artifacts/pdf-lab/section_tree.json`
    await page.goto(retrievalUrl)
    await page.locator('[data-testid="page-image"]').waitFor({ state: 'visible' })
    await page.locator('[data-testid="bbox-overlay"]').waitFor({ state: 'visible' })
    assert.match(await page.locator('[data-testid="section-breadcrumb"]').first().innerText(), /Deep Residual Learning/)
    assert.match(await page.locator('[data-testid="provenance-chain"]').innerText(), new RegExp(receipt.retrieval_element_id))
    assert.match(await page.locator('[data-testid="provenance-chain"]').innerText(), new RegExp(receipt.pdf_sha256))
    await page.setViewportSize({ width: 1440, height: 2200 })
    await page.screenshot({ path: join(SCREENSHOT_ROOT, 'retrieval.png'), fullPage: true })

    await context.close()
    await rm(join(artifactMount, 'page_images', receipt.retrieval_image_filename))
    await assert.rejects(stat(join(artifactMount, 'page_images', receipt.retrieval_image_filename)))

    context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    page = await context.newPage()
    await page.goto(retrievalUrl)
    const imageError = page.locator('[data-testid="page-image-error"]')
    await imageError.waitFor({ state: 'visible' })
    assert.match(await imageError.innerText(), /failed closed/i)
    assert.equal(await page.locator('[data-testid="page-image"]').count(), 0)
    await page.screenshot({ path: join(SCREENSHOT_ROOT, 'fail-closed.png'), fullPage: true })
    await context.close()

    const screenshotSizes = {}
    for (const name of ['queue.png', 'calibrate.png', 'retrieval.png', 'fail-closed.png']) {
      const info = await stat(join(SCREENSHOT_ROOT, name))
      assert(info.size > 10_000, `${name} is unexpectedly small`)
      screenshotSizes[name] = info.size
    }
    const labels = (await readFile(join(artifactMount, 'calibration/labels_v1.jsonl'), 'utf8')).trim()
    assert(labels, 'live calibration decision was not persisted')
    console.log(`live receipt: ${JSON.stringify(receipt)}`)
    console.log(`screenshots: ${JSON.stringify(screenshotSizes)}`)
    console.log(`labels_v1 row: ${labels}`)
  } catch (error) {
    console.error('API output:', api.output())
    throw error
  } finally {
    if (browser) await browser.close()
    await stop(api.child)
    await rm(artifactMount, { recursive: true, force: true })
  }
})
