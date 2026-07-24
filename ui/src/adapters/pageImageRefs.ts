export const PAGE_IMAGE_SCHEMA = 'pdf_oxide.page_image.v1'
export const PAGE_IMAGE_NAMING = 'sha256(canonical JSON of schema,pdf_sha256,page_index,dpi,format + NUL + PNG bytes)'

export interface PageImageRef {
  page: number
  filename: string
  byte_sha256: string
}

export interface PageImageManifest {
  schema: typeof PAGE_IMAGE_SCHEMA
  pdf_sha256: string
  directory: string
  dpi: number
  format: string
  naming: typeof PAGE_IMAGE_NAMING
  images: PageImageRef[]
}

export interface CalibrationPageImageIndex {
  schema: 'pdf_oxide.calibration_page_images.v1'
  documents: Record<string, PageImageManifest>
}

export interface ResolvedPageImage {
  src: string
  filename: string
  byteSha256: string
}

function isLowerSha256(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

function encodedPath(path: string): string {
  return path
    .split('/')
    .filter(Boolean)
    .map(encodeURIComponent)
    .join('/')
}

export function assertPageImageManifest(value: unknown): asserts value is PageImageManifest {
  if (!value || typeof value !== 'object') throw new Error('page-image manifest must be an object')
  const manifest = value as Partial<PageImageManifest>
  if (manifest.schema !== PAGE_IMAGE_SCHEMA) {
    throw new Error(`page-image manifest schema must be ${PAGE_IMAGE_SCHEMA}`)
  }
  if (typeof manifest.pdf_sha256 !== 'string' || !isLowerSha256(manifest.pdf_sha256)) {
    throw new Error('page-image manifest pdf_sha256 must be a lowercase SHA-256')
  }
  if (typeof manifest.directory !== 'string' || !manifest.directory || manifest.directory.startsWith('/')) {
    throw new Error('page-image manifest directory must be a non-empty relative path')
  }
  if (!Number.isInteger(manifest.dpi) || Number(manifest.dpi) <= 0) {
    throw new Error('page-image manifest dpi must be a positive integer')
  }
  if (manifest.format !== 'png') throw new Error('page-image manifest format must be png')
  if (manifest.naming !== PAGE_IMAGE_NAMING) {
    throw new Error('page-image manifest naming must match pipeline_page_images.py')
  }
  if (!Array.isArray(manifest.images) || manifest.images.length === 0) {
    throw new Error('page-image manifest images must be a non-empty array')
  }
  const seenPages = new Set<number>()
  for (const image of manifest.images) {
    if (!image || typeof image !== 'object') throw new Error('page-image entry must be an object')
    if (!Number.isInteger(image.page) || image.page < 0 || seenPages.has(image.page)) {
      throw new Error('page-image entry page must be a unique non-negative integer')
    }
    seenPages.add(image.page)
    if (typeof image.filename !== 'string' || !/^[0-9a-f]{64}\.png$/.test(image.filename)) {
      throw new Error('page-image filename must be a content-addressed lowercase SHA-256 PNG name')
    }
    if (!isLowerSha256(image.byte_sha256)) {
      throw new Error('page-image byte_sha256 must be a lowercase SHA-256')
    }
  }
}

export function assertCalibrationPageImageIndex(value: unknown): asserts value is CalibrationPageImageIndex {
  if (!value || typeof value !== 'object') throw new Error('calibration page-image index must be an object')
  const index = value as Partial<CalibrationPageImageIndex>
  if (index.schema !== 'pdf_oxide.calibration_page_images.v1') {
    throw new Error('calibration page-image index schema must be pdf_oxide.calibration_page_images.v1')
  }
  if (!index.documents || typeof index.documents !== 'object' || Array.isArray(index.documents)) {
    throw new Error('calibration page-image index documents must be an object')
  }
  for (const manifest of Object.values(index.documents)) assertPageImageManifest(manifest)
}

export function resolvePageImageRef(
  indexValue: unknown,
  doc: string,
  page: number,
  baseUrl = '/artifacts/pdf-lab/calibration',
): ResolvedPageImage {
  assertCalibrationPageImageIndex(indexValue)
  const manifest = indexValue.documents[doc]
  if (!manifest) throw new Error(`no content-addressed page-image manifest for document ${doc}`)
  const image = manifest.images.find(candidate => candidate.page === page)
  if (!image) throw new Error(`no content-addressed page image for ${doc} page ${page}`)
  return {
    src: `${baseUrl.replace(/\/+$/, '')}/${encodedPath(manifest.directory)}/${encodeURIComponent(image.filename)}`,
    filename: image.filename,
    byteSha256: image.byte_sha256,
  }
}
