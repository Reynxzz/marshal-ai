import { describe, expect, it } from 'vitest'
import { POINTS } from '#/lib/marshal/points'
import { PLANT_SEGMENTS } from '#/lib/simview/plantMap'
import { boundsOf, createLaneGraph, type LaneSegment } from '../world/plantMap'

describe('lane graph', () => {
  const graph = createLaneGraph()

  it('is valid on the contract map', () => {
    expect(graph.validate()).toEqual([])
  })

  it('contains every leg the dashboard viewer draws', () => {
    for (const [from, to] of PLANT_SEGMENTS) {
      const edge = graph.edges.find(
        (item) => (item.from === from && item.to === to) || (item.from === to && item.to === from),
      )
      expect(edge, `${from} -> ${to}`).toBeDefined()
    }
  })

  it('measures straight-line distances and neighbours', () => {
    expect(graph.distance('line_end', 'buffer_zone_b')).toBeCloseTo(10)
    expect(graph.neighbors('yard.entry')).toContain('yard.Y1')
    expect(graph.neighbors('line_end')).toEqual(['buffer_zone_b'])
  })

  it('reports unknown legs and unreachable points', () => {
    const broken = createLaneGraph(POINTS, [['line_end', 'nope'] as unknown as LaneSegment])
    const problems = broken.validate()
    expect(problems.some((note) => note.includes('unknown point'))).toBe(true)
    expect(problems.some((note) => note.includes('unreachable'))).toBe(true)
  })

  it('routes between named points over the lane graph', () => {
    const route = graph.route('line_end', 'yard.Y1')
    expect(route[0]).toBe('line_end')
    expect(route.at(-1)).toBe('yard.Y1')
    for (let index = 1; index < route.length; index++) {
      expect(graph.neighbors(route[index - 1])).toContain(route[index])
    }
    expect(graph.route('test_track', 'test_track')).toEqual(['test_track'])
    expect(graph.route('rework.electrical', 'test_track')).toEqual([
      'rework.electrical',
      'test_track',
    ])
    expect(() => graph.route('line_end', 'nope' as never)).toThrow(/unknown point/)
  })

  it('exposes the named-point bounds', () => {
    const bounds = boundsOf()
    expect(bounds).toEqual({ minX: -30.75, maxX: 150.75, minY: -109.25, maxY: 5.25 })
  })
})
