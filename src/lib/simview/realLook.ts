// Daylight look for the camera frames (the live backend's gate, evidence and assist shots, which
// Gemini judges): a gradient sky, a sun with soft shadows that follows the shot, and props closer
// to the real thing than primitives (a worker in hi-vis with a helmet, a taped carton). Builds
// without WebGL (jsdom); shadows only render where the renderer enables them.

import * as THREE from 'three'

/** Daylight palette. */
export const DAYLIGHT = {
  zenith: 0x6f9ccc,
  horizon: 0xdfe7ee,
  haze: 0xd3dde6,
  concrete: 0x9a9b96,
  sun: 0xfff3e0,
} as const

/** Shadow box around the shot, metres each way: covers a car and its neighbours at full detail. */
const SHADOW_HALF_M = 30

export type Daylight = {
  /** Moves the sun (and its shadow box) to keep shadows sharp around a CARLA-frame point. */
  focus(x: number, y: number): void
}

function lerpColour(a: number, b: number, t: number): [number, number, number] {
  const ca = new THREE.Color(a)
  const cb = new THREE.Color(b)
  return [ca.r + (cb.r - ca.r) * t, ca.g + (cb.g - ca.g) * t, ca.b + (cb.b - ca.b) * t]
}

/**
 * Sky dome, haze, sky/ground fill and a shadow-casting sun.
 * @param scene - Target scene.
 * @param center - Plant centre, CARLA frame.
 * @returns Handle to point the sun's shadows at the current shot.
 */
export function addDaylight(scene: THREE.Scene, center: { x: number; y: number }): Daylight {
  scene.background = new THREE.Color(DAYLIGHT.haze)
  scene.fog = new THREE.Fog(DAYLIGHT.haze, 220, 650)

  // Inside the camera's far plane (600 m), so the dome is never clipped.
  const dome = new THREE.SphereGeometry(520, 32, 16)
  const position = dome.getAttribute('position')
  const colours: number[] = []
  for (let i = 0; i < position.count; i++) {
    const t = Math.max(0, Math.min(1, position.getY(i) / 260))
    colours.push(...lerpColour(DAYLIGHT.horizon, DAYLIGHT.zenith, Math.sqrt(t)))
  }
  dome.setAttribute('color', new THREE.Float32BufferAttribute(colours, 3))
  const sky = new THREE.Mesh(
    dome,
    new THREE.MeshBasicMaterial({
      vertexColors: true,
      side: THREE.BackSide,
      fog: false,
      depthWrite: false,
    }),
  )
  sky.name = 'sky'
  sky.position.set(center.x, 0, center.y)
  scene.add(sky)

  scene.add(new THREE.HemisphereLight(0xe2ecf6, 0x6e6a5e, 0.95))
  const sun = new THREE.DirectionalLight(DAYLIGHT.sun, 2.4)
  sun.castShadow = true
  sun.shadow.mapSize.set(2048, 2048)
  sun.shadow.bias = -0.0004
  sun.shadow.normalBias = 0.02
  const cam = sun.shadow.camera
  cam.left = -SHADOW_HALF_M
  cam.right = SHADOW_HALF_M
  cam.top = SHADOW_HALF_M
  cam.bottom = -SHADOW_HALF_M
  cam.near = 1
  cam.far = 220
  scene.add(sun)
  scene.add(sun.target)

  // Late-morning sun from the south-west: long enough shadows to read the car's shape.
  const offset = { x: -55, y: 90, z: 70 }
  const focus = (x: number, y: number): void => {
    sun.position.set(x + offset.x, offset.y, y + offset.z)
    sun.target.position.set(x, 0, y)
    sun.target.updateMatrixWorld()
  }
  focus(center.x, center.y)
  return { focus }
}

