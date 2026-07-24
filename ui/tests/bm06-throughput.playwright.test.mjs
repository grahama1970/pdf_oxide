import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { spawn } from 'node:child_process'
import { copyFile, cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import net from 'node:net'
import test from 'node:test'

import { chromium } from 'playwright'

const UI_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)))
const REPO_ROOT = resolve(UI_ROOT, '..')
const SOURCE_ARTIFACTS = join(REPO_ROOT, 'artifacts/pdf-lab')
const RECEIPT_ROOT = join(REPO_ROOT, 'artifacts/ux_roundtable')
const WORKLOAD_SIZE = 50

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

async function fixtureRoot(name) {
  const root = await mkdtemp(join(tmpdir(), `pdf-oxide-${name}-`))
  await cp(join(SOURCE_ARTIFACTS, 'annotation-calls'), join(root, 'annotation-calls'), { recursive: true })
  await copyFile(
    join(SOURCE_ARTIFACTS, 'annotation_queue_manifest_v1.json'),
    join(root, 'annotation_queue_manifest_v1.json'),
  )
  await copyFile(
    join(SOURCE_ARTIFACTS, 'document_mount_manifest_v1.json'),
    join(root, 'document_mount_manifest_v1.json'),
  )
  return root
}

function readJsonl(text) {
  return text.trim().split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line))
}

async function runWorkload(browser, kind, fixtureSha256, uiCommit) {
  const artifactsRoot = await fixtureRoot(kind)
  const port = await freePort()
  const origin = `http://127.0.0.1:${port}`
  const server = startServer(port, artifactsRoot)
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  const workloadId = `bm06-${kind}-50`
  try {
    await waitFor(`${origin}/api/pdf-lab/status`)
    const query = new URLSearchParams({
      workload: workloadId,
      fixtureHash: fixtureSha256,
      uiCommit,
    })
    await page.goto(`${origin}/#pdf-lab/annotations?${query}`)
    await page.locator('[data-testid="annotation-queue-route"]').waitFor()
    await page.getByLabel('Filter by reason').selectOption('char_parity_deficit')
    assert.equal(await page.locator('[data-testid="annotation-row"]').count() < 60, true)

    const firstRow = page.locator('[data-testid="annotation-row"]').first()
    await firstRow.click()
    await page.locator('.pdf-verify-contract-blocker.is-small').waitFor()
    assert.equal(await page.locator('[data-testid="annotation-accept"]').isDisabled(), true)
    assert.equal(await page.locator('[data-testid="annotation-defer"]').isDisabled(), true)
    assert.equal(await page.locator('[data-testid="annotation-save-type"]').isDisabled(), true)
    assert.equal(await page.locator('[data-testid="annotation-save-bounds"]').isDisabled(), true)

    await page.locator('[data-testid="annotation-row"]').nth(1).click()
    await page.locator('[data-testid="page-image"]').waitFor()
    const started = performance.now()
    for (let index = 0; index < WORKLOAD_SIZE; index += 1) {
      const decisionResponse = page.waitForResponse(response => (
        response.url().endsWith('/api/pdf-lab/annotation-decisions')
        && response.request().method() === 'POST'
      ))
      const timingResponse = page.waitForResponse(response => (
        response.url().endsWith('/api/pdf-lab/ux-timing-events')
        && response.request().method() === 'POST'
      ))
      if (kind === 'mixed' && index % 5 === 0) {
        if (index % 10 === 0) {
          await page.locator('[data-testid="annotation-corrected-type"]').selectOption('Table')
          await page.locator('[data-testid="annotation-save-type"]').click()
        } else {
          await page.locator('[data-testid="annotation-bound-x"]').fill('10')
          await page.locator('[data-testid="annotation-bound-y"]').fill('20')
          await page.locator('[data-testid="annotation-bound-width"]').fill('100')
          await page.locator('[data-testid="annotation-bound-height"]').fill('40')
          await page.locator('[data-testid="annotation-save-bounds"]').click()
        }
      } else if (index % 2 === 0) {
        await page.locator('[data-testid="annotation-accept"]').click()
      } else {
        await page.locator('[data-testid="annotation-defer"]').click()
      }
      assert.equal((await decisionResponse).status(), 201)
      assert.equal((await timingResponse).status(), 201)
      await page.locator('[data-testid="page-image"]').waitFor()
      if (index === 0) {
        assert.equal(await page.locator('[data-testid="queue-decision-badge"]').count() >= 1, true)
      }
    }
    const elapsedMs = performance.now() - started
    const itemsPerHour = WORKLOAD_SIZE * 3_600_000 / elapsedMs
    const decisions = readJsonl(await readFile(join(artifactsRoot, 'annotation_decisions_v1.jsonl'), 'utf-8'))
    const timings = readJsonl(await readFile(join(artifactsRoot, 'ux_timing_event_v1.jsonl'), 'utf-8'))
    assert.equal(decisions.length, WORKLOAD_SIZE)
    assert.equal(timings.length, WORKLOAD_SIZE)
    assert.equal(new Set(decisions.map(row => row.event_id)).size, WORKLOAD_SIZE)
    assert.equal(new Set(decisions.map(row => row.item_id)).size, WORKLOAD_SIZE)
    assert.equal(new Set(timings.map(row => row.event_id)).size, WORKLOAD_SIZE)
    assert.equal(timings.every(row => row.fixture_sha256 === fixtureSha256 && row.ui_commit === uiCommit), true)

    const selectedBeforeReload = await page.locator('[data-testid="annotation-queue-route"]').getAttribute('data-selected-id')
    await page.reload()
    await page.locator('[data-testid="annotation-queue-route"]').waitFor()
    assert.equal(await page.getByLabel('Filter by reason').inputValue(), 'char_parity_deficit')
    assert.equal(await page.locator('[data-testid="annotation-queue-route"]').getAttribute('data-selected-id'), selectedBeforeReload)
    assert.match(await page.locator('body').innerText(), /Saved (accept|defer|correct type|correct bounds)/)

    return {
      workload_id: workloadId,
      kind,
      item_count: WORKLOAD_SIZE,
      correction_count: kind === 'mixed' ? 10 : 0,
      elapsed_ms: elapsedMs,
      items_per_hour: itemsPerHour,
      decision_writes: decisions.length,
      timing_writes: timings.length,
      duplicate_event_ids: 0,
      dropped_writes: 0,
      threshold_items_per_hour: kind === 'mixed' ? 60 : 120,
      pass: itemsPerHour >= (kind === 'mixed' ? 60 : 120),
      timing_events: timings,
    }
  } catch (error) {
    throw new Error(`${error instanceof Error ? error.stack : String(error)}\nServer output:\n${server.output()}`)
  } finally {
    await context.close()
    await stopServer(server.child)
    await rm(artifactsRoot, { recursive: true, force: true })
  }
}

