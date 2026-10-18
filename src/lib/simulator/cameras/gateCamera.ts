// Gate inspection cameras (TEAM_PLAN M3.1/M3.2): four fixed cameras around `inspection_gate`
// render clean JPEGs for the gate flow. The poses and view filtering are pure and tested
// headlessly; the renderer and the JPEG encoder are injectable, so tests never need WebGL.
// shortcut: frames come off the renderer canvas (same pattern as src/render/capture.ts) rather
// than a WebGLRenderTarget + pixel read-back; switch when the M5.1 evidence buffer needs raw
// pixels.

import * as THREE from 'three'
import { type NamedPoint, POINTS } from '#/lib/marshal/points'
import type { GateFrames, GateView } from '#/lib/marshal/types'
import { carlaToThree, carlaYawToThree } from '../coords'

/** Render size, TEAM_PLAN M3.1. */
export const GATE_FRAME_WIDTH = 1280
export const GATE_FRAME_HEIGHT = 720
/** JPEG quality for the server-function payloads. */
export const GATE_JPEG_QUALITY = 0.85

/** The four gate views, in capture order. */
export const GATE_VIEWS: readonly GateView[] = ['front', 'rear', 'left', 'right']

/** One fixed gate camera pose in the CARLA frame. */
export type GateCameraPose = {
  view: GateView
  x: number
  y: number
  /** Height, metres. */
  z: number
  /** Viewing direction, degrees (0 looks along +x, like yaw 180 looking back). */
  yaw: number
  /** Negative looks down (TEAM_PLAN M3.1: -5). */
  pitch: number
  fov: number
}

/** Minimal renderer surface the rig needs (tests inject a fake). */
export type GateRenderer = {
  render(scene: THREE.Scene, camera: THREE.PerspectiveCamera): void
  domElement: HTMLCanvasElement
  /** Optional cleanup for renderers the rig created itself. */
  dispose?: () => void
}

/** Options for {@link createGateCamera}. */
export type GateCameraOptions = {
  /** Scene to render (the plant with the cars). */
  scene: THREE.Scene
  /** Gate point (defaults to `inspection_gate`). */
  gate?: NamedPoint
  /** Renderer (defaults to a hidden WebGL renderer, created on first capture). */
  renderer?: GateRenderer
  /** Canvas -> base64 JPEG (defaults to `toDataURL`; tests inject a fake). */
  encode?: (canvas: HTMLCanvasElement) => string
}

/** The gate capture port plus disposal. */
export type GateCamera = {
  /**
   * Renders the requested views and returns one base64 JPEG per view (unrequested views empty).
   * @param vin - Car being inspected (the scene already shows it; kept for the port contract).
   * @param views - Views to render.
   * @returns base64 JPEG per gate view.
   */
  capture(vin: string, views: GateView[]): Promise<GateFrames>
  /** Frees the default renderer (injected renderers stay with their owner). */
  dispose(): void
}

/**
 * The four fixed poses around the gate (TEAM_PLAN M3.1): front/rear 7 m down the lane at 1.5 m,
 * left/right 5 m to the sides at 1.2 m, all pitched -5 degrees with a 90 degree field of view.
 * @param gate - Gate point (defaults to `inspection_gate`).
 * @returns Poses in capture order.
 */
export function gateCameraPoses(gate: NamedPoint = POINTS.inspection_gate): GateCameraPose[] {
  const forwardX = Math.cos((gate.yaw * Math.PI) / 180)
  const forwardY = Math.sin((gate.yaw * Math.PI) / 180)
  // "Right" follows the TEAM_PLAN camera table: +y of the lane when the gate yaw is 0.
  const rightX = -forwardY
  const rightY = forwardX
  const place = (forward: number, right: number) => ({
    x: gate.x + forwardX * forward + rightX * right,
    y: gate.y + forwardY * forward + rightY * right,
  })
  return [
    { view: 'front', ...place(7, 0), z: 1.5, yaw: gate.yaw + 180, pitch: -5, fov: 90 },
    { view: 'rear', ...place(-7, 0), z: 1.5, yaw: gate.yaw, pitch: -5, fov: 90 },
    { view: 'left', ...place(0, -5), z: 1.2, yaw: gate.yaw + 90, pitch: -5, fov: 90 },
    { view: 'right', ...place(0, 5), z: 1.2, yaw: gate.yaw - 90, pitch: -5, fov: 90 },
  ]
}

function buildCameras(poses: readonly GateCameraPose[]): Record<GateView, THREE.PerspectiveCamera> {
  const cameras = {} as Record<GateView, THREE.PerspectiveCamera>
  for (const pose of poses) {
    const camera = new THREE.PerspectiveCamera(
      pose.fov,
      GATE_FRAME_WIDTH / GATE_FRAME_HEIGHT,
      0.1,
      500,
    )
    const [x, y, z] = carlaToThree(pose.x, pose.y, pose.z)
    camera.position.set(x, y, z)
    camera.rotation.order = 'YXZ'
    camera.rotation.y = carlaYawToThree(pose.yaw)
    camera.rotation.x = (pose.pitch * Math.PI) / 180
    cameras[pose.view] = camera
  }
  return cameras
}

function createRenderer(): GateRenderer {
  const canvas = document.createElement('canvas')
  canvas.width = GATE_FRAME_WIDTH
  canvas.height = GATE_FRAME_HEIGHT
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    preserveDrawingBuffer: true,
  })
  renderer.setSize(GATE_FRAME_WIDTH, GATE_FRAME_HEIGHT, false)
  renderer.setPixelRatio(1)
  renderer.outputColorSpace = THREE.SRGBColorSpace
  return renderer
}

function defaultEncode(canvas: HTMLCanvasElement): string {
  const url = canvas.toDataURL('image/jpeg', GATE_JPEG_QUALITY)
  return url.slice(url.indexOf(',') + 1)
}

/**
 * Builds the gate camera rig.
 * @param options - Scene, optional gate point, renderer and encoder.
 * @returns Camera rig implementing the gate capture port.
 */
export function createGateCamera(options: GateCameraOptions): GateCamera {
  const gate = options.gate ?? POINTS.inspection_gate
  const cameras = buildCameras(gateCameraPoses(gate))
  const encode = options.encode ?? defaultEncode
  let renderer = options.renderer ?? null

  return {
    async capture(_vin, views) {
      if (!renderer) renderer = createRenderer()
      const canvas = renderer.domElement
      const frames: GateFrames = { front: '', rear: '', left: '', right: '' }
      for (const view of views) {
        const camera = cameras[view]
        if (!camera) continue
        renderer.render(options.scene, camera)
        frames[view] = encode(canvas)
      }
      return frames
    },
    dispose() {
      if (renderer && !options.renderer) renderer.dispose?.()
      renderer = null
    },
  }
}
