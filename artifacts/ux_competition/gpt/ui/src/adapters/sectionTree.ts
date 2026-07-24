export const SECTION_TREE_SCHEMA = 'pdf_oxide.section_tree.v2' as const

export interface RawSectionProvenance {
  pdf_sha256: string
  page: number
  bbox?: [number, number, number, number]
}

export interface RawSectionV2 {
  id: string
  title: string
  level: number
  parent_id: string | null
  children: string[]
  depth: number
  doc_order: number
  page_start: number
  page_end: number
  provenance: RawSectionProvenance
  block_ids: string[]
}

export interface SectionTreePayloadV2 {
  schema?: typeof SECTION_TREE_SCHEMA
  pdf_sha256?: string
  sections: RawSectionV2[]
}

export interface SectionNode {
  id: string
  title: string
  level: number
  parentId: string | null
  childIds: readonly string[]
  depth: number
  docOrder: number
  pageStart: number
  pageEnd: number
  provenance: {
    pdfSha256: string
    page: number
    bbox: readonly [number, number, number, number] | null
  }
  blockIds: readonly string[]
  path: readonly string[]
  pathIds: readonly string[]
}

export interface SectionTree {
  schema: typeof SECTION_TREE_SCHEMA
  pdfSha256: string
  roots: readonly SectionNode[]
  ordered: readonly SectionNode[]
  byId: ReadonlyMap<string, SectionNode>
  byBlockId: ReadonlyMap<string, SectionNode>
}

const SHA256_RE = /^[a-f0-9]{64}$/i

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.trim().length === 0) throw new Error(`${field} is required`)
  return value.trim()
}

function nonNegativeInteger(value: unknown, field: string): number {
  const numberValue = typeof value === 'number' ? value : Number(value)
  if (!Number.isInteger(numberValue) || numberValue < 0) throw new Error(`${field} must be a non-negative integer`)
  return numberValue
}

function normalizePdfSha(value: unknown, field: string): string {
  const normalized = requiredString(value, field).replace(/^sha256:/i, '').toLowerCase()
  if (!SHA256_RE.test(normalized)) throw new Error(`${field} must be a SHA-256 digest`)
  return normalized
}

function stringArray(value: unknown, field: string): string[] {
  if (!Array.isArray(value)) throw new Error(`${field} must be an array`)
  const output = value.map((entry, index) => requiredString(entry, `${field}[${index}]`))
  if (new Set(output).size !== output.length) throw new Error(`${field} contains duplicates`)
  return output
}

function normalizeBbox(value: unknown, field: string): readonly [number, number, number, number] | null {
  if (value == null) return null
  if (!Array.isArray(value) || value.length !== 4) throw new Error(`${field} must have four numbers`)
  const values = value.map(Number)
  if (!values.every(Number.isFinite)) throw new Error(`${field} must contain finite numbers`)
  return [values[0], values[1], values[2], values[3]]
}

interface MutableSection {
  id: string
  title: string
  level: number
  parentId: string | null
  childIds: string[]
  depth: number
  docOrder: number
  pageStart: number
  pageEnd: number
  provenance: SectionNode['provenance']
  blockIds: string[]
}

function normalizeSection(raw: unknown, index: number, defaultPdfSha?: string): MutableSection {
  const section = asRecord(raw)
  if (!section) throw new Error(`sections[${index}] must be an object`)
  const provenance = asRecord(section.provenance)
  if (!provenance) throw new Error(`sections[${index}].provenance is required`)

  const pageStart = nonNegativeInteger(section.page_start, `sections[${index}].page_start`)
  const pageEnd = nonNegativeInteger(section.page_end, `sections[${index}].page_end`)
  if (pageEnd < pageStart) throw new Error(`sections[${index}] page_end precedes page_start`)

  const parentValue = section.parent_id
  const parentId = parentValue == null ? null : requiredString(parentValue, `sections[${index}].parent_id`)
  const pdfSha256 = normalizePdfSha(provenance.pdf_sha256 ?? defaultPdfSha, `sections[${index}].provenance.pdf_sha256`)

  return {
    id: requiredString(section.id, `sections[${index}].id`),
    title: requiredString(section.title, `sections[${index}].title`),
    level: nonNegativeInteger(section.level, `sections[${index}].level`),
    parentId,
    childIds: stringArray(section.children, `sections[${index}].children`),
    depth: nonNegativeInteger(section.depth, `sections[${index}].depth`),
    docOrder: nonNegativeInteger(section.doc_order, `sections[${index}].doc_order`),
    pageStart,
    pageEnd,
    provenance: {
      pdfSha256,
      page: nonNegativeInteger(provenance.page, `sections[${index}].provenance.page`),
      bbox: normalizeBbox(provenance.bbox, `sections[${index}].provenance.bbox`),
    },
    blockIds: stringArray(section.block_ids, `sections[${index}].block_ids`),
  }
}

