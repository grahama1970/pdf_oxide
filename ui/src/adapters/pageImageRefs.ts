export type BboxXywh = readonly [x: number, y: number, width: number, height: number]

export interface PageImageRef {
  sha256: string
  filename: string
  href: string
  mimeType: 'image/png'
  page?: number
  width?: number
  height?: number
  doc?: string
  pdfSha256?: string
}

export interface PageImageLookupContext {
  doc?: string
  page?: number
  pdfSha256?: string
  baseUrl?: string
  indexUrl?: string
  strictContentAddressed?: boolean
}

export interface PageImageIndex {
  byDocAndPage: ReadonlyMap<string, readonly PageImageRef[]>
  all: readonly PageImageRef[]
}

const SHA256_RE = /^[a-f0-9]{64}$/i
const CONTENT_ADDRESSED_PNG_RE = /^([a-f0-9]{64})\.png$/i

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function finitePositive(value: unknown): number | undefined {
  const numberValue = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numberValue) && numberValue > 0 ? numberValue : undefined
}

function finiteInteger(value: unknown): number | undefined {
  const numberValue = typeof value === 'number' ? value : Number(value)
  return Number.isInteger(numberValue) && numberValue >= 0 ? numberValue : undefined
}

function normalizeSha256(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  const normalized = value.trim().replace(/^sha256:/i, '').toLowerCase()
  return SHA256_RE.test(normalized) ? normalized : undefined
}

