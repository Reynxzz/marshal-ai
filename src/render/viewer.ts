// Viewer renderer (T16): draws the shared yard scene on a visible canvas at requestAnimationFrame
// speed. Read-only consumer of simulation snapshots.

import * as THREE from 'three'
import type { CarSnapshot } from '#/sim/vehicle'
import type { World } from '#/sim/world'
import { interpolatePose } from './interpolate'
import { type ActorSnapshot, createYardScene } from './scene'

export type ViewMode = 'overview' | 'follow' | 'gate' | 'track'

export type Viewer = {
  render(prev: CarSnapshot[], next: CarSnapshot[], actors: ActorSnapshot[], alpha: number): void
  setCamera(mode: ViewMode, vin?: string): void
  resize(width: number, height: number): void
  dispose(): void
}

export function createViewer(canvas: HTMLCanvasElement, world: World): Viewer {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true })
  renderer.shadowMap.enabled = true
  const dpr = typeof devicePixelRatio === 'number' ? Math.min(2, devicePixelRatio) : 1
  renderer.setPixelRatio(dpr)

  const yard = createYardScene(world)
  const camera = new THREE.PerspectiveCamera(55, 1, 0.5, 2000)

  const points = Object.values(world.points)
  const center = {
    x: (Math.min(...points.map((p) => p.x)) + Math.max(...points.map((p) => p.x))) / 2,
    y: (Math.min(...points.map((p) => p.y)) + Math.max(...points.map((p) => p.y))) / 2,
  }

  let mode: ViewMode = 'overview'
  let followVin: string | undefined

  const setCamera = (next: ViewMode, vin?: string): void => {
    mode = next
    followVin = vin
  }

  const aimAt = (x: number, z: number, height: number, distance: number, pitch: number): void => {
    camera.position.set(x, height, z + distance)
    camera.lookAt(x, pitch, z)
  }

  const updateCamera = (cars: CarSnapshot[]): void => {
    if (mode === 'overview') {
      camera.position.set(center.x, 150, center.y + 110)
      camera.lookAt(center.x, 0, center.y)
      return
    }
    if (mode === 'follow') {
      const car = cars.find((c) => c.vin === followVin) ?? cars[0]
      if (car) {
        camera.position.set(car.x - Math.cos(car.yaw) * 14, 9, car.y - Math.sin(car.yaw) * 14)
        camera.lookAt(car.x, 1, car.y)
      }
      return
    }
    const anchor = world.point(mode === 'gate' ? 'inspection_gate' : 'test_track')
    aimAt(anchor.x, anchor.y, mode === 'gate' ? 6 : 8, 18, 1)
  }

  const render = (
    prev: CarSnapshot[],
    next: CarSnapshot[],
    actors: ActorSnapshot[],
    alpha: number,
  ): void => {
    const previous = new Map(prev.map((c) => [c.vin, c]))
    const snapped = next.map((car) => {
      const before = previous.get(car.vin)
      if (!before) return car
      const pose = interpolatePose(before, car, alpha)
      return { ...car, x: pose.x, y: pose.y, yaw: pose.yaw }
    })
    yard.syncCars(snapped)
    yard.syncActors(actors)
    updateCamera(snapped)
    renderer.render(yard.scene, camera)
  }

  const resize = (width: number, height: number): void => {
    if (width <= 0 || height <= 0) return
    camera.aspect = width / height
    camera.updateProjectionMatrix()
    renderer.setSize(width, height, false)
  }

  const dispose = (): void => {
    yard.dispose()
    renderer.dispose()
  }

  return { render, setCamera, resize, dispose }
}
