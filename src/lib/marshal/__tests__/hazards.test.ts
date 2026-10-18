import { describe, expect, it } from 'vitest'
import { clearHazard, HAZARD_PRESETS, stageHazard } from '../hazards'
import { POINTS } from '../points'
import { ScriptedDrivingSystem } from '../testing/scriptedSystem'

const EXPECTED_IDS = [
  'hz-truck-1',
  'hz-box-1',
  'hz-box-2',
  'hz-sign-1',
  'hz-cone-1',
  'hz-cone-2',
  'hz-cone-3',
  'hz-worker-1',
]

describe('stageHazard', () => {
  it('spawns the forklift preset at the finishing corner, rotated by the point yaw', () => {
    const ds = new ScriptedDrivingSystem()
    const staged = stageHazard(ds, 'finishing_corner', 'forklift_unloading')
    expect(staged.ids).toEqual(EXPECTED_IDS)
    expect(staged.description).toContain('worker')

    const props = ds.listProps()
    expect(props.map((prop) => prop.kind)).toEqual([
      'truck',
      'box',
      'box',
      'cone',
      'cone',
      'cone',
      'cone',
      'worker',
    ])
    // finishing has yaw 180: forward 6 / left 3.5 maps to x - 6, y - 3.5.
    const truck = props[0]
    expect(truck.x).toBeCloseTo(POINTS.finishing.x - 6)
    expect(truck.y).toBeCloseTo(POINTS.finishing.y - 3.5)
    expect(truck.yaw).toBe(180)
  })

  it('rotates offsets by any point yaw', () => {
    const ds = new ScriptedDrivingSystem()
    const spawned: { id: string; x: number; y: number; yaw: number }[] = []
    ds.getTransform = () => ({ x: 10, y: 20, yaw: 90 })
    ds.spawnProp = (id, _kind, x, y, yaw) => {
      spawned.push({ id, x, y, yaw })
    }
    stageHazard(ds, 'finishing_corner', 'forklift_unloading')
    const preset = HAZARD_PRESETS.forklift_unloading.props
    for (const [index, prop] of preset.entries()) {
      expect(spawned[index].id).toBe(prop.id)
      expect(spawned[index].x).toBeCloseTo(10 - prop.left)
      expect(spawned[index].y).toBeCloseTo(20 + prop.forward)
      expect(spawned[index].yaw).toBe(90 + prop.yaw)
    }
  })

  it('clearHazard removes every staged prop', () => {
    const ds = new ScriptedDrivingSystem()
    const staged = stageHazard(ds, 'finishing_corner', 'forklift_unloading')
    clearHazard(ds, staged.ids)
    expect(ds.listProps()).toEqual([])
  })
})
