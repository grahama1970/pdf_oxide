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
  return `${baseUrl.replace(/\/+$/, '')}/${filename.replace(/^\/+/, '')}`
}

function isDirectHref(value: string): boolean {
  return /^(?:https?:|data:|blob:|\/)/i.test(value)
}

export function normalizePageImageRef(
  raw: unknown,
  context: PageImageLookupContext = {},
): PageImageRef {
  const strict = context.strictContentAddressed ?? true
  const baseUrl = context.baseUrl ?? '/artifacts/pdf-lab/page_images'

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
  for (const [key, value] of Object.entries(record)) {
    const match = /^(.*?)(?:::|#|\/)(\d+)$/.exec(key)
    if (!match) continue
    rows.push({ doc: match[1], page: Number(match[2]), page_image_refs: value })
  }
  return rows
}

export function parsePageImageIndex(
  raw: unknown,
  options: Pick<PageImageLookupContext, 'baseUrl' | 'strictContentAddressed'> = {},
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

export function lookupPageImageRefs(
  index: PageImageIndex | null | undefined,
  doc: string | undefined,
  page: number,
): readonly PageImageRef[] {
  if (!index) return []
  return index.byDocAndPage.get(pageKey(doc, page))
    ?? index.byDocAndPage.get(pageKey(undefined, page))
    ?? []
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

export function bboxStyle(bbox: BboxXywh): Readonly<Record<'left' | 'top' | 'width' | 'height', string>> {
  return {
    left: `${bbox[0] * 100}%`,
    top: `${bbox[1] * 100}%`,
    width: `${bbox[2] * 100}%`,
    height: `${bbox[3] * 100}%`,
  }
}