export function normalizeSectionTree(raw: unknown): SectionTree {
  const record = asRecord(raw)
  if (!record) throw new Error('section tree must be an object')
  if (record.schema != null && record.schema !== SECTION_TREE_SCHEMA) {
    throw new Error(`unsupported section tree schema: ${String(record.schema)}`)
  }
  if (!Array.isArray(record.sections)) throw new Error('section tree sections must be an array')

  const defaultPdfSha = typeof record.pdf_sha256 === 'string'
    ? normalizePdfSha(record.pdf_sha256, 'section tree pdf_sha256')
    : undefined
  const sections = record.sections.map((section, index) => normalizeSection(section, index, defaultPdfSha))
  if (sections.length === 0) throw new Error('section tree must contain at least one section')

  const byIdMutable = new Map<string, MutableSection>()
  const docOrders = new Set<number>()
  for (const section of sections) {
    if (byIdMutable.has(section.id)) throw new Error(`duplicate section id: ${section.id}`)
    if (docOrders.has(section.docOrder)) throw new Error(`duplicate doc_order: ${section.docOrder}`)
    byIdMutable.set(section.id, section)
    docOrders.add(section.docOrder)
  }

  for (const section of sections) {
    if (section.parentId && !byIdMutable.has(section.parentId)) {
      throw new Error(`section ${section.id} references missing parent ${section.parentId}`)
    }
    for (const childId of section.childIds) {
      const child = byIdMutable.get(childId)
      if (!child) throw new Error(`section ${section.id} references missing child ${childId}`)
      if (child.parentId !== section.id) {
        throw new Error(`section ${section.id} child ${childId} does not point back to its parent`)
      }
    }
    if (section.parentId) {
      const parent = byIdMutable.get(section.parentId)!
      if (!parent.childIds.includes(section.id)) {
        throw new Error(`section ${section.id} is missing from parent ${parent.id}.children`)
      }
    }
  }

  const visiting = new Set<string>()
  const visited = new Set<string>()
  const pathCache = new Map<string, { titles: string[]; ids: string[] }>()

  const visit = (section: MutableSection): { titles: string[]; ids: string[] } => {
    const cached = pathCache.get(section.id)
    if (cached) return cached
    if (visiting.has(section.id)) throw new Error(`section tree cycle detected at ${section.id}`)
    visiting.add(section.id)

    const parentPath = section.parentId ? visit(byIdMutable.get(section.parentId)!) : { titles: [], ids: [] }
    const expectedDepth = parentPath.ids.length
    if (section.depth !== expectedDepth) {
      throw new Error(`section ${section.id} depth ${section.depth} does not match parent chain depth ${expectedDepth}`)
    }
    const path = {
      titles: [...parentPath.titles, section.title],
      ids: [...parentPath.ids, section.id],
    }
    pathCache.set(section.id, path)
    visiting.delete(section.id)
    visited.add(section.id)
    return path
  }

  sections.forEach(visit)
  if (visited.size !== sections.length) throw new Error('section tree validation did not visit every section')

  const pdfShaSet = new Set(sections.map((section) => section.provenance.pdfSha256))
  if (pdfShaSet.size !== 1) throw new Error('all section provenance rows must reference the same PDF')
  const pdfSha256 = [...pdfShaSet][0]

  const orderedMutable = sections.slice().sort((left, right) => left.docOrder - right.docOrder)
  for (let index = 1; index < orderedMutable.length; index += 1) {
    if (orderedMutable[index - 1].docOrder >= orderedMutable[index].docOrder) {
      throw new Error('doc_order must define a strict total order')
    }
  }

  const finalById = new Map<string, SectionNode>()
  for (const section of orderedMutable) {
    const path = pathCache.get(section.id)!
    finalById.set(section.id, Object.freeze({
      ...section,
      childIds: Object.freeze([...section.childIds]),
      blockIds: Object.freeze([...section.blockIds]),
      provenance: Object.freeze({ ...section.provenance }),
      path: Object.freeze([...path.titles]),
      pathIds: Object.freeze([...path.ids]),
    }))
  }

  const ordered = Object.freeze(orderedMutable.map((section) => finalById.get(section.id)!))
  const roots = Object.freeze(ordered.filter((section) => section.parentId === null))
  if (roots.length === 0) throw new Error('section tree has no root')

  const byBlockId = new Map<string, SectionNode>()
  for (const section of ordered) {
    for (const blockId of section.blockIds) {
      if (byBlockId.has(blockId)) throw new Error(`block ${blockId} belongs to multiple sections`)
      byBlockId.set(blockId, section)
    }
  }

  return {
    schema: SECTION_TREE_SCHEMA,
    pdfSha256,
    roots,
    ordered,
    byId: finalById,
    byBlockId,
  }
}

export function sectionPath(tree: SectionTree, sectionId: string): readonly string[] {
  const section = tree.byId.get(sectionId)
  if (!section) throw new Error(`unknown section id: ${sectionId}`)
  return section.path
}

export function sectionForElement(
  tree: SectionTree,
  element: { id?: string; element_id?: string; section_id?: string; page?: number },
): SectionNode | null {
  if (element.section_id) return tree.byId.get(element.section_id) ?? null
  const elementId = element.id ?? element.element_id
  if (elementId) {
    const direct = tree.byBlockId.get(elementId)
    if (direct) return direct
  }
  if (typeof element.page === 'number') {
    return tree.ordered.find((section) => section.pageStart <= element.page! && section.pageEnd >= element.page!) ?? null
  }
  return null
}

export function sectionTreeRows(tree: SectionTree): Array<{
  id: string
  label: string
  depth: number
  parentId: string | null
  childIds: readonly string[]
  docOrder: number
  pageRange: readonly [number, number]
  breadcrumb: string
}> {
  return tree.ordered.map((section) => ({
    id: section.id,
    label: section.title,
    depth: section.depth,
    parentId: section.parentId,
    childIds: section.childIds,
    docOrder: section.docOrder,
    pageRange: [section.pageStart, section.pageEnd],
    breadcrumb: section.path.join(' › '),
  }))
}
