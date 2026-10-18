// On-board cameras (TEAM_PLAN M5.1): the assist chase clip plus the front/rear evidence buffers.
// Poses, buffer rolling and the sample cadence are pure and tested headlessly; the renderer and
// JPEG encoder are injectable. Frames are clean (no overlays).
// shortcut: frames come off the renderer canvas (same pattern as the gate camera) rather than a
// WebGLRenderTarget pixel read-back; switch when the buffers need raw pixels.

import * as THREE from 'three'
import { type CarlaPose, carlaToThree, carlaYawToThree } from '../coords'

/** Rolling buffer depth per camera, frames. */
export const CAR_BUFFER_FRAMES = 8
/** Evidence capture period: 8 frames cover the last 4 s (TEAM_PLAN M5.1). */
export const EVIDENCE_PERIOD_S = 0.5
/** Assist capture period: 8 frames cover the last 10 s (TEAM_PLAN M5.1). */
export const ASSIST_PERIOD_S = 10 / CAR_BUFFER_FRAMES
/** Render size and quality for the server-function payloads. */
export const CAR_FRAME_WIDTH = 1280
export const CAR_FRAME_HEIGHT = 720
export const CAR_JPEG_QUALITY = 0.9

/** The three on-board cameras. */
export type CarCameraView = 'assist' | 'evidence_front' | 'evidence_rear'

/** One on-board camera pose in the CARLA frame. */
export type CarCameraPose = {
  view: CarCameraView
  x: number
  y: number
  z: number
  /** Viewing direction, degrees. */
  yaw: number
  /** Negative looks down. */
  pitch: number
  fov: number
}

/** Minimal renderer surface the rig needs (tests inject a fake). */
export type CarCameraRenderer = {
  render(scene: THREE.Scene, camera: THREE.PerspectiveCamera): void
  domElement: HTMLCanvasElement
  dispose?: () => void
}

/** Options for {@link createCarCamera}. */
export type CarCameraOptions = {
  /** Scene to render (the plant with the cars). */
  scene: THREE.Scene
  /** Renderer (defaults to a hidden WebGL renderer, created on first capture). */
  renderer?: CarCameraRenderer
  /** Canvas -> base64 JPEG (defaults to `toDataURL`; tests inject a fake). */
  encode?: (canvas: HTMLCanvasElement) => string
}

/** A car to sample. */
export type CarCameraTarget = { vin: string; pose: CarlaPose }

/** The on-board camera rig. */
export type CarCameraRig = {
  /**
   * Advances the capture clocks and renders the due frames for every car.
   * @param cars - Cars and their current poses.
   * @param dt - Sim seconds since the last sample.
   */
  sample(cars: readonly CarCameraTarget[], dt: number): void
  /**
   * Evidence frames for a car, oldest first ([] when unknown).
   * @param vin - Car VIN.
   * @param camera - Which evidence camera.
   * @returns Up to {@link CAR_BUFFER_FRAMES} base64 JPEG frames.
   */
  evidence(vin: string, camera: 'front' | 'rear'): string[]
  /**
   * Assist chase frames for a car, oldest first ([] when unknown).
   * @param vin - Car VIN.
   * @returns Up to {@link CAR_BUFFER_FRAMES} base64 JPEG frames.
   */
  assist(vin: string): string[]
  /** Frees the default renderer (injected renderers stay with their owner). */
  dispose(): void
}

/** A fixed-length rolling buffer of encoded frames, oldest first. */
export class FrameBuffer {
  private readonly items: string[] = []

  /**
   * @param capacity - Maximum frames kept.
   */
  constructor(private readonly capacity: number = CAR_BUFFER_FRAMES) {}

  /**
   * Appends a frame, dropping the oldest when full.
   * @param frame - base64 JPEG.
   */
  push(frame: string): void {
    this.items.push(frame)
    if (this.items.length > this.capacity) this.items.shift()
  }

  /**
   * Buffered frames.
   * @returns Copy, oldest first.
   */
  frames(): string[] {
    return [...this.items]
  }
}

/**
 * The three on-board poses for a car (TEAM_PLAN M5.1): assist 6 m behind and 3 m up pitched
 * -12 degrees with 90 degrees FOV; evidence 7 m behind / 7.5 m ahead at 1.6 m pitched -5 with
 * 38 degrees FOV. The rear camera looks forward, the front camera looks back at the car.
 * @param car - Car pose (CARLA frame, yaw degrees).
 * @returns Poses in view order.
 */
