// Dusk atmosphere (TEAM_PLAN M1.7): a low sun (15 degrees by default) and a dim ambient fill.

import * as THREE from 'three'

/** Dusk lighting options. */
export type DuskOptions = {
  /** Sun elevation above the horizon, degrees (TEAM_PLAN: 15). */
  elevationDeg?: number
  /** Sun compass direction in the CARLA frame, degrees. */
  azimuthDeg?: number
  /** Sun intensity. */
  intensity?: number
  /** Ambient fill intensity. */
  ambientIntensity?: number
  /** Sun distance from the plant centre, metres. */
  distanceM?: number
}

/**
 * Sun position for an elevation and azimuth.
 * @param center - Plant centre in the CARLA frame.
 * @param options - Elevation/azimuth/distance overrides.
 * @returns three.js position [x, y, z] relative to the plant.
 */
export function sunPosition(
  center: { x: number; y: number },
  options: DuskOptions = {},
): [number, number, number] {
  const elevation = ((options.elevationDeg ?? 15) * Math.PI) / 180
  const azimuth = ((options.azimuthDeg ?? 120) * Math.PI) / 180
  const distance = options.distanceM ?? 200
  const horizontal = Math.cos(elevation) * distance
  return [
    center.x + horizontal * Math.cos(azimuth),
    Math.sin(elevation) * distance,
    center.y + horizontal * Math.sin(azimuth),
  ]
}

/**
 * Adds the dusk sun (shadow-less here; the engine enables shadows) and an ambient fill.
 * @param scene - Target scene.
 * @param center - Plant centre in the CARLA frame.
 * @param options - Elevation/azimuth/intensity overrides.
 * @returns The directional sun light.
 */
export function addDuskLighting(
  scene: THREE.Scene,
  center: { x: number; y: number },
  options: DuskOptions = {},
): THREE.DirectionalLight {
  const sun = new THREE.DirectionalLight(0xffe6c0, options.intensity ?? 1.4)
  sun.position.set(...sunPosition(center, options))
  scene.add(sun)
  scene.add(new THREE.AmbientLight(0x2a3340, options.ambientIntensity ?? 0.55))
  return sun
}
