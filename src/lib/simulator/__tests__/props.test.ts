import { describe, expect, it } from 'vitest'
import { FLAT_PROP_HEIGHT_M, isPropKind, PROP_FOOTPRINTS, propFootprint } from '../actors/props'

describe('props', () => {
  it('has a footprint for every kind', () => {
    expect(Object.keys(PROP_FOOTPRINTS).sort()).toEqual([
      'barrel',
      'box',
      'cone',
      'truck',
      'worker',
    ])
    expect(propFootprint('box')).toEqual({ length: 1.2, width: 1.2, height: 1, moving: false })
    expect(propFootprint('worker').moving).toBe(true)
    expect(propFootprint('truck')).toMatchObject({ length: 8, width: 2.5 })
  })

  it('returns copies and guards kinds', () => {
    const footprint = propFootprint('box')
    footprint.length = 99
    expect(PROP_FOOTPRINTS.box.length).toBe(1.2)
    expect(isPropKind('cone')).toBe(true)
    expect(isPropKind('crate')).toBe(false)
    expect(FLAT_PROP_HEIGHT_M).toBe(0.15)
  })
})
