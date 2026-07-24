import type { BreadcrumbNode } from '../components/pdf-lab/PdfLabLabelingExport'

export interface SectionProvenance {
  pdf_sha256: string
  page: number
  bbox: [number, number, number, number] | null
}

export interface SectionTreeNodeV2 {
  id: string
  title: string
  parent_id: string | null
  children: string[]
  doc_order: number
  provenance: SectionProvenance
}

export interface SectionTreeV2 {
  schema: 'pdf_oxide.section_tree.v2'
  sections: SectionTreeNodeV2[]
}

export interface NormalizedSectionPath {
  breadcrumb: string[]
  breadcrumbNodes: BreadcrumbNode[]
  provenance: SectionProvenance[]
}

function isFiniteBBox(value: unknown): value is [number, number, number, number] {
  return Array.isArray(value)
    && value.length === 4
    && value.every(coordinate => typeof coordinate === 'number' && Number.isFinite(coordinate))
}

export function assertSectionTreeV2(value: unknown): asserts value is SectionTreeV2 {
  if (!value || typeof value !== 'object') throw new Error('section tree must be an object')
  const tree = value as Partial<SectionTreeV2>
  if (tree.schema !== 'pdf_oxide.section_tree.v2') {
    throw new Error('section tree schema must be pdf_oxide.section_tree.v2')
  }
  if (!Array.isArray(tree.sections)) throw new Error('section tree sections must be an array')

  const ids = new Set<string>()
  const orders = new Set<number>()
  for (const section of tree.sections) {
    if (!section || typeof section !== 'object') throw new Error('section tree node must be an object')
    if (typeof section.id !== 'string' || !section.id || ids.has(section.id)) {
      throw new Error('section tree node id must be unique and non-empty')
    }
    ids.add(section.id)
    if (typeof section.title !== 'string' || !section.title.trim()) {
      throw new Error(`section ${section.id} title must be non-empty`)
    }
    if (section.parent_id !== null && typeof section.parent_id !== 'string') {
      throw new Error(`section ${section.id} parent_id must be a string or null`)
    }
    if (!Array.isArray(section.children) || !section.children.every(child => typeof child === 'string')) {
      throw new Error(`section ${section.id} children must be an array of ids`)
    }
    if (!Number.isInteger(section.doc_order) || section.doc_order < 0 || orders.has(section.doc_order)) {
      throw new Error(`section ${section.id} doc_order must be a unique non-negative integer`)
    }
    orders.add(section.doc_order)
    const provenance = section.provenance
    if (
      !provenance
      || typeof provenance !== 'object'
      || typeof provenance.pdf_sha256 !== 'string'
      || !/^[0-9a-f]{64}$/.test(provenance.pdf_sha256)
      || !Number.isInteger(provenance.page)
      || provenance.page < 0
      || (provenance.bbox !== null && !isFiniteBBox(provenance.bbox))
    ) {
      throw new Error(`section ${section.id} provenance is invalid`)
    }
  }

  for (const section of tree.sections) {
    if (section.parent_id !== null && !ids.has(section.parent_id)) {
      throw new Error(`section ${section.id} has unknown parent ${section.parent_id}`)
    }
    for (const child of section.children) {
      if (!ids.has(child)) throw new Error(`section ${section.id} has unknown child ${child}`)
    }
  }
}

export function normalizeSectionPath(treeValue: unknown, sectionId: string): NormalizedSectionPath {
  assertSectionTreeV2(treeValue)
  const byId = new Map(treeValue.sections.map(section => [section.id, section]))
  const path: SectionTreeNodeV2[] = []
  const visited = new Set<string>()
  let current = byId.get(sectionId)
  if (!current) throw new Error(`unknown section ${sectionId}`)

  while (current) {
    if (visited.has(current.id)) throw new Error(`section tree cycle at ${current.id}`)
    visited.add(current.id)
    path.unshift(current)
    current = current.parent_id === null ? undefined : byId.get(current.parent_id)
  }

  return {
    breadcrumb: path.map(section => section.title),
    breadcrumbNodes: path.map((section, level) => ({
      level,
      kind: level === 0 ? 'document' : 'section',
      label: section.title,
      id: section.id,
      node_id: section.id,
      parent_node_id: section.parent_id ?? undefined,
      source: 'section_hierarchy',
      page: section.provenance.page,
    })),
    provenance: path.map(section => section.provenance),
  }
}
