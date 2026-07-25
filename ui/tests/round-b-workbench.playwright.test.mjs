import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import net from 'node:net'
import test from 'node:test'

import { chromium } from 'playwright'

const UI_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)))
const REPO_ROOT = resolve(UI_ROOT, '..')
const SCREENSHOT_ROOT = join(REPO_ROOT, 'artifacts/ux_roundtable/workbench')

async function freePort() {
  return await new Promise((resolvePort, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (!address || typeof address === 'string') return reject(new Error('no free port'))
      server.close((error) => error ? reject(error) : resolvePort(address.port))
    })
  })
}

async function waitFor(url, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {
      // Server is still starting.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 100))
  }
  throw new Error(`timed out waiting for ${url}`)
}

function startServer(port) {
  return spawn('npm', ['run', 'dev:api'], {
    cwd: UI_ROOT,
    env: {
      ...process.env,
      PDF_LAB_API_PORT: String(port),
      PDF_LAB_ARTIFACTS_ROOT: join(REPO_ROOT, 'artifacts/pdf-lab'),
      PDF_LAB_PUBLIC_ROOT: join(REPO_ROOT, 'artifacts/pdf-lab'),
    },
    detached: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
}

async function stopServer(child) {
  if (!child.killed) {
    try {
      process.kill(-child.pid, 'SIGTERM')
    } catch {
      // Process already exited.
    }
  }
  await new Promise((resolveWait) => setTimeout(resolveWait, 200))
}

test('Round B workbench supports lifted zoom, pan, full-page, draw, evidence, and collapsed rail', { timeout: 90_000 }, async () => {
  await mkdir(SCREENSHOT_ROOT, { recursive: true })
  const port = await freePort()
  const origin = `http://127.0.0.1:${port}`
  const server = startServer(port)
  let output = ''
  server.stdout.on('data', (chunk) => { output += chunk })
  server.stderr.on('data', (chunk) => { output += chunk })
  let browser
  try {
    await waitFor(`${origin}/api/pdf-lab/status`)
    browser = await chromium.launch({ headless: true })
    const context = await browser.newContext({ viewport: { width: 1800, height: 1100 } })
    const page = await context.newPage()
    const query = new URLSearchParams({
      calls: '/artifacts/pdf-lab/annotation-calls/1512.03385v1/annotation_call.json',
      pageImages: '/artifacts/pdf-lab/annotation-calls/1512.03385v1/page_images_v1.json',
    })
    await page.goto(`${origin}/#pdf-lab/annotations?${query}`)
    await page.locator('[data-testid="annotation-queue-route"]').waitFor()
    await page.getByLabel('Filter by reason').selectOption('char_parity_deficit')
    await page.locator('[data-testid="char-parity-evidence"]').waitFor()
    await page.locator('[data-testid="page-image"]').waitFor()

    assert.equal(await page.locator('[data-testid="annotation-corrected-text"]').isVisible(), true)
    assert.match(await page.locator('[data-testid="missing-text-highlight"]').innerText(), /×/)
    assert.equal(await page.locator('[data-testid="char-parity-evidence"] mark').filter({ hasText: '×' }).count() > 0, true)
    assert.equal(await page.locator('[data-testid="engine-attribution"]').innerText(), 'pdf-oxide 0.3.14')
    await page.screenshot({ path: join(SCREENSHOT_ROOT, '01-arxiv-p4-char-parity.png'), fullPage: true })
    await page.screenshot({ path: join(SCREENSHOT_ROOT, '02-fix-text-open.png'), fullPage: true })

    const interactive = page.locator(
      '[data-testid="annotation-queue-route"] button, [data-testid="annotation-queue-route"] input, '
      + '[data-testid="annotation-queue-route"] select, [data-testid="annotation-queue-route"] textarea',
    )
    const compliance = await interactive.evaluateAll((elements) => elements.map((element) => ({
      qid: element.getAttribute('data-qid'),
      action: element.getAttribute('data-qs-action'),
      title: element.getAttribute('title'),
      name: element.getAttribute('aria-label') || element.textContent?.trim(),
    })))
    assert.equal(compliance.every((row) => row.qid && row.action && row.title && row.name), true)

    const canvas = page.locator('[data-testid="pdf-document-canvas"]')
    const initialZoom = Number(await canvas.getAttribute('data-zoom'))
    await page.getByLabel('Zoom in').click()
    assert.equal(Number(await canvas.getAttribute('data-zoom')) > initialZoom, true)
    await page.getByLabel('Fit page width').click()
    assert.equal(await canvas.getAttribute('data-fit-mode'), 'width')

    await page.getByLabel('Fix element bounds').click()
    await canvas.scrollIntoViewIfNeeded()
    const pageImage = page.locator('[data-testid="page-image"]')
    const imageBox = await pageImage.boundingBox()
    assert(imageBox)
    await page.mouse.move(imageBox.x + 40, imageBox.y + 60)
    await page.mouse.down()
    await page.mouse.move(imageBox.x + 190, imageBox.y + 170, { steps: 8 })
    await page.locator('[data-testid="bbox-draw-preview"]').waitFor()
    await page.screenshot({ path: join(SCREENSHOT_ROOT, '03-fix-bounds-mid-drag.png'), fullPage: true })
    await page.mouse.up()

    await page.locator('[data-testid="annotation-rail-toggle"]').click()
    assert.equal(await page.locator('.pdf-verify-adjudication__layout').getAttribute('class'), 'pdf-verify-adjudication__layout is-rail-collapsed')
    await page.screenshot({ path: join(SCREENSHOT_ROOT, '04-rail-collapsed.png'), fullPage: true })

    await page.getByLabel('Zoom in').click()
    await page.getByLabel('Zoom in').click()
    await page.getByLabel('Toggle canvas pan tool').click()
    assert.equal(await canvas.getAttribute('data-pan-mode'), 'true')
    const viewport = page.locator('[data-testid="pdf-document-canvas-viewport"]')
    const viewportBox = await viewport.boundingBox()
    assert(viewportBox)
    await viewport.evaluate((element) => {
      element.scrollLeft = 120
      element.scrollTop = 160
    })
    const beforePan = await viewport.evaluate((element) => [element.scrollLeft, element.scrollTop])
    await page.mouse.move(viewportBox.x + viewportBox.width / 2, viewportBox.y + viewportBox.height / 2)
    await page.mouse.down()
    await page.mouse.move(viewportBox.x + viewportBox.width / 2 - 80, viewportBox.y + viewportBox.height / 2 - 70, { steps: 6 })
    await page.mouse.up()
    const afterPan = await viewport.evaluate((element) => [element.scrollLeft, element.scrollTop])
    assert.notDeepEqual(afterPan, beforePan)
    await page.screenshot({ path: join(SCREENSHOT_ROOT, '05-zoomed-canvas.png'), fullPage: true })

    await page.locator('[data-testid="annotation-rail-toggle"]').click()
    await page.getByLabel('Filter by reason').selectOption('reviewer_flagged')
    await page.locator('[data-testid="annotation-row"]').first().click()
    await page.locator('[data-testid="page-image"]').waitFor()
    assert.equal(await page.locator('[data-testid="annotation-accept"]').isDisabled(), true)

    await context.close()
  } catch (error) {
    throw new Error(`${error instanceof Error ? error.stack : String(error)}\nServer output:\n${output}`)
  } finally {
    await browser?.close()
    await stopServer(server)
  }
})