/** Marks every mesh under an object as casting and receiving shadows. */
export function shadowed(object: THREE.Object3D): THREE.Object3D {
  object.traverse((node: THREE.Object3D) => {
    const mesh = node as THREE.Mesh
    if (mesh.isMesh) {
      mesh.castShadow = true
      mesh.receiveShadow = true
    }
  })
  return object
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

const std = (colour: number, roughness = 0.8, metalness = 0) =>
  new THREE.MeshStandardMaterial({ color: colour, roughness, metalness })

/**
 * A plant worker, about 1.75 m: work trousers and boots, a hi-vis orange vest with reflective
 * bands, arms, a head and a white hard hat. Faces local +X.
 * @returns Worker group.
 */
export function buildWorker(): THREE.Group {
  const group = new THREE.Group()
  const trousers = std(0x2c3440, 0.9)
  const boots = std(0x1a1a1a, 0.9)
  const vest = std(0xff7a1a, 0.55)
  const band = new THREE.MeshStandardMaterial({ color: 0xe8edf2, roughness: 0.25, metalness: 0.4 })
  const shirt = std(0x3b4a5c, 0.85)
  const skin = std(0xc89a74, 0.7)
  const hat = std(0xf4f4f0, 0.35)
  for (const side of [-1, 1]) {
    part(group, new THREE.BoxGeometry(0.16, 0.82, 0.16), trousers, 0, 0.49, side * 0.11)
    part(group, new THREE.BoxGeometry(0.26, 0.1, 0.16), boots, 0.04, 0.05, side * 0.11)
    const arm = part(
      group,
      new THREE.CapsuleGeometry(0.055, 0.5, 4, 8),
      shirt,
      0,
      1.18,
      side * 0.27,
    )
    arm.rotation.x = side * 0.12
  }
  part(group, new THREE.BoxGeometry(0.26, 0.58, 0.42), vest, 0, 1.2, 0)
  for (const y of [1.05, 1.28]) part(group, new THREE.BoxGeometry(0.27, 0.05, 0.43), band, 0, y, 0)
  part(group, new THREE.CylinderGeometry(0.06, 0.06, 0.08, 10), skin, 0, 1.53, 0)
  part(group, new THREE.SphereGeometry(0.115, 16, 12), skin, 0, 1.64, 0)
  part(
    group,
    new THREE.SphereGeometry(0.135, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2),
    hat,
    0,
    1.69,
    0,
  )
  part(group, new THREE.CylinderGeometry(0.17, 0.17, 0.015, 18), hat, 0.02, 1.69, 0)
  return group
}

/**
 * A shipping carton, 1.2 m, with tape across the top and down the sides.
 * @returns Box group.
 */
export function buildCarton(): THREE.Group {
  const group = new THREE.Group()
  part(group, new THREE.BoxGeometry(1.2, 1, 1.2), std(0xa77b4f, 0.95), 0, 0.5, 0)
  const tape = std(0xc9a46e, 0.5)
  part(group, new THREE.BoxGeometry(1.22, 0.01, 0.12), tape, 0, 1.0, 0)
  for (const side of [-1, 1])
    part(group, new THREE.BoxGeometry(0.01, 0.3, 0.12), tape, side * 0.61, 0.86, 0)
  return group
}

/**
 * A tileable grain texture (asphalt, concrete): base colour with fine speckle and soft wear
 * patches. Browser only; null without a 2D canvas (jsdom), where materials keep flat colour.
 * @param base - Base colour, CSS hex.
 * @param grain - Speckle strength, 0..1.
 * @returns Repeating texture, or null.
 */
export function grainTexture(base: string, grain: number): THREE.CanvasTexture | null {
  if (typeof document === 'undefined') return null
  const canvas = document.createElement('canvas')
  canvas.width = 256
  canvas.height = 256
  let ctx: CanvasRenderingContext2D | null = null
  try {
    ctx = canvas.getContext('2d')
  } catch {
    ctx = null
  }
  if (!ctx) return null
  ctx.fillStyle = base
  ctx.fillRect(0, 0, 256, 256)
  // Deterministic noise, so frames are the same from run to run.
  let seed = 7
  const rand = () => {
    seed = (seed * 16807) % 2147483647
    return seed / 2147483647
  }
  for (let i = 0; i < 40; i++) {
    const shade = rand() < 0.5 ? 0 : 255
    ctx.fillStyle = `rgba(${shade},${shade},${shade},${0.025 + rand() * 0.035})`
    ctx.beginPath()
    ctx.arc(rand() * 256, rand() * 256, 12 + rand() * 40, 0, Math.PI * 2)
    ctx.fill()
  }
  for (let i = 0; i < 9000; i++) {
    const shade = rand() < 0.5 ? 0 : 255
    ctx.fillStyle = `rgba(${shade},${shade},${shade},${rand() * grain})`
    ctx.fillRect(rand() * 256, rand() * 256, 1 + rand(), 1 + rand())
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.wrapS = THREE.RepeatWrapping
  texture.wrapT = THREE.RepeatWrapping
  texture.colorSpace = THREE.SRGBColorSpace
  texture.anisotropy = 4
  return texture
}

/**
 * Gives the camera world's road strips and ground real surfaces: asphalt grain on the strips (one
 * texture repeat every 6 m, scaled to each strip) and concrete on the apron.
 * @param roads - The `roads` group (roadBuilder): its widest strips are the asphalt.
 * @param ground - The ground plane.
 * @param groundSize - Ground plane size, metres (width, depth).
 */
export function surfaceTextures(
  roads: THREE.Object3D | undefined,
  ground: THREE.Mesh,
  groundSize: [number, number],
): void {
  const asphalt = grainTexture('#45484d', 0.22)
  const concrete = grainTexture('#a3a49f', 0.16)
  if (concrete) {
    concrete.repeat.set(groundSize[0] / 8, groundSize[1] / 8)
    ground.material = new THREE.MeshStandardMaterial({ map: concrete, roughness: 0.95 })
  }
  if (!asphalt || !roads) return
  roads.traverse((node: THREE.Object3D) => {
    const mesh = node as THREE.Mesh
    const params = mesh.isMesh ? mesh.geometry.parameters : null
    // Asphalt strips are the 5 m wide ones; edge lines and dashes are centimetres wide.
    if (!params || params.depth < 4) return
    const map = asphalt.clone()
    map.needsUpdate = true
    map.repeat.set(params.width / 6, params.depth / 6)
    mesh.material = new THREE.MeshStandardMaterial({ map, roughness: 0.92 })
  })
}