test('BM06 deterministic 50-item workloads meet both throughput tiers without dropped writes', { timeout: 180_000 }, async () => {
  const fixtureBytes = await readFile(join(SOURCE_ARTIFACTS, 'annotation_queue_manifest_v1.json'))
  const fixtureSha256 = createHash('sha256').update(fixtureBytes).digest('hex')
  const uiCommit = process.env.BM06_UI_COMMIT
    ?? execFileSync('git', ['rev-parse', 'HEAD'], { cwd: REPO_ROOT, encoding: 'utf-8' }).trim()
  const browser = await chromium.launch({ headless: true })
  try {
    const acceptDefer = await runWorkload(browser, 'accept-defer', fixtureSha256, uiCommit)
    const mixed = await runWorkload(browser, 'mixed', fixtureSha256, uiCommit)
    assert.equal(acceptDefer.pass, true)
    assert.equal(mixed.pass, true)
    await mkdir(RECEIPT_ROOT, { recursive: true })
    const allEvents = [...acceptDefer.timing_events, ...mixed.timing_events]
    await writeFile(
      join(RECEIPT_ROOT, 'ux_timing_event_v1.jsonl'),
      `${allEvents.map(row => JSON.stringify(row)).join('\n')}\n`,
    )
    const receipt = {
      schema: 'pdf_oxide.ux_throughput_receipt.v1',
      generated_at: new Date().toISOString(),
      fixture_sha256: fixtureSha256,
      ui_commit: uiCommit,
      workload_size: WORKLOAD_SIZE,
      workloads: [
        { ...acceptDefer, timing_events: undefined },
        { ...mixed, timing_events: undefined },
      ],
      totals: {
        decision_writes: acceptDefer.decision_writes + mixed.decision_writes,
        timing_writes: acceptDefer.timing_writes + mixed.timing_writes,
        duplicate_event_ids: 0,
        dropped_writes: 0,
      },
      pass: true,
    }
    await writeFile(
      join(RECEIPT_ROOT, 'BM06_THROUGHPUT_RECEIPT.json'),
      `${JSON.stringify(receipt, null, 2)}\n`,
    )
    console.log(JSON.stringify(receipt))
  } finally {
    await browser.close()
  }
})
