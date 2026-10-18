import * as THREE from 'three'
import type { CarSnapshot } from '#/sim/vehicle'

const BODY_LENGTH = 4.2
const BODY_WIDTH = 1.8
const WHEEL_RADIUS = 0.33
const LAMP_ON_EMISSIVE = 1.6
const LAMP_OFF_EMISSIVE = 0.02

type CarUserData = {
  bodyMaterial: THREE.MeshStandardMaterial
  headlightMaterials: THREE.MeshStandardMaterial[]
  brakeLightMaterials: THREE.MeshStandardMaterial[]
}

function lampMaterial(colour: number): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: colour,
    emissive: colour,
    emissiveIntensity: LAMP_OFF_EMISSIVE,
    roughness: 0.3,
  })
}

function part(
  group: THREE.Group,
  geometry: THREE.BufferGeometry,
  material: THREE.Material,
  x: number,
  y: number,
  z: number,
): THREE.Mesh {
  const mesh = new THREE.Mesh(geometry, material)
  mesh.position.set(x, y, z)
  mesh.castShadow = true
  mesh.receiveShadow = true
  group.add(mesh)
  return mesh
}

function wheel(
  group: THREE.Group,
  x: number,
  z: number,
  tire: THREE.Material,
  hub: THREE.Material,
): void {
  const tireMesh = part(
    group,
    new THREE.CylinderGeometry(WHEEL_RADIUS, WHEEL_RADIUS, 0.24, 18),
    tire,
    x,
    WHEEL_RADIUS,
    z,
  )
  tireMesh.rotation.x = Math.PI / 2
  const hubMesh = part(
    group,
    new THREE.CylinderGeometry(0.15, 0.15, 0.26, 12),
    hub,
    x,
    WHEEL_RADIUS,
    z,
  )
  hubMesh.rotation.x = Math.PI / 2
}

/** Build a car mesh whose local +X is the nose. Caller positions and rotates it. */
export function createCarMesh(car: CarSnapshot): THREE.Group {
  const group = new THREE.Group()

  const bodyMaterial = new THREE.MeshStandardMaterial({
    color: car.colour,
    roughness: 0.35,
    metalness: 0.25,
  })
  const glass = new THREE.MeshStandardMaterial({ color: 0x14181d, roughness: 0.15, metalness: 0.4 })
  const trim = new THREE.MeshStandardMaterial({ color: 0x16181c, roughness: 0.9 })
  const tire = new THREE.MeshStandardMaterial({ color: 0x141619, roughness: 0.95 })
  const hub = new THREE.MeshStandardMaterial({ color: 0x9aa1ab, roughness: 0.35, metalness: 0.8 })

  part(group, new THREE.BoxGeometry(BODY_LENGTH + 0.06, 0.22, BODY_WIDTH - 0.12), trim, 0, 0.4, 0)
  part(group, new THREE.BoxGeometry(BODY_LENGTH, 0.52, BODY_WIDTH), bodyMaterial, 0, 0.78, 0)

  part(group, new THREE.BoxGeometry(2.0, 0.5, BODY_WIDTH - 0.18), glass, -0.15, 1.27, 0)
  part(group, new THREE.BoxGeometry(1.86, 0.07, BODY_WIDTH - 0.3), bodyMaterial, -0.15, 1.55, 0)

  const windshield = part(
    group,
    new THREE.BoxGeometry(0.85, 0.06, BODY_WIDTH - 0.24),
    glass,
    1.08,
    1.29,
    0,
  )
  windshield.rotation.z = -0.68
  const rearWindow = part(
    group,
    new THREE.BoxGeometry(0.75, 0.06, BODY_WIDTH - 0.24),
    glass,
    -1.4,
    1.28,
    0,
  )
  rearWindow.rotation.z = 0.68

  part(group, new THREE.BoxGeometry(0.06, 0.18, 1.1), trim, 2.12, 0.62, 0)

  for (const x of [1.38, -1.35]) {
    for (const z of [0.84, -0.84]) wheel(group, x, z, tire, hub)
  }

  const headlightMaterial = lampMaterial(0xfff2c4)
  const brakeLightMaterial = lampMaterial(0xff2a1f)
  for (const z of [0.55, -0.55]) {
    part(group, new THREE.BoxGeometry(0.1, 0.16, 0.44), headlightMaterial, 2.11, 0.88, z)
    part(group, new THREE.BoxGeometry(0.08, 0.14, 0.46), brakeLightMaterial, -2.11, 0.92, z)
  }

  const data: CarUserData = {
    bodyMaterial,
    headlightMaterials: [headlightMaterial],
    brakeLightMaterials: [brakeLightMaterial],
  }
  group.userData = data

  updateCarMesh(group, car)
  return group
}

/** Sync colour, lamps, position and heading from a snapshot. */
export function updateCarMesh(group: THREE.Group, car: CarSnapshot): void {
  const data = group.userData as CarUserData
  data.bodyMaterial.color.set(car.colour)
  for (const material of data.headlightMaterials) {
    material.emissiveIntensity = car.lamps.headlights ? LAMP_ON_EMISSIVE : LAMP_OFF_EMISSIVE
  }
  for (const material of data.brakeLightMaterials) {
    material.emissiveIntensity = car.lamps.brake_lights ? LAMP_ON_EMISSIVE : LAMP_OFF_EMISSIVE
  }

  group.position.set(car.x, 0, car.y)
  group.rotation.y = -car.yaw
}
