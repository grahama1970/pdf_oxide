import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import net from 'node:net'
import test from 'node:test'

import { chromium } from 'playwright'

const UI_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)))

async function freePort() {
  return await new Promise((resolvePort, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (!address || typeof address === 'string') return reject(new Error('no free port'))
      server.close(error => error ? reject(error) : resolvePort(address.port))
    })
  })
}

async function waitFor(url) {
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    try {
      if ((await fetch(url)).ok) return
    } catch {
      // Server is starting.
    }
    await new Promise(resolveWait => setTimeout(resolveWait, 100))
  }
  throw new Error(`timed out waiting for ${url}`)
}

function startServer(port, artifactsRoot) {
  const child = spawn('npm', ['run', 'dev:api'], {
    cwd: UI_ROOT,
    env: {
      ...process.env,
      PDF_LAB_API_PORT: String(port),
      PDF_LAB_ARTIFACTS_ROOT: artifactsRoot,
      PDF_LAB_PUBLIC_ROOT: artifactsRoot,
    },
    detached: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let output = ''
  child.stdout.on('data', chunk => { output += chunk })
  child.stderr.on('data', chunk => { output += chunk })
  return { child, output: () => output }
}

async function stopServer(child) {
  try {
    process.kill(-child.pid, 'SIGTERM')
  } catch {
    // Already stopped.
  }
  await new Promise(resolveWait => setTimeout(resolveWait, 200))
}

test('BM02 resumes after 50, skips without a write, and undo restores the prior label', { timeout: 120_000 }, async () => {
  const artifactsRoot = await mkdtemp(join(tmpdir(), 'pdf-oxide-bm02-'))
  const calibrationDir = join(artifactsRoot, 'calibration')
  const pageImagesDir = join(calibrationDir, 'page_images')
  await mkdir(pageImagesDir, { recursive: true })
  const rows = Array.from({ length: 51 }, (_, index) => ({
    doc: 'bm02-fixture',
    quintile: index % 5,
    page: 0,
    bbox: [0.1, 0.1, 0.4, 0.4],
    type: 'Body',
    confidence: (index + 1) / 100,
    text: `Calibration item ${index + 1}`,
    label: null,
  }))
  const lines = rows.map(row => JSON.stringify(row))
  const itemShas = lines.map(line => createHash('sha256').update(line).digest('hex'))
  await writeFile(join(calibrationDir, 'sample_v1.jsonl'), `${lines.join('\n')}\n`)
  const png = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
    'base64',
  )
  const pdfSha = 'd'.repeat(64)
  const canonicalInputs = JSON.stringify({
    dpi: 150,
    format: 'png',
    page_index: 0,
    pdf_sha256: pdfSha,
    schema: 'pdf_oxide.page_image.v1',
  })
  const filename = `${createHash('sha256').update(canonicalInputs).update(Buffer.from([0])).update(png).digest('hex')}.png`
  await writeFile(join(pageImagesDir, filename), png)
  await writeFile(join(calibrationDir, 'page_images_v1.json'), JSON.stringify({
    schema: 'pdf_oxide.calibration_page_images.v1',
    documents: {
      'bm02-fixture': {
        schema: 'pdf_oxide.page_image.v1',
        pdf_sha256: pdfSha,
        directory: 'page_images',
        dpi: 150,
        format: 'png',
        naming: 'sha256(canonical JSON of schema,pdf_sha256,page_index,dpi,format + NUL + PNG bytes)',
        images: [{
          page: 0,
          filename,
          byte_sha256: createHash('sha256').update(png).digest('hex'),
        }],
      },
    },
  }))

  const port = await freePort()
  const origin = `http://127.0.0.1:${port}`
  const server = startServer(port, artifactsRoot)
  let browser
  try {
    await waitFor(`${origin}/api/pdf-lab/status`)
    browser = await chromium.launch({ headless: true })
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    const page = await context.newPage()
    await page.goto(`${origin}/#pdf-lab/calibrate`)
    const route = page.locator('[data-testid="calibrate-route"]')
    await route.waitFor()

    for (let index = 0; index < 50; index += 1) {
      await page.locator(`[data-current-item-sha="${itemShas[index]}"]`).waitFor()
      const responsePromise = page.waitForResponse(response => (
        response.url().endsWith('/api/pdf-lab/calibration/events')
        && response.request().method() === 'POST'
      ))
      await page.getByRole('button', { name: /Correct/ }).click()
      assert.equal((await responsePromise).status(), 201)
    }
    await page.locator(`[data-current-item-sha="${itemShas[50]}"]`).waitFor()
    assert.equal((await readFile(join(calibrationDir, 'events_v1.jsonl'), 'utf-8')).trim().split('\n').length, 50)

    await page.evaluate(() => {
      localStorage.clear()
      sessionStorage.clear()
    })
    await context.clearCookies()
    await page.reload()
    await page.locator(`[data-current-item-sha="${itemShas[50]}"]`).waitFor()
    assert.doesNotMatch(await route.evaluate(element => element.outerHTML), /0\.51/)

    await page.getByRole('button', { name: 'Previous calibration item' }).click()
    await page.locator(`[data-current-item-sha="${itemShas[49]}"]`).waitFor()
    const correctionResponsePromise = page.waitForResponse(response => (
      response.url().endsWith('/api/pdf-lab/calibration/events')
      && response.request().method() === 'POST'
    ))
    await page.getByRole('button', { name: /Wrong bounds/ }).click()
    const correctionResponse = await correctionResponsePromise
    assert.equal(correctionResponse.status(), 201, await correctionResponse.text())
    await page.waitForTimeout(100)
    if (await route.getAttribute('data-current-item-sha') !== itemShas[49]) {
      await page.getByRole('button', { name: 'Previous calibration item' }).click()
      await page.locator(`[data-current-item-sha="${itemShas[49]}"]`).waitFor()
    }
    const undoResponsePromise = page.waitForResponse(response => (
      response.url().endsWith('/api/pdf-lab/calibration/events')
      && response.request().method() === 'POST'
    ))
    await page.locator('[data-testid="calibration-undo"]').click()
    const undoResponse = await undoResponsePromise
    assert.equal(undoResponse.status(), 201, await undoResponse.text())
    await page.waitForTimeout(100)
    if (await route.getAttribute('data-current-item-sha') === itemShas[49]) {
      await page.getByRole('button', { name: 'Next calibration item' }).click()
    }
    await page.locator(`[data-current-item-sha="${itemShas[50]}"]`).waitFor()
    await page.locator('[data-testid="calibration-skip"]').click()
    await page.getByRole('status').filter({ hasText: 'Skipped without writing' }).waitFor()

    const labels = (await readFile(join(calibrationDir, 'labels_v1.jsonl'), 'utf-8'))
      .trim()
      .split('\n')
      .map(line => JSON.parse(line))
    assert.equal(labels.length, 50)
    assert.equal(labels.find(row => row.item_sha === itemShas[49]).label, 'correct')
    const events = (await readFile(join(calibrationDir, 'events_v1.jsonl'), 'utf-8')).trim().split('\n')
    assert.equal(events.length, 52)
  } catch (error) {
    throw new Error(`${error instanceof Error ? error.stack : String(error)}\nServer output:\n${server.output()}`)
  } finally {
    await browser?.close()
    await stopServer(server.child)
    await rm(artifactsRoot, { recursive: true, force: true })
  }
})
