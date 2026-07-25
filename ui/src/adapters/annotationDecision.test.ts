import { spawn, type ChildProcess } from 'child_process'
import { cpSync, mkdtempSync, mkdirSync, readFileSync, rmSync } from 'fs'
import { createServer } from 'net'
import { tmpdir } from 'os'
import { resolve } from 'path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  appendAnnotationDecision,
  getAnnotationDecisions,
} from '../../server/beforeMainContracts'
import {
  buildAnnotationDecisionInput,
  isAnnotationDecisionEvent,
} from './annotationDecision'
import type { AnnotationQueueItem } from './annotationCall'

const temporaryRoots: string[] = []
const childProcesses: ChildProcess[] = []

afterEach(() => {
  for (const child of childProcesses.splice(0)) child.kill('SIGTERM')
  for (const root of temporaryRoots.splice(0)) rmSync(root, { recursive: true })
})

async function unusedPort(): Promise<number> {
  const server = createServer()
  await new Promise<void>((resolvePromise) => server.listen(0, '127.0.0.1', resolvePromise))
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('failed to allocate test port')
  await new Promise<void>((resolvePromise, reject) => {
    server.close((error) => error ? reject(error) : resolvePromise())
  })
  return address.port
}

async function waitForApi(url: string): Promise<void> {
  let lastError: unknown
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(url)
      if (response.ok) return
      lastError = new Error(`status ${response.status}`)
    } catch (error) {
      lastError = error
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 100))
  }
  throw new Error(`API did not start: ${String(lastError)}`)
}

describe('annotation decision corrected_text ledger', () => {
  it('builds the workbench not-an-element decision without correction payloads', () => {
    const item = {
      id: 'item-not-element',
      itemSha256: 'a'.repeat(64),
      callSha256: 'b'.repeat(64),
    } as AnnotationQueueItem
    expect(buildAnnotationDecisionInput(item, 'not_an_element', {
      timestamp: '2026-07-25T12:00:00.000Z',
    })).toMatchObject({
      item_id: 'item-not-element',
      decision: 'not_an_element',
    })
  })

  it('round-trips corrected_text through the adapter input', () => {
    const item = {
      id: 'item-1',
      itemSha256: 'a'.repeat(64),
      callSha256: 'b'.repeat(64),
    } as AnnotationQueueItem
    const input = buildAnnotationDecisionInput(item, 'accept', {
      correctedText: 'Corrected extraction text.',
      timestamp: '2026-07-25T12:00:00.000Z',
    })

    expect(input.corrected_text).toBe('Corrected extraction text.')
    expect(isAnnotationDecisionEvent({
      schema: 'pdf_oxide.annotation_decision_event.v1',
      event_id: 'event',
      request_sha256: 'c'.repeat(64),
      ...input,
    })).toBe(true)
  })

  it('writes corrected_text through the API contract and reads it back', () => {
    const artifactsRoot = resolve(process.cwd(), '..', 'artifacts', 'pdf-lab')
    const manifestPath = resolve(artifactsRoot, 'annotation_queue_manifest_v1.json')
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf-8')) as {
      items: Array<{
        item_id: string
        item_sha256: string
        call_sha256: string
      }>
    }
    const item = manifest.items[0]
    const root = mkdtempSync(resolve(tmpdir(), 'pdf-oxide-corrected-text-'))
    temporaryRoots.push(root)
    const decisionsPath = resolve(root, 'annotation_decisions_v1.jsonl')

    const written = appendAnnotationDecision({
      idempotency_key: 'annotation:corrected-text:test',
      item_id: item.item_id,
      item_sha256: item.item_sha256,
      call_sha256: item.call_sha256,
      decision: 'accept',
      corrected_text: 'Human-corrected text persisted verbatim.\n',
      ts: '2026-07-25T12:00:00.000Z',
    }, decisionsPath, artifactsRoot, manifestPath)
    const readBack = getAnnotationDecisions(decisionsPath) as {
      events: Array<{ corrected_text?: string }>
      active: Array<{ corrected_text?: string }>
    }

    expect(written.event.corrected_text).toBe('Human-corrected text persisted verbatim.\n')
    expect(readBack.events[0].corrected_text).toBe('Human-corrected text persisted verbatim.\n')
    expect(readBack.active[0].corrected_text).toBe('Human-corrected text persisted verbatim.\n')
  })

  it('persists corrected_text through POST and returns it through GET', async () => {
    const sourceArtifacts = resolve(process.cwd(), '..', 'artifacts', 'pdf-lab')
    const root = mkdtempSync(resolve(tmpdir(), 'pdf-oxide-corrected-text-http-'))
    temporaryRoots.push(root)
    cpSync(
      resolve(sourceArtifacts, 'annotation-calls'),
      resolve(root, 'annotation-calls'),
      { recursive: true },
    )
    cpSync(
      resolve(sourceArtifacts, 'annotation_queue_manifest_v1.json'),
      resolve(root, 'annotation_queue_manifest_v1.json'),
    )
    mkdirSync(resolve(root, 'public'))

    const manifest = JSON.parse(
      readFileSync(resolve(root, 'annotation_queue_manifest_v1.json'), 'utf-8'),
    ) as {
      items: Array<{
        item_id: string
        item_sha256: string
        call_sha256: string
      }>
    }
    const item = manifest.items[0]
    const port = await unusedPort()
    const child = spawn(
      resolve(process.cwd(), 'node_modules', '.bin', 'tsx'),
      [resolve(process.cwd(), 'server', 'index.ts')],
      {
        cwd: process.cwd(),
        env: {
          ...process.env,
          PDF_LAB_API_PORT: String(port),
          PDF_LAB_ARTIFACTS_ROOT: root,
          PDF_LAB_PUBLIC_ROOT: resolve(root, 'public'),
        },
        stdio: 'ignore',
      },
    )
    childProcesses.push(child)
    const endpoint = `http://127.0.0.1:${port}/api/pdf-lab/annotation-decisions`
    await waitForApi(`http://127.0.0.1:${port}/api/pdf-lab/status`)

    const postResponse = await fetch(endpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        idempotency_key: 'annotation:corrected-text:http-test',
        item_id: item.item_id,
        item_sha256: item.item_sha256,
        call_sha256: item.call_sha256,
        decision: 'accept',
        corrected_text: 'HTTP-persisted corrected text.',
        ts: '2026-07-25T12:00:00.000Z',
      }),
    })
    expect(postResponse.status).toBe(201)
    const postPayload = await postResponse.json() as {
      event: { corrected_text?: string }
    }
    expect(postPayload.event.corrected_text).toBe('HTTP-persisted corrected text.')

    const getResponse = await fetch(endpoint)
    expect(getResponse.status).toBe(200)
    const getPayload = await getResponse.json() as {
      active: Array<{ corrected_text?: string }>
    }
    expect(getPayload.active[0].corrected_text).toBe('HTTP-persisted corrected text.')
  })

  it('rejects corrected_text over 20000 characters', () => {
    const item = {
      id: 'item-1',
      itemSha256: 'a'.repeat(64),
      callSha256: 'b'.repeat(64),
    } as AnnotationQueueItem

    expect(() => buildAnnotationDecisionInput(item, 'accept', {
      correctedText: 'x'.repeat(20_001),
    })).toThrow(/must not exceed 20000/)
  })
})
