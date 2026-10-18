// CARLA <-> three.js conversions (TEAM_PLAN M1.2). CARLA is Z-up; three.js is Y-up:
// position.set(x, z, y) and rotation.y = -degToRad(yaw). Pure math, no three.js import.

/** A pose in the CARLA map frame: x/y metres, yaw degrees, optional height `z`. */
export type CarlaPose = { x: number; y: number; yaw: number; z?: number }

/** The subset of an Object3D a pose is applied to (keeps this module three-free). */
export type PoseTarget = {
  position: { set(x: number, y: number, z: number): void }
  rotation: { y: number }
}

/**
 * three.js position for a CARLA point.
 * @param x - CARLA x, metres.
 * @param y - CARLA y, metres.
 * @param z - CARLA z (height), metres.
 * @returns [x, z, y] in three.js axes.
 */
export function carlaToThree(x: number, y: number, z = 0): [number, number, number] {
  return [x, z, y]
}

/**
 * three.js rotation.y for a CARLA yaw.
 * @param yawDeg - CARLA yaw, degrees.
 * @returns Yaw in radians, negated for the right-handed three.js frame.
 */
export function carlaYawToThree(yawDeg: number): number {
  return (-yawDeg * Math.PI) / 180
}

/**
 * Applies a CARLA pose to a three.js object.
 * @param object - Target with `position` and `rotation`.
 * @param pose - CARLA pose to apply.
 */
export function applyCarlaPose(object: PoseTarget, pose: CarlaPose): void {
  const [x, y, z] = carlaToThree(pose.x, pose.y, pose.z ?? 0)
  object.position.set(x, y, z)
  object.rotation.y = carlaYawToThree(pose.yaw)
}
