import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import net from 'node:net'

import { chromium } from 'playwright'

const UI_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)))
const SCREENSHOT_PATH = '/tmp/pdf-lab-calibrate-acceptance.png'

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

function start(command, args, env = {}) {
  const child = spawn(command, args, {
    cwd: UI_ROOT,
    env: { ...process.env, ...env },
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

test('calibrate route renders evidence and persists a labels_v1 row', { timeout: 60_000 }, async () => {
  const artifactRoot = await mkdtemp(join(tmpdir(), 'pdf-lab-calibrate-e2e-'))
  const calibrationDir = join(artifactRoot, 'calibration')
  const pageImageDir = join(calibrationDir, 'page_images')
  await mkdir(pageImageDir, { recursive: true })

  const sample = {
    doc: 'fixture-doc',
    quintile: 0,
    page: 2,
    bbox: [0.125, 0.25, 0.625, 0.75],
    type: 'table',
    confidence: 0.123456,
    text: 'Fixture table candidate',
    label: null,
  }
  const sampleLine = JSON.stringify(sample)
  const expectedItemSha = createHash('sha256').update(sampleLine).digest('hex')
  const png = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
    'base64',
  )
  const pdfSha = 'd'.repeat(64)
  const naming = 'sha256(canonical JSON of schema,pdf_sha256,page_index,dpi,format + NUL + PNG bytes)'
  const canonicalInputs = JSON.stringify({
    dpi: 150,
    format: 'png',
    page_index: 2,
    pdf_sha256: pdfSha,
    schema: 'pdf_oxide.page_image.v1',
  })
  const filename = `${createHash('sha256')
    .update(canonicalInputs)
    .update(Buffer.from([0]))
    .update(png)
    .digest('hex')}.png`
  const byteSha = createHash('sha256').update(png).digest('hex')
  await writeFile(join(calibrationDir, 'sample_v1.jsonl'), `${sampleLine}\n`)
  await writeFile(join(pageImageDir, filename), png)
  await writeFile(join(calibrationDir, 'page_images_v1.json'), JSON.stringify({
    schema: 'pdf_oxide.calibration_page_images.v1',
    documents: {
      'fixture-doc': {
        schema: 'pdf_oxide.page_image.v1',
        pdf_sha256: pdfSha,
        directory: 'page_images',
        dpi: 150,
        format: 'png',
        naming,
        images: [{ page: 2, filename, byte_sha256: byteSha }],
      },
    },
  }))

  const port = await freePort()
  const api = start('npm', ['run', 'dev:api'], {
    PDF_LAB_API_PORT: String(port),
    PDF_LAB_ARTIFACTS_ROOT: artifactRoot,
    PDF_LAB_PUBLIC_ROOT: artifactRoot,
  })
  let browser
  try {
    await waitFor(`http://127.0.0.1:${port}/api/pdf-lab/status`)
    browser = await chromium.launch({ headless: true })
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
    await page.goto(`http://127.0.0.1:${port}/#pdf-lab/calibrate`)

    const image = page.locator('[data-testid="page-image"]')
    await image.waitFor({ state: 'visible' })
    assert.equal(await page.locator('[data-confidence-hidden="true"]').count(), 1)
    assert.equal((await page.locator('body').innerText()).includes('0.123456'), false)

    const overlayStyle = await page.locator('[data-testid="bbox-overlay"]').evaluate(element => ({
      left: element.style.left,
      top: element.style.top,
      width: element.style.width,
      height: element.style.height,
    }))
    assert.deepEqual(overlayStyle, {
      left: '12.5%',
      top: '25%',
      width: '50%',
      height: '50%',
    })

    await page.screenshot({ path: SCREENSHOT_PATH, fullPage: true })
    await page.getByRole('button', { name: 'Correct', exact: true }).click()
    await page.getByRole('heading', { name: 'Calibration sample complete' }).waitFor()

    const rows = (await readFile(join(calibrationDir, 'labels_v1.jsonl'), 'utf-8'))
      .trim()
      .split('\n')
      .map(line => JSON.parse(line))
    assert.equal(rows.length, 1)
    assert.deepEqual(Object.keys(rows[0]).sort(), ['item_sha', 'label', 'ts'])
    assert.equal(rows[0].item_sha, expectedItemSha)
    assert.equal(rows[0].label, 'correct')
    assert.equal(new Date(rows[0].ts).toISOString(), rows[0].ts)

    await writeFile(join(calibrationDir, 'page_images_v1.json'), JSON.stringify({
      schema: 'pdf_oxide.calibration_page_images.v1',
      documents: {
        'fixture-doc': {
          schema: 'pdf_oxide.page_image.v1',
          pdf_sha256: pdfSha,
          directory: 'page_images',
          dpi: 150,
          format: 'png',
          naming,
          images: [{ page: 2, filename: `${'f'.repeat(64)}.png`, byte_sha256: byteSha }],
        },
      },
    }))
    const invalidManifestResponse = await fetch(`http://127.0.0.1:${port}/api/pdf-lab/calibration/sample`)
    assert.equal(invalidManifestResponse.status, 422)
    assert.equal((await invalidManifestResponse.json()).error, 'invalid_calibration_contract')

    console.log(`labels_v1 row: ${JSON.stringify(rows[0])}`)
    console.log(`screenshot: ${SCREENSHOT_PATH}`)
  } catch (error) {
    console.error('API output:', api.output())
    throw error
  } finally {
    if (browser) await browser.close()
    await stop(api.child)
    await rm(artifactRoot, { recursive: true, force: true })
  }
})
