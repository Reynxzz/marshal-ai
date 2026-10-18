import { describe, expect, it } from 'vitest'
import { createWorld, world, YARD_SLOTS } from '../world'

describe('world routing', () => {
  it('picks route A (finishing corners) from report_point to finishing', () => {
    const path = world.route('report_point', 'finishing')
    expect(path).toEqual([
      'report_point',
      'finishing_corner.p1',
      'finishing_corner.p2',
      'finishing_corner.p3',
      'finishing',
    ])
    const viaA = world.routeLength(path)
    const viaB = world.routeLength(['report_point', 'lane_c', 'finishing'])
    expect(viaA).toBeLessThan(viaB)
  })

  it('routes line_end to a yard slot through gate, track, report and finishing', () => {
    const path = world.route('line_end', 'yard.Y1')
    expect(path[0]).toBe('line_end')
    expect(path.at(-1)).toBe('yard.Y1')
    expect(path).toContain('inspection_gate')
    expect(path).toContain('test_track')
    expect(path).toContain('finishing')
  })

  it('is deterministic', () => {
    const a = createWorld()
    const b = createWorld()
    expect(a.route('line_end', 'yard.Y5')).toEqual(b.route('line_end', 'yard.Y5'))
  })

  it('exposes every yard slot', () => {
    expect(YARD_SLOTS).toEqual([
      'yard.Y1',
      'yard.Y2',
      'yard.Y3',
      'yard.Y4',
      'yard.Y5',
      'yard.Y6',
      'yard.Y7',
      'yard.Y8',
    ])
  })

  it('can avoid a closed zone', () => {
    const path = world.route('report_point', 'finishing', { avoid: ['finishing'] })
    expect(path).toEqual(['report_point', 'lane_c', 'finishing'])
  })

  it('throws on unknown points', () => {
    expect(() => world.point('nope')).toThrow()
    expect(() => world.route('line_end', 'nope')).toThrow()
  })
})
