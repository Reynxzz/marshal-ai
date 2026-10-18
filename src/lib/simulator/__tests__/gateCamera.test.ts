import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { fitGateFrames } from '#/lib/marshal/frames'
import { POINTS } from '#/lib/marshal/points'
import { offlineGate } from '#/lib/serverFns/logic'
import {
  createGateCamera,
  GATE_VIEWS,
  type GateRenderer,
  gateCameraPoses,
} from '../cameras/gateCamera'

function fakeRig() {
  const rendered: Array<{ x: number; y: number; z: number }> = []
  const renderer: GateRenderer = {
    domElement: {} as HTMLCanvasElement,
    render: (_scene, camera) => {
      rendered.push({ x: camera.position.x, y: camera.position.y, z: camera.position.z })
    },
  }
  return { renderer, rendered }
}

describe('gate camera poses', () => {
  it('matches the TEAM_PLAN M3.1 rig around inspection_gate', () => {
    const poses = Object.fromEntries(gateCameraPoses().map((pose) => [pose.view, pose]))
    expect(poses.front).toMatchObject({ x: 39, y: 5.25, z: 1.5, yaw: 180, pitch: -5, fov: 90 })
    expect(poses.rear).toMatchObject({ x: 25, y: 5.25, z: 1.5, yaw: 0, pitch: -5, fov: 90 })
    expect(poses.left).toMatchObject({ x: 32, y: 0.25, z: 1.2, yaw: 90 })
    expect(poses.right).toMatchObject({ x: 32, y: 10.25, z: 1.2, yaw: -90 })
    expect(Object.keys(poses)).toEqual([...GATE_VIEWS])
  })

  it('rotates the rig with the gate heading', () => {
    const poses = Object.fromEntries(
      gateCameraPoses({ x: 0, y: 0, yaw: 90 }).map((pose) => [pose.view, pose]),
    )
    expect(poses.front.x).toBeCloseTo(0)
    expect(poses.front.y).toBeCloseTo(7)
    expect(poses.front.yaw).toBe(270)
    expect(poses.rear.x).toBeCloseTo(0)
    expect(poses.rear.y).toBeCloseTo(-7)
    expect(poses.rear.yaw).toBe(90)
    expect(poses.left.x).toBeCloseTo(5)
    expect(poses.left.y).toBeCloseTo(0)
    expect(poses.left.yaw).toBe(180)
    expect(poses.right.x).toBeCloseTo(-5)
    expect(poses.right.y).toBeCloseTo(0)
    expect(poses.right.yaw).toBe(0)
  })
})

describe('gate camera capture', () => {
  it('renders each requested view and leaves the rest empty', async () => {
    const rig = fakeRig()
    const camera = createGateCamera({
      scene: new THREE.Scene(),
      renderer: rig.renderer,
      encode: () => 'JPEG',
    })
    const frames = await camera.capture('VIN-1', ['front', 'left'])
    expect(frames).toEqual({ front: 'JPEG', rear: '', left: 'JPEG', right: '' })
    expect(rig.rendered).toHaveLength(2)
    expect(rig.rendered[0]).toEqual({ x: 39, y: 1.5, z: 5.25 })
    expect(rig.rendered[1]).toEqual({ x: 32, y: 1.2, z: 0.25 })
  })

  it('captures all four views in order', async () => {
    const rig = fakeRig()
    const camera = createGateCamera({
      scene: new THREE.Scene(),
      renderer: rig.renderer,
      encode: () => 'FRAME',
    })
    const frames = await camera.capture('VIN-1', [...GATE_VIEWS])
    expect(frames).toEqual({ front: 'FRAME', rear: 'FRAME', left: 'FRAME', right: 'FRAME' })
    expect(rig.rendered).toHaveLength(4)
  })

  it('hands the frames to the offline gate check in the flow shape (M3.3 handshake)', async () => {
    const rig = fakeRig()
    const camera = createGateCamera({
      scene: new THREE.Scene(),
      gate: POINTS.inspection_gate,
      renderer: rig.renderer,
      encode: () => 'FRAME',
    })
    const captured = await camera.capture('VIN-1', [...GATE_VIEWS])
    const { frames, dropped } = fitGateFrames(captured)
    expect(dropped).toEqual([])
    const hints = {
      front: 'colour=blue',
      rear: 'colour=blue',
      left: 'colour=blue',
      right: 'colour=blue',
    }
    const result = offlineGate({ vin: 'VIN-1', expectedColour: 'blue', frames, hints })
    expect(result).toMatchObject({ pass: true, colour: 'blue' })
  })
})
