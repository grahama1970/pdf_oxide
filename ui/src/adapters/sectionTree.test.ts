import { describe, expect, it } from 'vitest'
import { normalizeSectionTree, sectionPath } from './sectionTree'

const PDF_SHA = 'c'.repeat(64)

function section(id: string, title: string, parentId: string | null, children: string[], depth: number, order: number) {
  return {
    id,
    title,
    level: depth + 1,
    parent_id: parentId,
    children,
    depth,
    doc_order: order,
    page_start: order,
    page_end: order + 1,
    provenance: { pdf_sha256: PDF_SHA, page: order, bbox: [0.1, 0.1, 0.5, 0.1] },
    block_ids: [`block-${id}`],
  }
}

describe('section tree v2 adapter', () => {
  it('builds stable ordered paths and block lookup', () => {
    const tree = normalizeSectionTree({
      schema: 'pdf_oxide.section_tree.v2',
      sections: [
        section('root', 'Chapter', null, ['child'], 0, 0),
        section('child', 'Specific section', 'root', [], 1, 1),
      ],
    })
    expect(sectionPath(tree, 'child')).toEqual(['Chapter', 'Specific section'])
    expect(tree.byBlockId.get('block-child')?.id).toBe('child')
  })

  it('rejects parent/child inconsistency', () => {
    expect(() => normalizeSectionTree({
      sections: [
        section('root', 'Chapter', null, [], 0, 0),
        section('child', 'Specific section', 'root', [], 1, 1),
      ],
    })).toThrow(/missing from parent/)
  })
})
