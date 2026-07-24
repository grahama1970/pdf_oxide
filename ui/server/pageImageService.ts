import { spawnSync } from 'child_process'
import { createHash } from 'crypto'
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  statSync,
  unlinkSync,
} from 'fs'
import { basename, dirname, resolve } from 'path'

import { ContractError, canonicalJson, readJsonLines, sha256, type JsonRecord } from './beforeMainContracts'

export interface DocumentMount {
  doc_id: string
  pdf_path: string
  pdf_sha256: string
}

export interface PageImageManifestRow extends JsonRecord {
  schema: 'pdf_oxide.page_image_manifest.v1'
  content_sha256: string
  byte_sha256: string
  pdf_sha256: string
  page: number
  crop_box: [number, number, number, number]
  rotation: 0 | 90 | 180 | 270
  dpi: number
  pixel_width: number
  pixel_height: number
  renderer_version: string
  filename: string
}

function hashFile(path: string): string {
  const digest = createHash('sha256')
  digest.update(readFileSync(path))
  return digest.digest('hex')
}

function documentMounts(path: string): DocumentMount[] {
  if (!existsSync(path)) throw new ContractError(422, 'document_mount_manifest_missing')
  const raw = JSON.parse(readFileSync(path, 'utf-8')) as unknown
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new ContractError(422, 'invalid_document_mount_manifest')
  }
  const record = raw as JsonRecord
  if (record.schema !== 'pdf_oxide.document_mount_manifest.v1' || !Array.isArray(record.documents)) {
    throw new ContractError(422, 'invalid_document_mount_manifest')
  }
  return record.documents.map((value, index) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new ContractError(422, 'invalid_document_mount', `invalid document mount ${index}`)
    }
    const mount = value as JsonRecord
    if (
      typeof mount.doc_id !== 'string'
      || typeof mount.pdf_path !== 'string'
      || typeof mount.pdf_sha256 !== 'string'
      || !/^[0-9a-f]{64}$/.test(mount.pdf_sha256)
    ) {
      throw new ContractError(422, 'invalid_document_mount')
    }
    if (!existsSync(mount.pdf_path) || !statSync(mount.pdf_path).isFile()) {
      throw new ContractError(422, 'mounted_pdf_missing', mount.pdf_path)
    }
    if (hashFile(mount.pdf_path) !== mount.pdf_sha256) {
      throw new ContractError(422, 'mounted_pdf_hash_mismatch', mount.doc_id)
    }
    return mount as unknown as DocumentMount
  })
}

export function verifyDocumentMountManifest(path: string): DocumentMount[] {
  return documentMounts(path)
}

function commandOutput(command: string, args: string[]): string {
  const result = spawnSync(command, args, { encoding: 'utf-8' })
  if (result.status !== 0) {
    throw new ContractError(422, 'page_image_render_failed', `${command}: ${result.stderr || result.stdout}`)
  }
  return `${result.stdout}${result.stderr}`.trim()
}

function pageGeometry(pdfPath: string, page: number): {
  cropBox: [number, number, number, number]
  rotation: 0 | 90 | 180 | 270
} {
  const pageNumber = page + 1
  const info = commandOutput('pdfinfo', ['-box', '-f', String(pageNumber), '-l', String(pageNumber), pdfPath])
  const cropMatch = new RegExp(`Page\\s+${pageNumber}\\s+CropBox:\\s+([\\d.-]+)\\s+([\\d.-]+)\\s+([\\d.-]+)\\s+([\\d.-]+)`).exec(info)
  const rotationMatch = new RegExp(`Page\\s+${pageNumber}\\s+rot:\\s+(\\d+)`).exec(info)
  if (!cropMatch || !rotationMatch) throw new ContractError(422, 'page_geometry_unavailable')
  const [x0, y0, x1, y1] = cropMatch.slice(1).map(Number)
  const rotation = Number(rotationMatch[1])
  if (![0, 90, 180, 270].includes(rotation) || x1 <= x0 || y1 <= y0) {
    throw new ContractError(422, 'invalid_page_geometry')
  }
  return {
    cropBox: [x0, y0, x1 - x0, y1 - y0],
    rotation: rotation as 0 | 90 | 180 | 270,
  }
}

function pngDimensions(bytes: Buffer): { width: number; height: number } {
  if (bytes.length < 24 || bytes.subarray(1, 4).toString('ascii') !== 'PNG') {
    throw new ContractError(422, 'renderer_did_not_return_png')
  }
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) }
}

function contentIdentity(row: Omit<PageImageManifestRow, 'content_sha256' | 'filename'>, bytes: Buffer): string {
  return sha256(Buffer.concat([
    Buffer.from(canonicalJson(row), 'utf-8'),
    Buffer.from([0]),
    bytes,
  ]))
}

