import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import {
  ASSIST_PERIOD_S,
  CAR_BUFFER_FRAMES,
  type CarCameraRenderer,
  carCameraPoses,
  createCarCamera,
  EVIDENCE_PERIOD_S,
  FrameBuffer,
} from '../cameras/carCamera'

describe('on-board camera poses', () => {
  it('matches the TEAM_PLAN M5.1 rig', () => {
    const poses = Object.fromEntries(
      carCameraPoses({ x: 10, y: 20, yaw: 0 }).map((pose) => [pose.view, pose]),
    )
    expect(poses.assist).toMatchObject({ x: 4, y: 20, z: 3, yaw: 0, pitch: -12, fov: 90 })
    expect(poses.evidence_rear).toMatchObject({ x: 3, y: 20, z: 1.6, yaw: 0, pitch: -5, fov: 38 })
    expect(poses.evidence_front).toMatchObject({
      x: 17.5,
      y: 20,
      z: 1.6,
      yaw: 180,
      pitch: -5,
      fov: 38,
    })
  })

  it('rotates the rig with the car heading', () => {
    const poses = Object.fromEntries(
      carCameraPoses({ x: 0, y: 0, yaw: 90 }).map((pose) => [pose.view, pose]),
    )
    expect(poses.assist.x).toBeCloseTo(0)
    expect(poses.assist.y).toBeCloseTo(-6)
    expect(poses.assist.yaw).toBe(90)
    expect(poses.evidence_front.y).toBeCloseTo(7.5)
    expect(poses.evidence_front.yaw).toBe(270)
  })
})

describe('FrameBuffer', () => {
  it('keeps the newest frames, oldest first', () => {
    const buffer = new FrameBuffer(3)
    for (const frame of ['a', 'b', 'c', 'd']) buffer.push(frame)
    expect(buffer.frames()).toEqual(['b', 'c', 'd'])
    expect(buffer.frames()).toHaveLength(3)
  })

  it('defaults to the eight-frame buffer', () => {
    const buffer = new FrameBuffer()
    for (let index = 0; index < CAR_BUFFER_FRAMES + 2; index++) buffer.push(`f${index}`)
    expect(buffer.frames()).toHaveLength(CAR_BUFFER_FRAMES)
    expect(buffer.frames()[0]).toBe('f2')
  })
})

function fakeRig() {
  let shots = 0
  const renderer: CarCameraRenderer = {
    domElement: {} as HTMLCanvasElement,
    render: () => {
      shots++
    },
  }
  const rig = createCarCamera({
    scene: new THREE.Scene(),
    renderer,
    encode: () => `f-${shots}`,
  })
  return { rig, shots: () => shots }
}

describe('car camera rig', () => {
  const target = { vin: 'VIN-1', pose: { x: 10, y: 20, yaw: 0 } }

  it('buffers evidence every 0.5 s and assist every 1.25 s', () => {
    const { rig } = fakeRig()
    for (let step = 0; step < 10; step++) rig.sample([target], EVIDENCE_PERIOD_S)
    expect(rig.evidence('VIN-1', 'front')).toHaveLength(CAR_BUFFER_FRAMES)
    expect(rig.evidence('VIN-1', 'rear')).toHaveLength(CAR_BUFFER_FRAMES)
    expect(rig.assist('VIN-1')).toHaveLength(4)
    expect(rig.evidence('VIN-2', 'front')).toEqual([])
    expect(rig.assist('VIN-2')).toEqual([])
  })

  it('renders every due capture and buffers the last eight front frames', () => {
    const { rig, shots } = fakeRig()
    rig.sample([target], 5)
    const frames = rig.evidence('VIN-1', 'front')
    // 10 evidence captures x 2 cameras + 4 assist captures x 1 camera.
    expect(shots()).toBe(10 * 2 + Math.floor(5 / ASSIST_PERIOD_S))
    expect(frames).toHaveLength(CAR_BUFFER_FRAMES)
    // front renders first in each evidence pair: 1, 3, ..., 19; the buffer keeps the last 8.
    expect(frames[0]).toBe('f-5')
    expect(frames.at(-1)).toBe('f-19')
  })

  it('accumulates sub-period time and ignores empty or zero samples', () => {
    const { rig, shots } = fakeRig()
    expect(EVIDENCE_PERIOD_S / 2).toBeLessThan(EVIDENCE_PERIOD_S)
    rig.sample([target], EVIDENCE_PERIOD_S / 2)
    expect(shots()).toBe(0)
    rig.sample([], 5)
    expect(shots()).toBe(0)
    rig.sample([target], EVIDENCE_PERIOD_S / 2)
    expect(shots()).toBe(2)
  })
})