function basenameFromHref(href: string): string {
  const withoutQuery = href.split(/[?#]/, 1)[0]
  const segments = withoutQuery.split('/')
  return decodeURIComponent(segments[segments.length - 1] ?? '')
}

function joinUrl(baseUrl: string, filename: string): string {
  const normalizedBase = baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`
  try {
    if (/^[a-z][a-z0-9+.-]*:/i.test(normalizedBase)) {
      return new URL(filename, normalizedBase).toString()
    }
    const resolved = new URL(filename, `http://pdf-lab.local${normalizedBase.startsWith('/') ? '' : '/'}${normalizedBase}`)
    return `${resolved.pathname}${resolved.search}${resolved.hash}`
  } catch {
    return `${baseUrl.replace(/\/+$/, '')}/${filename.replace(/^\/+/, '')}`
  }
}

function isDirectHref(value: string): boolean {
  return /^(?:https?:|data:|blob:|\/)/i.test(value)
}

function indexBaseUrl(indexUrl: string | undefined): string | undefined {
  if (!indexUrl) return undefined
  const clean = indexUrl.split(/[?#]/, 1)[0]
  const slash = clean.lastIndexOf('/')
  return slash >= 0 ? clean.slice(0, slash + 1) : './'
}

export function normalizePageImageRef(
  raw: unknown,
  context: PageImageLookupContext = {},
): PageImageRef {
  const strict = context.strictContentAddressed ?? true
  const baseUrl = context.baseUrl ?? indexBaseUrl(context.indexUrl) ?? '/artifacts/pdf-lab/page_images'

  let hrefCandidate: string | undefined
  let explicitSha: string | undefined
  let page = context.page
  let width: number | undefined
  let height: number | undefined
  let doc = context.doc
  let pdfSha256 = normalizeSha256(context.pdfSha256)
  let mimeType = 'image/png'

  if (typeof raw === 'string') {
    hrefCandidate = raw.trim()
  } else {
    const record = asRecord(raw)
    if (!record) throw new Error('page_image_ref must be a string or object')
    hrefCandidate = [record.href, record.url, record.path, record.filename]
      .find((value): value is string => typeof value === 'string' && value.trim().length > 0)
      ?.trim()
    explicitSha = normalizeSha256(record.sha256 ?? record.content_sha256 ?? record.image_sha256)
    page = finiteInteger(record.page) ?? page
    width = finitePositive(record.width ?? record.pixel_width)
    height = finitePositive(record.height ?? record.pixel_height)
    doc = typeof record.doc === 'string' ? record.doc : doc
    pdfSha256 = normalizeSha256(record.pdf_sha256) ?? pdfSha256
    mimeType = typeof record.mime_type === 'string' ? record.mime_type : mimeType
  }

  if (mimeType !== 'image/png') {
    throw new Error(`page image must be image/png, received ${mimeType}`)
  }

  if (!hrefCandidate && explicitSha) hrefCandidate = `${explicitSha}.png`
  if (!hrefCandidate) throw new Error('page_image_ref is missing href/path/filename')

  const href = isDirectHref(hrefCandidate) ? hrefCandidate : joinUrl(baseUrl, hrefCandidate)
  const basename = basenameFromHref(href)
  const basenameMatch = CONTENT_ADDRESSED_PNG_RE.exec(basename)
  const filenameSha = basenameMatch?.[1]?.toLowerCase()
  const sha256 = explicitSha ?? filenameSha

  if (!sha256) {
    throw new Error(`page image is not content addressed: ${basename || href}`)
  }
  if (strict && basename !== `${sha256}.png`) {
    throw new Error(`page image filename must be ${sha256}.png, received ${basename}`)
  }
  if (explicitSha && filenameSha && explicitSha !== filenameSha) {
    throw new Error('page image sha256 does not match its filename')
  }

  return {
    sha256,
    filename: `${sha256}.png`,
    href,
    mimeType: 'image/png',
    page,
    width,
    height,
    doc,
    pdfSha256,
  }
}

export function normalizePageImageRefs(
  raw: unknown,
  context: PageImageLookupContext = {},
): PageImageRef[] {
  const values = Array.isArray(raw) ? raw : raw == null ? [] : [raw]
  const seen = new Set<string>()
  const refs: PageImageRef[] = []
  for (const value of values) {
    const ref = normalizePageImageRef(value, context)
    const key = `${ref.sha256}::${ref.page ?? ''}`
    if (seen.has(key)) continue
    seen.add(key)
    refs.push(ref)
  }
  return refs
}

function pageKey(doc: string | undefined, page: number): string {
  return `${doc ?? '*'}::${page}`
}

function rowsFromIndexPayload(raw: unknown): unknown[] {
  if (Array.isArray(raw)) return raw
  const record = asRecord(raw)
  if (!record) return []
  if (Array.isArray(record.pages)) return record.pages
  if (Array.isArray(record.page_images)) return record.page_images

  const rows: unknown[] = []
  const documents = asRecord(record.documents)
  if (documents) {
    for (const [doc, manifestValue] of Object.entries(documents)) {
      const manifest = asRecord(manifestValue)
      if (!manifest || !Array.isArray(manifest.images)) continue
      const directory = typeof manifest.directory === 'string' ? manifest.directory : ''
      for (const imageValue of manifest.images) {
        const image = asRecord(imageValue)
        if (!image || typeof image.filename !== 'string') continue
        rows.push({
          doc,
          page: image.page,
          pdf_sha256: manifest.pdf_sha256,
          page_image_refs: [{
            filename: [directory, image.filename].filter(Boolean).join('/'),
            sha256: image.filename.replace(/\.png$/i, ''),
            page: image.page,
            pdf_sha256: manifest.pdf_sha256,
          }],
        })
      }
    }
    return rows
  }
  for (const [key, value] of Object.entries(record)) {
    const match = /^(.*?)(?:::|#|\/)(\d+)$/.exec(key)
    if (!match) continue
    rows.push({ doc: match[1], page: Number(match[2]), page_image_refs: value })
  }
  return rows
}

export function parsePageImageIndex(
  raw: unknown,
  options: Pick<PageImageLookupContext, 'baseUrl' | 'indexUrl' | 'strictContentAddressed'> = {},
): PageImageIndex {
  const map = new Map<string, PageImageRef[]>()
  const all: PageImageRef[] = []

  for (const row of rowsFromIndexPayload(raw)) {
    const record = asRecord(row)
    if (!record) continue
    const doc = typeof record.doc === 'string'
      ? record.doc
      : typeof record.document === 'string'
        ? record.document
        : undefined
    const page = finiteInteger(record.page ?? record.page_index)
    if (page === undefined) continue
    const rawRefs = record.page_image_refs ?? record.refs ?? record.images ?? record.image
    const refs = normalizePageImageRefs(rawRefs, {
      ...options,
      doc,
      page,
      pdfSha256: typeof record.pdf_sha256 === 'string' ? record.pdf_sha256 : undefined,
    })
    if (refs.length === 0) continue
    map.set(pageKey(doc, page), refs)
    if (!map.has(pageKey(undefined, page))) map.set(pageKey(undefined, page), refs)
    all.push(...refs)
  }

  return { byDocAndPage: map, all }
}

export function mergePageImageIndexes(indexes: readonly PageImageIndex[]): PageImageIndex {
  const map = new Map<string, readonly PageImageRef[]>()
  const all: PageImageRef[] = []
  for (const index of indexes) {
    for (const [key, refs] of index.byDocAndPage) map.set(key, refs)
    all.push(...index.all)
  }
  return { byDocAndPage: map, all }
}

export function lookupPageImageRefs(
  index: PageImageIndex | null | undefined,
  doc: string | undefined,
  page: number,
): readonly PageImageRef[] {
  if (!index) return []
  if (doc) return index.byDocAndPage.get(pageKey(doc, page)) ?? []
  return index.byDocAndPage.get(pageKey(undefined, page)) ?? []
}

export function resolvePageImageRefs(
  source: Record<string, unknown>,
  index: PageImageIndex | null | undefined,
  context: PageImageLookupContext & { page: number },
): PageImageRef[] {
  const direct = source.page_image_refs ?? source.pageImages ?? source.page_images
  if (direct != null) {
    return normalizePageImageRefs(direct, context)
  }
  return [...lookupPageImageRefs(index, context.doc, context.page)]
}

export function assertOriginalPageImages(
  refs: readonly PageImageRef[],
  description: string,
): asserts refs is readonly [PageImageRef, ...PageImageRef[]] {
  if (refs.length === 0) {
    throw new Error(`retrieval contract violation: missing original page image for ${description}`)
  }
}

export function normalizeBboxXywh(
  raw: unknown,
  pageImage?: Pick<PageImageRef, 'width' | 'height'>,
): BboxXywh | undefined {
  if (raw == null) return undefined
  if (!Array.isArray(raw) || raw.length !== 4) throw new Error('bbox must contain four numbers')
  const values = raw.map(Number)
  if (!values.every(Number.isFinite)) throw new Error('bbox values must be finite')
  let [x, y, width, height] = values

  const looksNormalized = x >= 0 && y >= 0 && width >= 0 && height >= 0
    && x + width <= 1.000001 && y + height <= 1.000001
  if (!looksNormalized) {
    if (!pageImage?.width || !pageImage.height) {
      throw new Error('absolute bbox requires page image width and height')
    }
    x /= pageImage.width
    width /= pageImage.width
    y /= pageImage.height
    height /= pageImage.height
  }

  if (width <= 0 || height <= 0 || x < 0 || y < 0 || x + width > 1.000001 || y + height > 1.000001) {
    throw new Error('bbox must be a positive normalized [x,y,width,height] rectangle')
  }
  return [x, y, width, height]
}

/**
 * Convert pdf_oxide's PDF-space [x,y,width,height] rectangle into the
 * top-left normalized coordinates used by the browser overlay.
 *
 * PDF coordinates have their origin at the bottom left. Page images and CSS
 * have their origin at the top left, so the vertical axis must be inverted.
 */
export function normalizePdfBboxXywh(
  raw: unknown,
  pageImage: Pick<PageImageRef, 'width' | 'height'>,
): BboxXywh {
  if (!pageImage.width || !pageImage.height) {
    throw new Error('PDF bbox requires page width and height')
  }
  if (!Array.isArray(raw) || raw.length !== 4) throw new Error('bbox must contain four numbers')
  const [x, y, width, height] = raw.map(Number)
  if (![x, y, width, height].every(Number.isFinite)) throw new Error('bbox values must be finite')
  if (
    x < 0
    || y < 0
    || width <= 0
    || height <= 0
    || x + width > pageImage.width
    || y + height > pageImage.height
  ) {
    throw new Error('PDF bbox must fit inside the source page')
  }
  return [
    x / pageImage.width,
    (pageImage.height - y - height) / pageImage.height,
    width / pageImage.width,
    height / pageImage.height,
  ]
}

/** Convert the mature extraction renderer's [x0,y0,x1,y1] box to UI xywh. */
export function normalizeBboxXyxy(
  raw: unknown,
  pageImage?: Pick<PageImageRef, 'width' | 'height'>,
): BboxXywh | undefined {
  if (raw == null) return undefined
  if (!Array.isArray(raw) || raw.length !== 4) throw new Error('bbox must contain four numbers')
  let [x0, y0, x1, y1] = raw.map(Number)
  if (![x0, y0, x1, y1].every(Number.isFinite)) throw new Error('bbox values must be finite')

  const looksNormalized = x0 >= 0 && y0 >= 0 && x1 <= 1.000001 && y1 <= 1.000001
  if (!looksNormalized) {
    if (!pageImage?.width || !pageImage.height) {
      throw new Error('absolute bbox requires page image width and height')
    }
    x0 /= pageImage.width
    x1 /= pageImage.width
    y0 /= pageImage.height
    y1 /= pageImage.height
  }
  if (x0 < 0 || y0 < 0 || x1 > 1.000001 || y1 > 1.000001 || x0 >= x1 || y0 >= y1) {
    throw new Error('bbox must be normalized [x0,y0,x1,y1] with positive area')
  }
  return [x0, y0, x1 - x0, y1 - y0]
}

export function bboxStyle(bbox: BboxXywh): Readonly<Record<'left' | 'top' | 'width' | 'height', string>> {
  return {
    left: `${bbox[0] * 100}%`,
    top: `${bbox[1] * 100}%`,
    width: `${bbox[2] * 100}%`,
    height: `${bbox[3] * 100}%`,
  }
}

// Compatibility surface retained from the Codex entry. The live calibration
// API validates these manifests and the image bytes before the winning routes
// consume them.
export const PAGE_IMAGE_SCHEMA = 'pdf_oxide.page_image.v1'
export const PAGE_IMAGE_NAMING = 'sha256(canonical JSON of schema,pdf_sha256,page_index,dpi,format + NUL + PNG bytes)'

export interface ManifestPageImageRef {
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
  images: ManifestPageImageRef[]
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
  const image = manifest.images.find((candidate) => candidate.page === page)
  if (!image) throw new Error(`no content-addressed page image for ${doc} page ${page}`)
  return {
    src: `${baseUrl.replace(/\/+$/, '')}/${encodedPath(manifest.directory)}/${encodeURIComponent(image.filename)}`,
    filename: image.filename,
    byteSha256: image.byte_sha256,
  }
}
