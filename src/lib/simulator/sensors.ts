// Simulated sensors for the dashboard: a roof lidar that ray-casts the engine's actual geometry
// (other cars as 4.9 x 1.9 m boxes, props at their real footprint) and the derived numbers the HUD
// shows. Pure functions, CARLA frame (x, y metres; yaw radians), no three.js.

import { CAR_LENGTH_M, CAR_WIDTH_M } from './actors/car'

/** Roof lidar: beams per revolution, vertical layers (beam height above ground), range. */
export const LIDAR = {
  beams: 240,
  /** Sensor height above the ground, metres (roof mount). */
  mount_m: 1.9,
  /** Channel elevations, degrees: a 12-channel spinning unit, mostly looking down. */
  channels: [-15, -11, -8, -6, -4.5, -3.5, -2.5, -1.8, -1.2, -0.6, 0, 1] as const,
  range_m: 40,
  hz: 10,
} as const

/** Something a lidar beam can hit: an oriented box on the ground. */
export type ScanTarget = {
  id: string
  kind: string
  x: number
  y: number
  /** Radians, CARLA frame. */
  yaw: number
  length: number
  width: number
  height: number
}

/** One lidar return: point (z = height above ground), range from the sensor, what it hit. */
export type LidarHit = { x: number; y: number; z: number; d: number; kind: string; id: string }

/** A full revolution. */
export type LidarScan = {
  hits: LidarHit[]
  /** Rays fired (beams x layers). */
  rays: number
  /** Nearest return, or null when nothing is within range. */
  nearest: LidarHit | null
}

/** A car as a scan target. */
export function carTarget(id: string, x: number, y: number, yaw: number): ScanTarget {
  return { id, kind: 'car', x, y, yaw, length: CAR_LENGTH_M, width: CAR_WIDTH_M, height: 1.45 }
}

/** A prop as a scan target, at its footprint (actors/props PROP_FOOTPRINTS). */
export function propTarget(
  id: string,
  kind: string,
  x: number,
  y: number,
  yaw: number,
  size: { length: number; width: number; height: number },
): ScanTarget {
  return { id, kind, x, y, yaw, length: size.length, width: size.width, height: size.height }
}

/**
 * Distance along a ray to an oriented box (slab test in the box's frame), or null.
 * @param ox - Ray origin x.
 * @param oy - Ray origin y.
 * @param dx - Unit direction x.
 * @param dy - Unit direction y.
 * @param box - Target.
 * @returns Distance to the first hit in front of the origin, or null.
 */
export function rayBox(
  ox: number,
  oy: number,
  dx: number,
  dy: number,
  box: ScanTarget,
): number | null {
  const c = Math.cos(-box.yaw)
  const s = Math.sin(-box.yaw)
  const rx = ox - box.x
  const ry = oy - box.y
  // Origin and direction in the box frame.
  const px = rx * c - ry * s
  const py = rx * s + ry * c
  const qx = dx * c - dy * s
  const qy = dx * s + dy * c
  const hx = box.length / 2
  const hy = box.width / 2
  let near = Number.NEGATIVE_INFINITY
  let far = Number.POSITIVE_INFINITY
  for (const [p, q, h] of [
    [px, qx, hx],
    [py, qy, hy],
  ]) {
    if (Math.abs(q) < 1e-12) {
      if (p < -h || p > h) return null
      continue
    }
    let t1 = (-h - p) / q
    let t2 = (h - p) / q
    if (t1 > t2) [t1, t2] = [t2, t1]
    near = Math.max(near, t1)
    far = Math.min(far, t2)
    if (near > far) return null
  }
  if (far < 0) return null
  return near >= 0 ? near : null
}

/**
 * One lidar revolution from a car's roof. Each channel's ray leaves the mount at its elevation; it
 * returns from the first thing it meets: a target whose height spans the ray's height at that
 * distance, or the flat plant floor (which down-looking channels reach at mount / tan(-elevation),
 * the rings a real lidar draws on the ground).
 * @param origin - Sensor pose (the scanning car), CARLA frame, yaw radians.
 * @param targets - Everything else on the plant.
 * @param options - Beam count, channels, mount height and range (defaults: {@link LIDAR}).
 * @returns Returns, ray count and the nearest return off an object (ground returns excluded).
 */
export function lidarScan(
  origin: { x: number; y: number; yaw: number },
  targets: readonly ScanTarget[],
  options: {
    beams?: number
    channels?: readonly number[]
    mount_m?: number
    range_m?: number
  } = {},
): LidarScan {
  const beams = options.beams ?? LIDAR.beams
  const channels = options.channels ?? LIDAR.channels
  const mount = options.mount_m ?? LIDAR.mount_m
  const range = options.range_m ?? LIDAR.range_m
  const near = targets.filter(
    (t) => Math.hypot(t.x - origin.x, t.y - origin.y) <= range + Math.hypot(t.length, t.width),
  )
  const hits: LidarHit[] = []
  let nearest: LidarHit | null = null
  for (let i = 0; i < beams; i++) {
    const angle = origin.yaw + (i / beams) * Math.PI * 2
    const dx = Math.cos(angle)
    const dy = Math.sin(angle)
    // Horizontal distance to each target along this azimuth (shared by every channel).
    const along: { d: number; target: ScanTarget }[] = []
    for (const target of near) {
      const d = rayBox(origin.x, origin.y, dx, dy, target)
      if (d !== null && d <= range) along.push({ d, target })
    }
    along.sort((a, b) => a.d - b.d)
    for (const elevation of channels) {
      const slope = Math.tan((elevation * Math.PI) / 180)
      const ground = slope < 0 ? mount / -slope : Number.POSITIVE_INFINITY
      let hit: { d: number; z: number; kind: string; id: string } | null = null
      for (const { d, target } of along) {
        if (d >= ground) break
        const z = mount + slope * d
        if (z >= 0 && z <= target.height) {
          hit = { d, z, kind: target.kind, id: target.id }
          break
        }
      }
      if (!hit && ground <= range) hit = { d: ground, z: 0, kind: 'ground', id: 'ground' }
      if (!hit) continue
      const point: LidarHit = {
        x: origin.x + dx * hit.d,
        y: origin.y + dy * hit.d,
        z: hit.z,
        d: Math.hypot(hit.d, hit.z - mount),
        kind: hit.kind,
        id: hit.id,
      }
      hits.push(point)
      if (point.kind !== 'ground' && (nearest === null || point.d < nearest.d)) nearest = point
    }
  }
  return { hits, rays: beams * channels.length, nearest }
}

/**
 * Bearing of a point from a car, degrees: 0 dead ahead, positive to the left.
 * @param car - Car pose, yaw radians.
 * @param point - Point.
 * @returns Bearing in (-180, 180].
 */
export function bearingDeg(
  car: { x: number; y: number; yaw: number },
  point: { x: number; y: number },
): number {
  let a = Math.atan2(point.y - car.y, point.x - car.x) - car.yaw
  while (a <= -Math.PI) a += Math.PI * 2
  while (a > Math.PI) a -= Math.PI * 2
  return (a * 180) / Math.PI
}
