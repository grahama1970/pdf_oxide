import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { spawn } from 'node:child_process'
import { copyFile, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import net from 'node:net'
import test from 'node:test'

const UI_ROOT = resolve(fileURLToPath(new URL('..', import.meta.url)))
const REPO_ROOT = resolve(UI_ROOT, '..')

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
      const response = await fetch(url)
      if (response.ok) return
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

async function postJson(origin, path, body) {
  const response = await fetch(`${origin}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return { response, payload: await response.json() }
}

test('BM01/BM03/BM04/BM07 APIs enforce durable separated contracts', { timeout: 120_000 }, async () => {
  const artifactsRoot = await mkdtemp(join(tmpdir(), 'pdf-oxide-before-main-api-'))
  const calibrationDir = join(artifactsRoot, 'calibration')
  await mkdir(calibrationDir, { recursive: true })
  await symlink(join(REPO_ROOT, 'artifacts/pdf-lab/annotation-calls'), join(artifactsRoot, 'annotation-calls'))
  await copyFile(
    join(REPO_ROOT, 'artifacts/pdf-lab/annotation_queue_manifest_v1.json'),
    join(artifactsRoot, 'annotation_queue_manifest_v1.json'),
  )
  await copyFile(
    join(REPO_ROOT, 'artifacts/pdf-lab/document_mount_manifest_v1.json'),
    join(artifactsRoot, 'document_mount_manifest_v1.json'),
  )

  const samples = [
    { doc: 'fixture', quintile: 0, page: 0, bbox: [0.1, 0.1, 0.2, 0.2], type: 'Body', confidence: 0.1, text: 'one', label: null },
    { doc: 'fixture', quintile: 1, page: 1, bbox: [0.2, 0.2, 0.3, 0.3], type: 'Title', confidence: 0.2, text: 'two', label: null },
  ]
  const lines = samples.map(value => JSON.stringify(value))
  const itemShas = lines.map(line => createHash('sha256').update(line).digest('hex'))
  await writeFile(join(calibrationDir, 'sample_v1.jsonl'), `${lines.join('\n')}\n`)

  const port = await freePort()
  const origin = `http://127.0.0.1:${port}`
  const server = startServer(port, artifactsRoot)
  try {
    await waitFor(`${origin}/api/pdf-lab/status`)

    const queue = await (await fetch(`${origin}/api/pdf-lab/annotation-queue`)).json()
    assert.deepEqual(queue.counts, {
      total: 2161,
      char_parity_deficit: 54,
      reviewer_flagged: 5,
      low_confidence: 2102,
    })
    assert.deepEqual(queue.priority_order, [
      'char_parity_deficit',
      'reviewer_flagged',
      'low_confidence',
    ])
    assert.equal(queue.calls.flatMap(call => call.items).length, 2161)
    assert.deepEqual(
      Object.fromEntries(
        Object.entries(Object.groupBy(queue.calls.flatMap(call => call.items), item => item.reason))
          .map(([reason, rows]) => [reason, rows.length]),
      ),
      { reviewer_flagged: 5, char_parity_deficit: 54, low_confidence: 2102 },
    )
    assert.deepEqual(
      queue.calls.flatMap(call => call.items).slice(0, 59).map(item => item.reason),
      [...Array(54).fill('char_parity_deficit'), ...Array(5).fill('reviewer_flagged')],
    )

    const firstLabel = {
      idempotency_key: 'calibration:fixture:first',
      action: 'label',
      item_sha: itemShas[0],
      label: 'correct',
      ts: '2026-07-24T20:00:00.000Z',
    }
    const created = await postJson(origin, '/api/pdf-lab/calibration/events', firstLabel)
    assert.equal(created.response.status, 201)
    const duplicate = await postJson(origin, '/api/pdf-lab/calibration/events', firstLabel)
    assert.equal(duplicate.response.status, 200)
    assert.equal(duplicate.payload.duplicate, true)
    assert.equal((await readFile(join(calibrationDir, 'events_v1.jsonl'), 'utf-8')).trim().split('\n').length, 1)

    const unknown = await postJson(origin, '/api/pdf-lab/calibration/events', {
      ...firstLabel,
      idempotency_key: 'calibration:fixture:unknown',
      item_sha: 'f'.repeat(64),
    })
    assert.equal(unknown.response.status, 409)
    assert.equal(unknown.payload.error, 'unknown_or_stale_item_sha')

    const missingRevision = await postJson(origin, '/api/pdf-lab/calibration/events', {
      ...firstLabel,
      idempotency_key: 'calibration:fixture:missing-revision',
      label: 'wrong_bounds',
      ts: '2026-07-24T20:00:01.000Z',
    })
    assert.equal(missingRevision.response.status, 409)
    assert.equal(missingRevision.payload.error, 'calibration_amendment_requires_revision_of')

    const corrected = await postJson(origin, '/api/pdf-lab/calibration/events', {
      ...firstLabel,
      idempotency_key: 'calibration:fixture:correction',
      label: 'wrong_bounds',
      revision_of: created.payload.event.event_id,
      ts: '2026-07-24T20:00:02.000Z',
    })
    assert.equal(corrected.response.status, 201)
    const undone = await postJson(origin, '/api/pdf-lab/calibration/events', {
      idempotency_key: 'calibration:fixture:undo',
      action: 'undo',
      item_sha: itemShas[0],
      revision_of: corrected.payload.event.event_id,
      ts: '2026-07-24T20:00:03.000Z',
    })
    assert.equal(undone.response.status, 201)
    assert.equal(undone.payload.labels[0].label, 'correct')
    assert.equal(undone.payload.cursor.item_sha, itemShas[1])
    assert.equal(undone.payload.labels.length, 1)

    const queueItem = queue.calls.flatMap(call => call.items)[0]
    const decision = {
      idempotency_key: 'annotation:fixture:first',
      item_id: queueItem.item_id,
      item_sha256: queueItem.item_sha256,
      call_sha256: queueItem.call_sha256,
      decision: 'accept',
      ts: '2026-07-24T20:01:00.000Z',
    }
    const decisionCreated = await postJson(origin, '/api/pdf-lab/annotation-decisions', decision)
    assert.equal(decisionCreated.response.status, 201)
    const decisionDuplicate = await postJson(origin, '/api/pdf-lab/annotation-decisions', decision)
    assert.equal(decisionDuplicate.response.status, 200)
    assert.equal(decisionDuplicate.payload.duplicate, true)
    const staleDecision = await postJson(origin, '/api/pdf-lab/annotation-decisions', {
      ...decision,
      idempotency_key: 'annotation:fixture:stale',
      call_sha256: 'f'.repeat(64),
    })
    assert.equal(staleDecision.response.status, 409)
    assert.equal(staleDecision.payload.error, 'unknown_or_stale_annotation_item')
    const invalidType = await postJson(origin, '/api/pdf-lab/annotation-decisions', {
      ...decision,
      idempotency_key: 'annotation:fixture:bad-type',
      decision: 'correct_type',
      corrected_type: 'made-up-type',
      revision_of: decisionCreated.payload.event.event_id,
    })
    assert.equal(invalidType.response.status, 400)
    assert.equal(invalidType.payload.error, 'invalid_corrected_type')
    const amended = await postJson(origin, '/api/pdf-lab/annotation-decisions', {
      ...decision,
      idempotency_key: 'annotation:fixture:amend',
      decision: 'correct_type',
      corrected_type: 'Table',
      revision_of: decisionCreated.payload.event.event_id,
      ts: '2026-07-24T20:01:01.000Z',
    })
    assert.equal(amended.response.status, 201)
    const decisionLines = (await readFile(join(artifactsRoot, 'annotation_decisions_v1.jsonl'), 'utf-8')).trim().split('\n')
    assert.equal(decisionLines.length, 2)
    assert.equal((await readFile(join(calibrationDir, 'events_v1.jsonl'), 'utf-8')).includes('annotation_decision'), false)

    const renderUrl = `${origin}/api/pdf-lab/page-images/fc63bcd61715d0181dd8e85998b1e6201ae3515fc6626102101cab1841e11ec6/0`
    const firstRender = await fetch(renderUrl)
    assert.equal(firstRender.status, 200)
    assert.match(firstRender.headers.get('content-location') ?? '', /^[^?]*[0-9a-f]{64}\.png$/)
    assert.match(firstRender.headers.get('content-sha256') ?? '', /^[0-9a-f]{64}$/)
    const firstBytes = Buffer.from(await firstRender.arrayBuffer())
    const secondRender = await fetch(renderUrl)
    assert.equal(secondRender.status, 200)
    assert.deepEqual(Buffer.from(await secondRender.arrayBuffer()), firstBytes)
    const renderRows = (await readFile(join(artifactsRoot, 'page-image-cache/page_image_manifest_v1.jsonl'), 'utf-8'))
      .trim()
      .split('\n')
    assert.equal(renderRows.length, 1)
    const renderManifest = JSON.parse(renderRows[0])
    assert.deepEqual(
      Object.keys(renderManifest).sort(),
      [
        'byte_sha256',
        'content_sha256',
        'crop_box',
        'dpi',
        'filename',
        'page',
        'pdf_sha256',
        'pixel_height',
        'pixel_width',
        'renderer_version',
        'rotation',
        'schema',
      ],
    )
    const failedRender = await fetch(`${origin}/api/pdf-lab/page-images/${'f'.repeat(64)}/0`)
    assert.equal(failedRender.status, 422)
    assert.equal((await failedRender.json()).error, 'unknown_pdf_mount')
  } catch (error) {
    throw new Error(`${error instanceof Error ? error.stack : String(error)}\nServer output:\n${server.output()}`)
  } finally {
    await stopServer(server.child)
    await rm(artifactsRoot, { recursive: true, force: true })
  }
})
