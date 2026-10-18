// One frame of the dashboard 3D view: car poses read from the engine, props, dock trucks and the
// focus. Pure (no three.js) so the focus / highlight rules are testable without WebGL.

import { COLOUR_RGB, type MarshalState, type Truck } from '#/lib/marshal/state'
import type { DrivingSystem, Pose } from '#/lib/marshal/types'
import type { SimFocus } from './focus'

/** Prop kinds the engine spawns. */
export type SimPropKind = 'box' | 'worker' | 'cone' | 'barrel' | 'truck'

/** A spawned prop as listed by the engine. */
export type SimPropLike = { id: string; kind: SimPropKind; x: number; y: number; yaw: number }

/** What the view needs from the engine; `listProps` is optional (ScriptedDrivingSystem has it). */
export type SimEngine = Pick<DrivingSystem, 'getPositionXY'> & {
  listProps?: () => SimPropLike[]
}

/** Car highlight: amber = a decision (needs you / held), blue = selected. */
export type CarHighlight = 'needs' | 'selected' | null

export type SimCar = Pose & {
  vin: string
  /** '#rrggbb'. */
  colour: string
  highlight: CarHighlight
}

export type SimProp = SimPropLike & {
  /** Within {@link BLOCKING_RADIUS_M} of the focused car. */
  blocking: boolean
}

export type SimSnapshot = {
  cars: SimCar[]
  props: SimProp[]
  /** Dock trucks still at the plant ('left' trucks are dropped). */
  trucks: Truck[]
  focus: SimFocus | null
  /** Pose of the focused car (null for the overview or when the engine lost it). */
  focusPose: Pose | null
}

/** Props this close to the focused car are drawn as blocking it. */
export const BLOCKING_RADIUS_M = 15

/**
 * '#rrggbb' for a build colour name.
 * @param name - Colour name, e.g. 'blue'.
 * @returns Hex colour (grey for unknown names).
 */
export function colourHexFor(name: string): string {
  const rgb = COLOUR_RGB[name.trim().toLowerCase()] ?? [0.5, 0.5, 0.5]
  return `#${rgb
    .map((v) =>
      Math.round(Math.max(0, Math.min(1, v)) * 255)
        .toString(16)
        .padStart(2, '0'),
    )
    .join('')}`
}

function lost(pose: Pose): boolean {
  // Engines return a zero pose for unknown cars; no named point sits at the origin.
  return pose.x === 0 && pose.y === 0 && pose.yaw === 0
}

/**
 * Reads one frame for the 3D view.
 * @param engine - Running engine (null before the shift starts).
 * @param state - Orchestrator snapshot.
 * @param focus - Focus target from `chooseFocus`.
 * @returns Cars, props, trucks and focus pose.
 */
export function readSimSnapshot(
  engine: SimEngine | null,
  state: MarshalState,
  focus: SimFocus | null,
): SimSnapshot {
  const cars: SimCar[] = []
  if (engine) {
    for (const vin of state.order) {
      const car = state.cars[vin]
      if (!car || car.stage === 'shipped') continue
      const pose = engine.getPositionXY(vin)
      if (!pose || lost(pose)) continue
      const highlight: CarHighlight =
        focus?.vin === vin
          ? focus.reason === 'selected'
            ? 'selected'
            : 'needs'
          : car.stage === 'waiting'
            ? 'needs'
            : null
      cars.push({
        vin,
        x: pose.x,
        y: pose.y,
        yaw: pose.yaw,
        colour: colourHexFor(car.colour),
        highlight,
      })
    }
  }
  const focusCar = focus ? cars.find((car) => car.vin === focus.vin) : undefined
  const focusPose = focusCar ? { x: focusCar.x, y: focusCar.y, yaw: focusCar.yaw } : null
  const props: SimProp[] = (engine?.listProps?.() ?? []).map((prop) => ({
    ...prop,
    blocking:
      focusPose !== null &&
      Math.hypot(prop.x - focusPose.x, prop.y - focusPose.y) <= BLOCKING_RADIUS_M,
  }))
  return {
    cars,
    props,
    trucks: state.trucks.filter((truck) => truck.state !== 'left'),
    focus,
    focusPose,
  }
}
