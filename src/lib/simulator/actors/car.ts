// Engine car mesh (TEAM_PLAN M1.6): 4.9 m x 1.9 m body, 2.85 m wheelbase, with emissive low beam,
// brake bar, reverse lights and indicators. Lamp faults (M5.3) are visual only: the lamp mesh
// goes dark while telemetry keeps reporting ON.

import * as THREE from 'three'
import { applyCarlaPose, type CarlaPose } from '../coords'

/** Car dimensions, metres (TEAM_PLAN: critical for parking geometry). */
export const CAR_LENGTH_M = 4.9
export const CAR_WIDTH_M = 1.9
export const CAR_WHEELBASE_M = 2.85

/** Lamp states the engine drives from telemetry. */
export type CarLamps = {
  headlights: boolean
  brake_lights: boolean
  reverse: boolean
  indicator_left: boolean
  indicator_right: boolean
}

/** A lamp group a state or fault controls. */
export type LampName = keyof CarLamps

/** Lamp faults the sim supports (visual only). */
export type CarFault = 'headlights' | 'brake_lights' | 'indicators'

type CarParts = {
  body: THREE.MeshStandardMaterial
  lamps: Record<LampName, THREE.MeshStandardMaterial[]>
  state: CarLamps
  faults: Set<CarFault>
}

/** An engine car mesh; lamp internals live on `userData`. */
export type EngineCar = THREE.Group

const LAMP_ON = 1.8
const LAMP_OFF = 0.02

function lampMaterial(colour: number): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: colour,
    emissive: colour,
    emissiveIntensity: LAMP_OFF,
    roughness: 0.3,
  })
}

function part(
  group: THREE.Object3D,
  geometry: THREE.BufferGeometry,
  material: THREE.Material,
  x: number,
  y: number,
  z: number,
): THREE.Mesh {
  const mesh = new THREE.Mesh(geometry, material)
  mesh.position.set(x, y, z)
  group.add(mesh)
  return mesh
}

/**
 * Lamp state after faults: a faulty lamp is visually off (telemetry is unaffected).
 * @param state - Telemetry lamp state.
 * @param faults - Visual faults.
 * @returns Effective lamp state.
 */
export function effectiveLamps(state: CarLamps, faults: ReadonlySet<CarFault>): CarLamps {
  return {
    headlights: state.headlights && !faults.has('headlights'),
    brake_lights: state.brake_lights && !faults.has('brake_lights'),
    reverse: state.reverse,
    indicator_left: state.indicator_left && !faults.has('indicators'),
    indicator_right: state.indicator_right && !faults.has('indicators'),
  }
}

function applyLamps(car: EngineCar): void {
  const parts = car.userData.parts as CarParts
  const effective = effectiveLamps(parts.state, parts.faults)
  for (const name of Object.keys(parts.lamps) as LampName[]) {
    for (const material of parts.lamps[name]) {
      material.emissiveIntensity = effective[name] ? LAMP_ON : LAMP_OFF
    }
  }
}

/**
 * Builds a car whose local +X is the nose.
 * @param colour - Body colour (anything MeshStandardMaterial accepts).
 * @returns Car group; place it with {@link updateCarMesh}.
 */