export function carCameraPoses(car: CarlaPose): CarCameraPose[] {
  const yaw = (car.yaw * Math.PI) / 180
  const fx = Math.cos(yaw)
  const fy = Math.sin(yaw)
  const place = (forward: number) => ({ x: car.x + fx * forward, y: car.y + fy * forward })
  return [
    { view: 'assist', ...place(-6), z: 3, yaw: car.yaw, pitch: -12, fov: 90 },
    { view: 'evidence_rear', ...place(-7), z: 1.6, yaw: car.yaw, pitch: -5, fov: 38 },
    { view: 'evidence_front', ...place(7.5), z: 1.6, yaw: car.yaw + 180, pitch: -5, fov: 38 },
  ]
}

function buildCameras(): Record<CarCameraView, THREE.PerspectiveCamera> {
  const aspect = CAR_FRAME_WIDTH / CAR_FRAME_HEIGHT
  return {
    assist: new THREE.PerspectiveCamera(90, aspect, 0.1, 500),
    evidence_front: new THREE.PerspectiveCamera(38, aspect, 0.1, 500),
    evidence_rear: new THREE.PerspectiveCamera(38, aspect, 0.1, 500),
  }
}

function createRenderer(): CarCameraRenderer {
  const canvas = document.createElement('canvas')
  canvas.width = CAR_FRAME_WIDTH
  canvas.height = CAR_FRAME_HEIGHT
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true })
  renderer.setSize(CAR_FRAME_WIDTH, CAR_FRAME_HEIGHT, false)
  renderer.setPixelRatio(1)
  renderer.outputColorSpace = THREE.SRGBColorSpace
  return renderer
}

function defaultEncode(canvas: HTMLCanvasElement): string {
  const url = canvas.toDataURL('image/jpeg', CAR_JPEG_QUALITY)
  return url.slice(url.indexOf(',') + 1)
}

type CarBuffers = { front: FrameBuffer; rear: FrameBuffer; assist: FrameBuffer }

/**
 * Builds the on-board camera rig.
 * @param options - Scene, optional renderer and encoder.
 * @returns Rig with evidence/assist buffers.
 */
export function createCarCamera(options: CarCameraOptions): CarCameraRig {
  const cameras = buildCameras()
  const encode = options.encode ?? defaultEncode
  const buffers = new Map<string, CarBuffers>()
  let renderer = options.renderer ?? null
  let evidenceClock = 0
  let assistClock = 0

  const buffersOf = (vin: string): CarBuffers => {
    let entry = buffers.get(vin)
    if (!entry) {
      entry = { front: new FrameBuffer(), rear: new FrameBuffer(), assist: new FrameBuffer() }
      buffers.set(vin, entry)
    }
    return entry
  }

  const shoot = (pose: CarCameraPose): string => {
    if (!renderer) renderer = createRenderer()
    const camera = cameras[pose.view]
    const [x, y, z] = carlaToThree(pose.x, pose.y, pose.z)
    camera.position.set(x, y, z)
    camera.rotation.order = 'YXZ'
    camera.rotation.y = carlaYawToThree(pose.yaw)
    camera.rotation.x = (pose.pitch * Math.PI) / 180
    renderer.render(options.scene, camera)
    return encode(renderer.domElement)
  }

  const capture = (cars: readonly CarCameraTarget[], views: CarCameraView[]): void => {
    for (const car of cars) {
      const poses = carCameraPoses(car.pose)
      const entry = buffersOf(car.vin)
      for (const view of views) {
        const pose = poses.find((item) => item.view === view)
        if (!pose) continue
        if (view === 'assist') entry.assist.push(shoot(pose))
        else if (view === 'evidence_front') entry.front.push(shoot(pose))
        else entry.rear.push(shoot(pose))
      }
    }
  }

  return {
    sample(cars, dt) {
      if (!(dt > 0) || cars.length === 0) return
      evidenceClock += dt
      assistClock += dt
      while (evidenceClock + 1e-9 >= EVIDENCE_PERIOD_S) {
        evidenceClock -= EVIDENCE_PERIOD_S
        capture(cars, ['evidence_front', 'evidence_rear'])
      }
      while (assistClock + 1e-9 >= ASSIST_PERIOD_S) {
        assistClock -= ASSIST_PERIOD_S
        capture(cars, ['assist'])
      }
    },
    evidence(vin, camera) {
      const entry = buffers.get(vin)
      if (!entry) return []
      return camera === 'front' ? entry.front.frames() : entry.rear.frames()
    },
    assist(vin) {
      return buffers.get(vin)?.assist.frames() ?? []
    },
    dispose() {
      if (renderer && !options.renderer) renderer.dispose?.()
      renderer = null
      buffers.clear()
    },
  }
}