function assertCachedRow(row: PageImageManifestRow, cacheRoot: string): string {
  const imagePath = resolve(cacheRoot, row.filename)
  if (basename(imagePath) !== row.filename || !existsSync(imagePath)) {
    throw new ContractError(422, 'cached_page_image_missing')
  }
  const bytes = readFileSync(imagePath)
  if (sha256(bytes) !== row.byte_sha256) throw new ContractError(422, 'cached_page_image_hash_mismatch')
  const { content_sha256: _content, filename: _filename, ...identity } = row
  const expectedIdentity = contentIdentity(identity, bytes)
  if (row.content_sha256 !== expectedIdentity || row.filename !== `${expectedIdentity}.png`) {
    throw new ContractError(422, 'cached_page_image_identity_mismatch')
  }
  return imagePath
}

export function ensurePageImage(
  mountsPath: string,
  cacheRoot: string,
  pdfSha256: string,
  page: number,
  dpi = 150,
): { manifest: PageImageManifestRow; path: string } {
  if (!/^[0-9a-f]{64}$/.test(pdfSha256) || !Number.isInteger(page) || page < 0 || !Number.isInteger(dpi) || dpi < 1) {
    throw new ContractError(400, 'invalid_page_image_request')
  }
  const mount = documentMounts(mountsPath).find((candidate) => candidate.pdf_sha256 === pdfSha256)
  if (!mount) throw new ContractError(422, 'unknown_pdf_mount')
  mkdirSync(cacheRoot, { recursive: true })
  const ledgerPath = resolve(cacheRoot, 'page_image_manifest_v1.jsonl')
  const existing = readJsonLines(ledgerPath)
    .map((row) => row as PageImageManifestRow)
    .find((row) => row.pdf_sha256 === pdfSha256 && row.page === page && row.dpi === dpi)
  if (existing) return { manifest: existing, path: assertCachedRow(existing, cacheRoot) }

  const geometry = pageGeometry(mount.pdf_path, page)
  const rendererVersion = commandOutput('pdftoppm', ['-v']).split(/\r?\n/)[0]
  const prefix = resolve(cacheRoot, `.render-${process.pid}-${pdfSha256.slice(0, 12)}-${page}`)
  const temporaryPng = `${prefix}.png`
  try {
    commandOutput('pdftoppm', [
      '-f', String(page + 1),
      '-l', String(page + 1),
      '-r', String(dpi),
      '-png',
      '-singlefile',
      mount.pdf_path,
      prefix,
    ])
    if (!existsSync(temporaryPng)) throw new ContractError(422, 'page_image_render_missing_output')
    const bytes = readFileSync(temporaryPng)
    const dimensions = pngDimensions(bytes)
    const base = {
      schema: 'pdf_oxide.page_image_manifest.v1' as const,
      byte_sha256: sha256(bytes),
      pdf_sha256: pdfSha256,
      page,
      crop_box: geometry.cropBox,
      rotation: geometry.rotation,
      dpi,
      pixel_width: dimensions.width,
      pixel_height: dimensions.height,
      renderer_version: rendererVersion,
    }
    const identity = contentIdentity(base, bytes)
    const manifest: PageImageManifestRow = {
      ...base,
      content_sha256: identity,
      filename: `${identity}.png`,
    }
    const outputPath = resolve(cacheRoot, manifest.filename)
    if (existsSync(outputPath)) {
      if (!readFileSync(outputPath).equals(bytes)) throw new ContractError(422, 'page_image_identity_collision')
    } else {
      renameSync(temporaryPng, outputPath)
    }
    appendFileSync(ledgerPath, `${JSON.stringify(manifest)}\n`, 'utf-8')
    return { manifest, path: assertCachedRow(manifest, cacheRoot) }
  } finally {
    if (existsSync(temporaryPng)) unlinkSync(temporaryPng)
  }
}

export function resolveContentAddressedPageImage(
  cacheRoot: string,
  filename: string,
): { manifest: PageImageManifestRow; path: string } {
  if (!/^[0-9a-f]{64}\.png$/.test(filename)) throw new ContractError(400, 'invalid_page_image_filename')
  const ledgerPath = resolve(cacheRoot, 'page_image_manifest_v1.jsonl')
  const row = readJsonLines(ledgerPath)
    .map((value) => value as PageImageManifestRow)
    .find((candidate) => candidate.filename === filename)
  if (!row) throw new ContractError(422, 'unknown_page_image_identity')
  return { manifest: row, path: assertCachedRow(row, cacheRoot) }
}

export function pageImageManifestPath(cacheRoot: string): string {
  return resolve(cacheRoot, 'page_image_manifest_v1.jsonl')
}

export function documentMountManifestDirectory(path: string): string {
  return dirname(path)
}
