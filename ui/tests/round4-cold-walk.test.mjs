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
const SCREENSHOT_ROOT = join(REPO_ROOT, 'artifacts/ux_competition/round4')
const ROUND6_SCREENSHOT_ROOT = join(REPO_ROOT, 'artifacts/ux_competition/round6')
const TRUE_TOTAL = 2_161
const RAW_PARSE_ERROR = /Unexpected token|JSON\.parse|SyntaxError|is not valid JSON/i

async function waitFor(url, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs
  let latest
  while (Date.now() < deadline) {
    try {
      latest = await fetch(url)
      if (latest.ok) return latest
    } catch {
      // Server is still starting.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 100))
  }
  throw new Error(`${url} did not become ready (last status ${latest?.status ?? 'unreachable'})`)
}

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
  if (child.killed) return
  try {
    process.kill(-child.pid, 'SIGTERM')
  } catch {
    // Process already exited.
  }
  await new Promise((resolveWait) => setTimeout(resolveWait, 200))
}

async function assertNoRawParseError(page) {
  const body = await page.locator('body').innerText()
  assert.doesNotMatch(body, RAW_PARSE_ERROR)
}

test('round 4 cold walk discovers the front-door artifact mounts', { timeout: 120_000 }, async () => {
  await mkdir(SCREENSHOT_ROOT, { recursive: true })
  await mkdir(ROUND6_SCREENSHOT_ROOT, { recursive: true })
  const port = await freePort()
  const origin = `http://127.0.0.1:${port}`
  const server = startServer(port)
  let serverOutput = ''
  server.stdout.on('data', (chunk) => { serverOutput += chunk })
  server.stderr.on('data', (chunk) => { serverOutput += chunk })
  let browser
  try {
    const mountsResponse = await waitFor(`${origin}/api/pdf-lab/mounts`)
    const mounts = await mountsResponse.json()
    assert.equal(mounts.annotation_calls.length, 4)
    assert.equal(
      mounts.annotation_calls.reduce((total, call) => total + call.item_count, 0),
      TRUE_TOTAL,
    )
    assert.deepEqual(
      Object.fromEntries(mounts.page_image_indexes
        .filter((entry) => entry.url.includes('/annotation-calls/'))
        .map((entry) => [entry.document_ids[0], entry.page_count])),
      {
        'NASA_SP-2016-6105': 195,
        'NIST.SP.800-53Ar5': 88,
        'NIST_SP_800-53r5': 363,
      },
    )
    assert.equal(mounts.retrieval_results.length, 1)
    assert.match(mounts.retrieval_results[0].url, /^\/api\/pdf-lab\/retrieval-answers\//)
    assert.match(
      mounts.retrieval_results[0].page_image_index_url,
      /annotation-calls\/NIST_SP_800-53r5\/page_images_v1\.json$/,
    )

    browser = await chromium.launch({ headless: true })
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    const page = await context.newPage()

    await page.goto(`${origin}/`)
    const queue = page.locator('[data-testid="annotation-queue-route"]')
    await queue.waitFor()
    await assertNoRawParseError(page)
    await assert.doesNotReject(async () => {
      await page.getByText(`${TRUE_TOTAL.toLocaleString()} engine-raised items`, { exact: false }).waitFor()
    })
    await page.screenshot({ path: join(SCREENSHOT_ROOT, 'bare-root.png'), fullPage: true })

    await page.goto(`${origin}/#pdf-lab/calibrate`)
    const calibrate = page.locator('[data-testid="calibrate-route"]')
    await calibrate.waitFor()
    await calibrate.locator('img').waitFor()
    await assertNoRawParseError(page)
    await page.screenshot({ path: join(SCREENSHOT_ROOT, 'calibrate.png'), fullPage: true })

    await page.goto(`${origin}/#pdf-lab/annotations`)
    await page.locator('[data-testid="annotation-queue-route"]').waitFor()
    await page.getByText(`${TRUE_TOTAL.toLocaleString()} engine-raised items`, { exact: false }).waitFor()
    await assertNoRawParseError(page)
    await page.screenshot({ path: join(SCREENSHOT_ROOT, 'annotations.png'), fullPage: true })

    await page.goto(`${origin}/#pdf-lab/evidence`)
    await page.getByRole('heading', { name: 'Traceable answer' }).waitFor()
    await page.locator('[data-testid="evidence-group"] img').waitFor()
    await assertNoRawParseError(page)
    assert.doesNotMatch(
      await page.locator('body').innerText(),
      /Retrieval evidence withheld|Retrieval evidence needs attention/,
    )
    await page.screenshot({ path: join(SCREENSHOT_ROOT, 'evidence.png'), fullPage: true })
    await page.screenshot({ path: join(ROUND6_SCREENSHOT_ROOT, 'evidence-bare.png'), fullPage: true })

    await page.goto(`${origin}/#unknown-route`)
    await page.locator('[data-testid="annotation-queue-route"]').waitFor()
    await assertNoRawParseError(page)

    await context.close()
  } catch (error) {
    throw new Error(`${error instanceof Error ? error.stack : String(error)}\nServer output:\n${serverOutput}`)
  } finally {
    await browser?.close()
    await stopServer(server)
  }
})
