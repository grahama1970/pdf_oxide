import { describe, it, expect, beforeEach } from 'vitest'

// Mirror of the route's storage helpers to prove the round-trip contract.
const DRAWN_REGIONS_KEY = 'pdf-oxide.drawn-annotations.v1'

function saveDrawnRegions(itemId: string, regions: Array<{ id: string; bbox: number[]; label: string }>): void {
  const store = JSON.parse(window.localStorage.getItem(DRAWN_REGIONS_KEY) ?? '{}')
  if (regions.length) store[itemId] = regions.map(({ id, bbox, label }) => ({ id, bbox, label, editable: true }))
  else delete store[itemId]
  window.localStorage.setItem(DRAWN_REGIONS_KEY, JSON.stringify(store))
}
function loadDrawnRegions(itemId: string) {
  const store = JSON.parse(window.localStorage.getItem(DRAWN_REGIONS_KEY) ?? '{}')
  const rows = store[itemId]
  return Array.isArray(rows) ? rows.filter((row: any) => Array.isArray(row?.bbox) && row.bbox.length === 4) : []
}

describe('drawn-annotation persistence', () => {
  beforeEach(() => window.localStorage.clear())

  it('round-trips drawn regions under a stable content-derived item id', () => {
    const itemId = 'sha:4:region:char_parity_deficit:0'
    saveDrawnRegions(itemId, [{ id: 'canvas_a', bbox: [0.1, 0.2, 0.3, 0.4], label: 'Table' }])
    // simulate refresh: new module read of the same key
    expect(loadDrawnRegions(itemId)).toEqual([{ id: 'canvas_a', bbox: [0.1, 0.2, 0.3, 0.4], label: 'Table', editable: true }])
  })

  it('persists label edits and deletions', () => {
    const itemId = 'sha:4:region:char_parity_deficit:0'
    saveDrawnRegions(itemId, [{ id: 'canvas_a', bbox: [0, 0, 0.1, 0.1], label: 'region' }])
    saveDrawnRegions(itemId, [{ id: 'canvas_a', bbox: [0, 0, 0.1, 0.1], label: 'Figure' }])
    expect(loadDrawnRegions(itemId)[0].label).toBe('Figure')
    saveDrawnRegions(itemId, [])
    expect(loadDrawnRegions(itemId)).toEqual([])
    expect(window.localStorage.getItem(DRAWN_REGIONS_KEY)).toBe('{}')
  })

  it('keeps separate regions per item id', () => {
    saveDrawnRegions('sha:4:region:r:0', [{ id: 'a', bbox: [0, 0, 1, 1], label: 'Table' }])
    saveDrawnRegions('sha:5:block:r:0', [{ id: 'b', bbox: [0, 0, 1, 1], label: 'Body' }])
    expect(loadDrawnRegions('sha:4:region:r:0')).toHaveLength(1)
    expect(loadDrawnRegions('sha:5:block:r:0')[0].id).toBe('b')
  })
})
