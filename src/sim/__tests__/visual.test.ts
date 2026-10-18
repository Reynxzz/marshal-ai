import { describe, expect, it } from 'vitest'
import { colourHex, WRONG_COLOUR } from '#/lib/colors'
import { VisualInspector, visualVerdict } from '../flows/visual'
import { makeHost, snap, stubAi } from './helpers'

const buildSheet = { model: 'M', colour: 'blue' as const, options: [] }

describe('visualVerdict', () => {
  it('FAILs on a confirmed mismatch', () => {
    expect(visualVerdict([{ field: 'colour', declared: 'blue', seen: 'red' }], [], 0.9)).toBe(
      'FAIL',
    )
  })
  it('PASSes a clean car', () => {
    expect(visualVerdict([], [], 0.9)).toBe('PASS')
  })
  it('REVIEWs ambiguous findings', () => {
    expect(visualVerdict([], [{ kind: 'scratch' }], 0.7)).toBe('REVIEW')
  })
})

describe('VisualInspector (offline)', () => {
  it('FAILs a red car declared blue', async () => {
    const result = await new VisualInspector(makeHost()).inspect(
      snap('V1', WRONG_COLOUR.hex),
      buildSheet,
    )
    expect(result.verdict).toBe('FAIL')
    expect(result.mismatches[0]).toMatchObject({ field: 'colour', declared: 'blue', seen: 'red' })
  })

  it('PASSes a matching car', async () => {
    const result = await new VisualInspector(makeHost()).inspect(
      snap('V2', colourHex('blue')),
      buildSheet,
    )
    expect(result.verdict).toBe('PASS')
  })

  it('REVIEWs when a stub reports an ambiguous defect', async () => {
    const ai = stubAi(async (task) => {
      if (task === 'visual.screen') return { hasDefect: true, confidence: 0.7 }
      if (task === 'visual.defects') return { findings: [{ kind: 'scratch' }] }
      if (task === 'visual.match') return { mismatches: [], confidence: 0.7 }
      throw new Error('unexpected task')
    })
    const result = await new VisualInspector(makeHost({ ai })).inspect(
      snap('V3', colourHex('grey')),
      buildSheet,
    )
    expect(result.verdict).toBe('REVIEW')
    expect(result.findings.length).toBeGreaterThan(0)
  })
})
