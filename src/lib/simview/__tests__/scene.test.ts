import { describe, expect, it } from 'vitest'
import { ScriptedDrivingSystem } from '#/lib/marshal/testing/scriptedSystem'
import { chooseFocus } from '../focus'
import { buildPlantLayout } from '../plantMap'
import { createPlantScene } from '../scene'
import { BLOCKING_RADIUS_M, colourHexFor, readSimSnapshot } from '../snapshot'
import { car, openCase, stateWith } from './fixtures'

function engine(): ScriptedDrivingSystem {
  const ds = new ScriptedDrivingSystem()
  ds.spawn('P001', { at: 'test_track', colour: [0.1, 0.25, 0.75] })
  ds.spawn('P002', { at: 'finishing', colour: [0.92, 0.92, 0.92] })
  ds.spawnProp('blk-box', 'box', 84, -109.25, 180)
  ds.spawnProp('far-cone', 'cone', 0, 100, 0)
  return ds
}

const state = stateWith(
  [
    car('P001', 'test_track', { location: 'test_track' }),
    car('P002', 'waiting', { location: 'finishing', colour: 'white' }),
    car('P009', 'parked', { location: 'yard.Y3' }),
  ],
  [openCase('AC-P002-1', 'P002', 10)],
)

describe('readSimSnapshot', () => {
  it('reads engine poses, highlights the focused car and marks props that block it', () => {
    const focus = chooseFocus(state, { selected: null })
    const snap = readSimSnapshot(engine(), state, focus)
    // P009 is not in the engine (zero pose): it is not drawn.
    expect(snap.cars.map((item) => item.vin)).toEqual(['P001', 'P002'])
    expect(snap.cars[0]).toMatchObject({ x: 62, y: 5.25, yaw: 0, highlight: null })
    expect(snap.cars[1]).toMatchObject({ colour: '#ebebeb', highlight: 'needs' })
    expect(snap.focusPose).toEqual({ x: 90, y: -109.25, yaw: 180 })
    expect(snap.props.find((prop) => prop.id === 'blk-box')?.blocking).toBe(true)
    expect(snap.props.find((prop) => prop.id === 'far-cone')?.blocking).toBe(false)
    expect(BLOCKING_RADIUS_M).toBe(15)
  })

  it('marks a plain selection blue and works without an engine', () => {
    const focus = chooseFocus(state, { selected: 'P001' })
    expect(readSimSnapshot(engine(), state, focus).cars[0].highlight).toBe('selected')
    expect(readSimSnapshot(null, state, focus)).toMatchObject({
      cars: [],
      props: [],
      focusPose: null,
    })
  })

  it('converts build colours to hex', () => {
    expect(colourHexFor('blue')).toBe('#1a40bf')
    expect(colourHexFor('mauve')).toBe('#808080')
  })
})

describe('createPlantScene', () => {
  it('builds the plant and syncs cars, props, trucks and the focus label without WebGL', () => {
    const plant = createPlantScene(buildPlantLayout())
    const before = plant.scene.children.length
    const focus = chooseFocus(state, { selected: null })
    const snap = {
      ...readSimSnapshot(engine(), state, focus),
      trucks: [{ id: 'T-1', state: 'loading' as const, slots: [] }],
    }
    plant.sync(snap, 1)
    expect(plant.carIds()).toEqual(['P001', 'P002'])
    expect(plant.propIds()).toEqual(['blk-box', 'far-cone'])
    expect(plant.scene.children.length).toBe(before + 2 + 2 + 1)
    const focusLabel = plant.labels().find((label) => label.kind === 'focus')
    expect(focusLabel).toMatchObject({ text: 'P002 · finishing', colour: '#E39B2E' })
    expect(plant.labels().some((label) => label.text === 'Inspection gate')).toBe(true)

    plant.sync({ cars: [], props: [], trucks: [], focus: null, focusPose: null })
    expect(plant.carIds()).toEqual([])
    expect(plant.propIds()).toEqual([])
    expect(plant.scene.children.length).toBe(before)
    expect(plant.labels().some((label) => label.kind === 'focus')).toBe(false)
    plant.dispose()
    expect(plant.scene.children.length).toBe(0)
  })
})