export function createCarMesh(colour: string | number): EngineCar {
  const group = new THREE.Group()
  group.name = 'car'
  const halfLength = CAR_LENGTH_M / 2
  const halfWidth = CAR_WIDTH_M / 2

  const body = new THREE.MeshStandardMaterial({ color: colour, roughness: 0.35, metalness: 0.25 })
  const glass = new THREE.MeshStandardMaterial({ color: 0x14181d, roughness: 0.15, metalness: 0.4 })
  const trim = new THREE.MeshStandardMaterial({ color: 0x16181c, roughness: 0.9 })
  const tire = new THREE.MeshStandardMaterial({ color: 0x141619, roughness: 0.95 })
  const hub = new THREE.MeshStandardMaterial({ color: 0x9aa1ab, roughness: 0.35, metalness: 0.8 })

  part(group, new THREE.BoxGeometry(CAR_LENGTH_M + 0.06, 0.22, CAR_WIDTH_M - 0.12), trim, 0, 0.4, 0)
  part(group, new THREE.BoxGeometry(CAR_LENGTH_M, 0.52, CAR_WIDTH_M), body, 0, 0.78, 0)
  part(group, new THREE.BoxGeometry(2.3, 0.5, CAR_WIDTH_M - 0.18), glass, -0.15, 1.27, 0)
  part(group, new THREE.BoxGeometry(2.1, 0.07, CAR_WIDTH_M - 0.3), body, -0.15, 1.55, 0)
  const windshield = part(
    group,
    new THREE.BoxGeometry(0.9, 0.06, CAR_WIDTH_M - 0.24),
    glass,
    1.2,
    1.3,
    0,
  )
  windshield.rotation.z = -0.68
  const rearWindow = part(
    group,
    new THREE.BoxGeometry(0.8, 0.06, CAR_WIDTH_M - 0.24),
    glass,
    -1.5,
    1.28,
    0,
  )
  rearWindow.rotation.z = 0.68

  for (const x of [CAR_WHEELBASE_M / 2, -CAR_WHEELBASE_M / 2]) {
    for (const z of [halfWidth - 0.16, -(halfWidth - 0.16)]) {
      const wheel = part(group, new THREE.CylinderGeometry(0.34, 0.34, 0.24, 18), tire, x, 0.34, z)
      wheel.rotation.x = Math.PI / 2
      const cap = part(group, new THREE.CylinderGeometry(0.15, 0.15, 0.26, 12), hub, x, 0.34, z)
      cap.rotation.x = Math.PI / 2
    }
  }

  const headlight = lampMaterial(0xfff2c4)
  const brakeLight = lampMaterial(0xff2a1f)
  const reverseLight = lampMaterial(0xf2f4f8)
  const indicatorLeft = lampMaterial(0xffa020)
  const indicatorRight = lampMaterial(0xffa020)
  for (const side of [1, -1]) {
    const indicator = side === 1 ? indicatorRight : indicatorLeft
    part(
      group,
      new THREE.BoxGeometry(0.1, 0.16, 0.5),
      headlight,
      halfLength - 0.04,
      0.88,
      side * 0.55,
    )
    part(
      group,
      new THREE.BoxGeometry(0.08, 0.14, 0.4),
      reverseLight,
      -halfLength + 0.04,
      0.9,
      side * 0.6,
    )
    part(
      group,
      new THREE.BoxGeometry(0.1, 0.14, 0.24),
      indicator,
      halfLength - 0.06,
      0.88,
      side * 1.0,
    )
    part(
      group,
      new THREE.BoxGeometry(0.1, 0.14, 0.24),
      indicator,
      -halfLength + 0.06,
      0.9,
      side * 1.0,
    )
  }
  part(group, new THREE.BoxGeometry(0.08, 0.16, 1.3), brakeLight, -halfLength + 0.02, 0.95, 0)

  const parts: CarParts = {
    body,
    lamps: {
      headlights: [headlight],
      brake_lights: [brakeLight],
      reverse: [reverseLight],
      indicator_left: [indicatorLeft],
      indicator_right: [indicatorRight],
    },
    state: {
      headlights: false,
      brake_lights: false,
      reverse: false,
      indicator_left: false,
      indicator_right: false,
    },
    faults: new Set(),
  }
  group.userData.parts = parts
  return group
}

/**
 * Sets the telemetry lamp state; faulty lamps stay visually off.
 * @param car - Car from {@link createCarMesh}.
 * @param state - Lamp state.
 */
export function setCarLamps(car: EngineCar, state: CarLamps): void {
  const parts = car.userData.parts as CarParts
  parts.state = { ...state }
  applyLamps(car)
}

/**
 * Replaces the visual lamp faults (telemetry keeps reporting ON).
 * @param car - Car from {@link createCarMesh}.
 * @param faults - Fault names.
 */
export function setCarFaults(car: EngineCar, faults: ReadonlySet<CarFault>): void {
  const parts = car.userData.parts as CarParts
  parts.faults = new Set(faults)
  applyLamps(car)
}

/**
 * Applies a CARLA pose to the car (position and heading).
 * @param car - Car from {@link createCarMesh}.
 * @param pose - CARLA pose.
 */
export function updateCarMesh(car: EngineCar, pose: CarlaPose): void {
  applyCarlaPose(car, pose)
}
