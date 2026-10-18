import { describe, expect, it } from 'vitest'
import { POINTS, type PointName } from '#/lib/marshal/points'
import { PLANT_SEGMENTS } from '#/lib/simview/plantMap'
import { boundsOf, createLaneGraph, type LaneSegment } from '../world/plantMap'

describe('lane graph', () => {
  const graph = createLaneGraph()

  it('is valid on the contract map', () => {
    expect(graph.validate()).toEqual([])
  })

  it('can drive every leg the plain viewer draws (one way, round the loop where needed)', () => {
    for (const [from, to] of PLANT_SEGMENTS) {
      const route = graph.route(from as PointName, to as PointName)
      expect(route.at(-1), `${from} -> ${to}`).toBe(to)
    }
  })

  it('follows the CARLA loop: each leg starts and ends on its points, in 1 m steps', () => {
    for (const edge of graph.edges) {
      const path = graph.path(edge.from, edge.to)
      const a = POINTS[edge.from]
      const b = POINTS[edge.to]
      expect(Math.hypot(path[0].x - a.x, path[0].y - a.y)).toBeLessThan(1e-6)
      const end = path[path.length - 1]
      expect(Math.hypot(end.x - b.x, end.y - b.y)).toBeLessThan(1e-6)
      for (let i = 1; i < path.length; i++) {
        expect(Math.hypot(path[i].x - path[i - 1].x, path[i].y - path[i - 1].y)).toBeLessThan(1.6)
      }
    }
    // The finishing corner is the north-east bend, not a straight line across the plant.
    const corner = graph.path('report_point', 'finishing')
    expect(Math.max(...corner.map((p) => p.x))).toBeGreaterThan(145)
    // One way: a reworked car comes back to the test track round the loop, past the yard.
    expect(graph.route('rework.electrical', 'test_track')).toEqual([
      'rework.electrical',
      'test_track',
    ])
    expect(Math.min(...graph.path('rework.electrical', 'test_track').map((p) => p.x))).toBeLessThan(
      -25,
    )
    expect(() => graph.path('test_track', 'inspection_gate')).toThrow(/no lane leg/)
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
