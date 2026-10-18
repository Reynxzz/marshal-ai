import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import { applyCarlaPose, carlaToThree, carlaYawToThree } from '../coords'

describe('coords', () => {
  it('maps CARLA (x, y, z) to three (x, z, y)', () => {
    expect(carlaToThree(10, -20, 1.5)).toEqual([10, 1.5, -20])
    expect(carlaToThree(10, -20)).toEqual([10, 0, -20])
  })

  it('negates yaw for the right-handed frame', () => {
    expect(carlaYawToThree(90)).toBeCloseTo(-Math.PI / 2)
    expect(carlaYawToThree(-45)).toBeCloseTo(Math.PI / 4)
  })

  it('applies a pose to an object', () => {
    const object = new THREE.Group()
    applyCarlaPose(object, { x: 5, y: -8, yaw: 90, z: 1 })
    expect(object.position.x).toBe(5)
    expect(object.position.y).toBe(1)
    expect(object.position.z).toBe(-8)
    expect(object.rotation.y).toBeCloseTo(-Math.PI / 2)
  })
})
