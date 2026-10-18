// Heads-up display over the dashboard 3D view, in the Tesla screen's style: the followed car's
// drive cluster (gear, speed, steering, acceleration) and what its sensors see (lidar returns,
// the car ahead, obstacles, route). Every number comes from the engine (ThreeJsEngine.telemetryOf
// and .scan); nothing here is invented. Plain DOM, updated from the render loop.

import type { CarTelemetry } from '#/lib/simulator/engine'
import { bearingDeg, LIDAR, type LidarScan } from '#/lib/simulator/sensors'

/** Plant-wide numbers when no car is followed. */
export type PlantStats = { cars: number; moving: number; avgKmh: number; odometerKm: number }

/** Simulation figures shown under the cluster. */
export type SimStats = { stepHz: number; timeScale: number; fps: number }

export type HudInput = {
  telemetry: CarTelemetry | null
  scan: LidarScan | null
  pose: { x: number; y: number; yaw: number } | null
  plant: PlantStats
  sim: SimStats
}

export type Hud = { update(input: HudInput): void; dispose(): void }

const GEARS = ['P', 'R', 'N', 'D'] as const

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className: string,
  parent?: HTMLElement,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  node.className = className
  if (text !== undefined) node.textContent = text
  parent?.appendChild(node)
  return node
}

const fixed = (value: number, digits = 1): string => {
  const out = value.toFixed(digits)
  return out === `-${(0).toFixed(digits)}` ? (0).toFixed(digits) : out
}
const signed = (value: number, digits = 1): string =>
  `${value >= 0.05 ? '+' : ''}${fixed(value, digits)}`
const clock = (seconds: number): string => {
  const s = Math.max(0, Math.round(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
const side = (bearing: number): string =>
  Math.abs(bearing) < 8 ? 'ahead' : `${Math.abs(Math.round(bearing))}° ${bearing > 0 ? 'L' : 'R'}`

/**
 * Builds the HUD inside the 3D view's stage.
 * @param stage - Positioned container over the canvas.
 * @returns HUD with update and dispose.
 */
export function createHud(stage: HTMLElement): Hud {
  const root = el('div', 'md-hud', stage)
  root.setAttribute('aria-hidden', 'true')

  // Drive cluster.
  const drive = el('div', 'md-hud-card md-hud-drive', root)
  const gears = el('div', 'md-hud-gears', drive)
  const gearEls = GEARS.map((gear) => el('span', 'md-hud-gear', gears, gear))
  const speedRow = el('div', 'md-hud-speed', drive)
  const speed = el('span', 'md-hud-speed-value', speedRow, '0')
  el('span', 'md-hud-speed-unit', speedRow, 'km/h')
  const vin = el('div', 'md-hud-vin', drive)
  const grid = el('div', 'md-hud-grid', drive)
  const cell = (label: string): HTMLElement => {
    const box = el('div', 'md-hud-cell', grid)
    el('span', 'md-hud-label', box, label)
    return el('span', 'md-hud-value', box, '–')
  }
  const steer = cell('Steer')
  const accel = cell('Accel')
  const lateral = cell('Lateral')
  const yawRate = cell('Yaw rate')
  const heading = cell('Heading')
  const trip = cell('Trip')
  const mode = el('div', 'md-hud-mode', drive)

  // Perception.
  const sense = el('div', 'md-hud-card md-hud-sense', root)
  el(
    'div',
    'md-hud-title',
    sense,
    `LiDAR · ${LIDAR.channels.length} ch × ${LIDAR.beams} · ${LIDAR.range_m} m · ${LIDAR.hz} Hz`,
  )
  const row = (label: string): HTMLElement => {
    const line = el('div', 'md-hud-row', sense)
    el('span', 'md-hud-label', line, label)
    return el('span', 'md-hud-value', line, '–')
  }
  const returns = row('Returns')
  const nearest = row('Nearest')
  const lead = row('Lead car')
  const obstacle = row('Obstacle')
  const route = row('Route')
  const lamps = el('div', 'md-hud-lamps', sense)
  const lampEls = {
    headlights: el('span', 'md-hud-lamp', lamps, 'HEAD'),
    brake_lights: el('span', 'md-hud-lamp brake', lamps, 'BRAKE'),
    reverse: el('span', 'md-hud-lamp', lamps, 'REV'),
    indicator_left: el('span', 'md-hud-lamp amber', lamps, '◀'),
    indicator_right: el('span', 'md-hud-lamp amber', lamps, '▶'),
  }

  // Simulation line.
  const sim = el('div', 'md-hud-sim', root)

  return {
    update({ telemetry: t, scan, pose, plant, sim: s }) {
      sim.textContent = `Physics ${s.stepHz} Hz fixed step · kinematic bicycle · ×${s.timeScale} · ${Math.round(s.fps)} fps`
      root.classList.toggle('plant', t === null)
      if (!t) {
        speed.textContent = fixed(plant.avgKmh, 0)
        vin.textContent = `Plant average · ${plant.moving}/${plant.cars} cars moving`
        for (const g of gearEls) g.classList.remove('on')
        steer.textContent = '–'
        accel.textContent = '–'
        lateral.textContent = '–'
        yawRate.textContent = '–'
        heading.textContent = '–'
        trip.textContent = `${fixed(plant.odometerKm, 2)} km`
        mode.textContent = 'Select a car to ride along'
        sense.classList.add('idle')
        return
      }
      sense.classList.remove('idle')
      gearEls.forEach((g, i) => {
        g.classList.toggle('on', GEARS[i] === t.gear)
      })
      speed.textContent = fixed(t.speed_kmh, 0)
      vin.textContent = t.vin
      steer.textContent = `${signed(t.steer_deg)}°`
      accel.textContent = `${signed(t.accel_mps2, 2)} m/s²`
      lateral.textContent = `${fixed(Math.abs(t.lateral_g), 2)} g`
      yawRate.textContent = `${signed(t.yaw_rate_dps)}°/s`
      heading.textContent = `${String(Math.round(t.heading_deg) % 360).padStart(3, '0')}°`
      trip.textContent = `${fixed(t.odometer_m, 0)} m`
      mode.textContent = t.mode
      mode.dataset.kind = /Braking|Stopped|Waiting/.test(t.mode) ? 'warn' : 'ok'

      if (scan) {
        const objects = scan.hits.filter((hit) => hit.kind !== 'ground').length
        returns.textContent = `${scan.hits.length} pts · ${objects} on objects`
      } else {
        returns.textContent = '–'
      }
      if (scan?.nearest && pose) {
        nearest.textContent = `${scan.nearest.kind} ${fixed(scan.nearest.d)} m · ${side(bearingDeg(pose, scan.nearest))}`
      } else {
        nearest.textContent = `clear to ${LIDAR.range_m} m`
      }
      lead.textContent = t.leader
        ? `${t.leader.vin} ${fixed(t.leader.gap_m)} m${t.leader.ttc_s !== null ? ` · TTC ${fixed(t.leader.ttc_s)} s` : ''}`
        : 'none'
      obstacle.textContent = t.obstacle
        ? `${t.obstacle.kind} ${fixed(t.obstacle.ahead_m)} m ahead`
        : 'lane clear'
      route.textContent = t.route
        ? `→ ${t.route.next} · ${fixed(t.route.remaining_m, 0)} m${t.route.eta_s !== null ? ` · ${clock(t.route.eta_s)}` : ''}`
        : 'no mission'
      for (const [name, node] of Object.entries(lampEls)) {
        node.classList.toggle('on', t.lamps[name as keyof typeof t.lamps])
      }
    },
    dispose() {
      root.remove()
    },
  }
}
