import { describe, expect, it } from 'vitest'
import { placeIncident } from '../incident'
import { world } from '../world'

describe('placeIncident blockers', () => {
  const route = world.route('line_end', 'inspection_gate')

  it('defaults to box + worker', () => {
    const p = placeIncident(world, route)
    expect(p?.actors.map((a) => a.kind)).toEqual(['box', 'walker'])
    expect(p?.situation).toContain('worker')
  })

  it('places a lone box without a person', () => {
    const p = placeIncident(world, route, ['box'])
    expect(p?.actors.map((a) => a.kind)).toEqual(['box'])
    expect(p?.situation).not.toMatch(/worker|person|people|operator|technician/i)
  })

  it('places a forklift work zone', () => {
    const p = placeIncident(world, route, ['vehicle', 'box', 'walker'])
    expect(p?.actors.map((a) => a.kind).sort()).toEqual(['box', 'vehicle', 'walker'])
    expect(p?.situation).toContain('forklift')
  })
})
