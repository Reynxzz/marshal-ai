// Pure hybrid A* parking planner (TEAM_PLAN M7.5). Plans in the slot frame: origin at the slot
// centre, +x along the slot heading, +y to the left. States are (x, y, heading) discretised on a
// 0.25m grid with 5 deg heading bins; motion primitives follow the kinematic bicycle model of
// M7.1 (wheel +/-1 = +/-30 deg, wheelbase 2.85m) in both gears. Obstacles are oriented boxes
// inflated by the side clearance. Deterministic: same input, same output.

import type { ControlStep, ObstacleBox, ParkingSlot, Pose } from '../types'

/** Car dimensions and parking tolerances (TEAM_PLAN "Car Dimensions"). */
export const CAR_GEOMETRY = {
  length_m: 4.9,
  width_m: 1.9,
  wheelbase_m: 2.85,
  maxSteerDeg: 30,
  sideClearance_m: 0.3,
  headingToleranceDeg: 8,
  /** Fore/aft tolerance for "parked" (not in TEAM_PLAN; half a primitive step of slack). */
  longitudinalTolerance_m: 0.5,
} as const

/** One candidate path from the planner. */
export type CandidatePath = {
  label: 'A' | 'B' | 'C'
  steps: ControlStep[]
  /** Path length in metres with reverse / gear-change / steering penalties. */
  cost: number
  /** Predicted final pose in the map frame. */
  end: Pose
}

type Local = { x: number; y: number; th: number }

type Box = { cx: number; cy: number; ux: number; uy: number; hl: number; hw: number; r: number }

type Gear = 'D' | 'R'

const DEG = Math.PI / 180
const TWO_PI = Math.PI * 2
const CELL_M = 0.25
const HEADING_BINS = 72
const STEP_M = 0.5
const SAMPLES_PER_STEP = 2
const SPEED_MPS = 1
const WHEELS = [-1, -0.5, 0, 0.5, 1] as const
const GEARS: readonly Gear[] = ['D', 'R']
const REVERSE_FACTOR = 1.3
const GEAR_SWITCH_COST = 1.5
const STEER_CHANGE_COST = 0.1
const REUSE_PENALTY = 0.6
const MAX_EXPANSIONS = 6000
const SEARCH_MARGIN_M = 8
const HEURISTIC_WEIGHT = 1.5
const MAX_LABELS = ['A', 'B', 'C'] as const

const CAR_HL = CAR_GEOMETRY.length_m / 2
const CAR_HW = CAR_GEOMETRY.width_m / 2
const CAR_R = Math.hypot(CAR_HL, CAR_HW)
const MAX_STEER = CAR_GEOMETRY.maxSteerDeg * DEG
const MIN_TURN_RADIUS = CAR_GEOMETRY.wheelbase_m / Math.tan(MAX_STEER)
const HEADING_TOL = CAR_GEOMETRY.headingToleranceDeg * DEG

function wrapAngle(angle: number): number {
  let a = angle % TWO_PI
  if (a > Math.PI) a -= TWO_PI
  if (a <= -Math.PI) a += TWO_PI
  return a
}

/**
 * Converts a map-frame pose into the slot frame.
 * @param pose - Pose in the map frame (yaw in degrees).
 * @param slot - Target slot.
 * @returns Slot-frame pose (heading in radians).
 */
export function toSlotFrame(pose: Pose, slot: ParkingSlot): Local {
  const yaw = slot.yaw * DEG
  const c = Math.cos(yaw)
  const s = Math.sin(yaw)
  const dx = pose.x - slot.x
  const dy = pose.y - slot.y
  return { x: dx * c + dy * s, y: -dx * s + dy * c, th: wrapAngle(pose.yaw * DEG - yaw) }
}

/**
 * Converts a slot-frame pose back into the map frame.
 * @param local - Slot-frame pose (heading in radians).
 * @param slot - Target slot.
 * @returns Map-frame pose (yaw in degrees).
 */
export function fromSlotFrame(local: Local, slot: ParkingSlot): Pose {
  const yaw = slot.yaw * DEG
  const c = Math.cos(yaw)
  const s = Math.sin(yaw)
  return {
    x: slot.x + local.x * c - local.y * s,
    y: slot.y + local.x * s + local.y * c,
    yaw: wrapAngle(local.th + yaw) / DEG,
  }
}

function inGoal(x: number, y: number, th: number): boolean {
  return (
    Math.abs(y) <= CAR_GEOMETRY.sideClearance_m &&
    Math.abs(x) <= CAR_GEOMETRY.longitudinalTolerance_m &&
    Math.abs(wrapAngle(th)) <= HEADING_TOL
  )
}

/**
 * True when the car sits in the slot: within 0.3m laterally, 0.5m fore/aft and 8 deg of the
 * slot heading.
 * @param pose - Car pose in the map frame.
 * @param slot - Target slot.
 * @returns Whether the car counts as parked.
 */
export function isParked(pose: Pose, slot: ParkingSlot): boolean {
  const local = toSlotFrame(pose, slot)
  return inGoal(local.x, local.y, local.th)
}

function toBox(obstacle: ObstacleBox, slot: ParkingSlot): Box {
  const local = toSlotFrame(obstacle, slot)
  const hl = obstacle.length / 2 + CAR_GEOMETRY.sideClearance_m
  const hw = obstacle.width / 2 + CAR_GEOMETRY.sideClearance_m
  return {
    cx: local.x,
    cy: local.y,
    ux: Math.cos(local.th),
    uy: Math.sin(local.th),
    hl,
    hw,
    r: Math.hypot(hl, hw),
  }
}

/** Half-width of a box (half extents hl/hw, unit length axis ux/uy) projected onto axis a. */
function extent(hl: number, hw: number, ux: number, uy: number, ax: number, ay: number): number {
  return hl * Math.abs(ux * ax + uy * ay) + hw * Math.abs(-uy * ax + ux * ay)
}

/** Separating-axis test between the car footprint at (x, y, th) and every inflated box. */
function collides(x: number, y: number, th: number, boxes: readonly Box[]): boolean {
  const ux = Math.cos(th)
  const uy = Math.sin(th)
  for (const b of boxes) {
    const dx = b.cx - x
    const dy = b.cy - y
    const reach = CAR_R + b.r
    if (dx * dx + dy * dy > reach * reach) continue
    const axes: [number, number][] = [
      [ux, uy],
      [-uy, ux],
      [b.ux, b.uy],
      [-b.uy, b.ux],
    ]
    let separated = false
    for (const [ax, ay] of axes) {
      const distance = Math.abs(dx * ax + dy * ay)
      const carExtent = extent(CAR_HL, CAR_HW, ux, uy, ax, ay)
      const boxExtent = extent(b.hl, b.hw, b.ux, b.uy, ax, ay)
      if (distance > carExtent + boxExtent) {
        separated = true
        break
      }
    }
    if (!separated) return true
  }
  return false
}

/** Pose after driving `s` metres (negative = reverse) with front-wheel angle `delta`. */
function advance(x: number, y: number, th: number, s: number, delta: number): Local {
  const k = Math.tan(delta) / CAR_GEOMETRY.wheelbase_m
  if (Math.abs(k) < 1e-9) return { x: x + s * Math.cos(th), y: y + s * Math.sin(th), th }
  const nth = th + s * k
  return {
    x: x + (Math.sin(nth) - Math.sin(th)) / k,
    y: y - (Math.cos(nth) - Math.cos(th)) / k,
    th: nth,
  }
}

function heuristic(x: number, y: number, th: number): number {
  return HEURISTIC_WEIGHT * Math.max(Math.hypot(x, y), MIN_TURN_RADIUS * Math.abs(wrapAngle(th)))
}

/** Minimal binary min-heap of node indices keyed by f. */
class MinHeap {
  private readonly items: number[] = []
  private readonly keys: number[] = []

  get size(): number {
    return this.items.length
  }

  push(item: number, key: number): void {
    const { items, keys } = this
    let i = items.length
    items.push(item)
    keys.push(key)
    while (i > 0) {
      const parent = (i - 1) >> 1
      if (keys[parent] <= key) break
      items[i] = items[parent]
      keys[i] = keys[parent]
      i = parent
    }
    items[i] = item
    keys[i] = key
  }

  pop(): number {
    const { items, keys } = this
    const top = items[0]
    const lastItem = items.pop() as number
    const lastKey = keys.pop() as number
    const n = items.length
    if (n > 0) {
      let i = 0
      let left = 1
      while (left < n) {
        const right = left + 1
        const child = right < n && keys[right] < keys[left] ? right : left
        if (keys[child] >= lastKey) break
        items[i] = items[child]
        keys[i] = keys[child]
        i = child
        left = 2 * i + 1
      }
      items[i] = lastItem
      keys[i] = lastKey
    }
    return top
  }
}

type Bounds = { minX: number; minY: number; maxX: number; maxY: number; w: number }

type SearchResult = { steps: ControlStep[]; cost: number; end: Local; cells: number[] }

function search(
  start: Local,
  boxes: readonly Box[],
  bounds: Bounds,
  penalised: ReadonlySet<number>,
): SearchResult | null {
  const xs: number[] = [start.x]
  const ys: number[] = [start.y]
  const ths: number[] = [start.th]
  const gs: number[] = [0]
  const costs: number[] = [0]
  const parents: number[] = [-1]
  const gears: (Gear | null)[] = [null]
  const wheels: number[] = [0]
  const best = new Map<number, number>()
  const closed = new Set<number>()
  const heap = new MinHeap()

  const cellOf = (x: number, y: number) =>
    Math.floor((x - bounds.minX) / CELL_M) + Math.floor((y - bounds.minY) / CELL_M) * bounds.w
  const keyOf = (x: number, y: number, th: number) => {
    const bin = Math.floor(((wrapAngle(th) + Math.PI) / TWO_PI) * HEADING_BINS) % HEADING_BINS
    return cellOf(x, y) * HEADING_BINS + bin
  }

  heap.push(0, heuristic(start.x, start.y, start.th))
  let expansions = 0

  while (heap.size > 0 && expansions < MAX_EXPANSIONS) {
    const node = heap.pop()
    const key = keyOf(xs[node], ys[node], ths[node])
    if (closed.has(key)) continue
    closed.add(key)
    expansions++

    for (const gear of GEARS) {
      const dir = gear === 'D' ? 1 : -1
      for (const wheel of WHEELS) {
        const delta = wheel * MAX_STEER
        let next: Local = { x: xs[node], y: ys[node], th: ths[node] }
        let blocked = false
        for (let i = 1; i <= SAMPLES_PER_STEP; i++) {
          const s = (dir * STEP_M * i) / SAMPLES_PER_STEP
          next = advance(xs[node], ys[node], ths[node], s, delta)
          const outside =
            next.x < bounds.minX ||
            next.x > bounds.maxX ||
            next.y < bounds.minY ||
            next.y > bounds.maxY
          if (outside || collides(next.x, next.y, next.th, boxes)) {
            blocked = true
            break
          }
        }
        if (blocked) continue
        const nextKey = keyOf(next.x, next.y, next.th)
        if (closed.has(nextKey)) continue

        const parentGear = gears[node]
        let stepCost = STEP_M * (gear === 'R' ? REVERSE_FACTOR : 1)
        if (parentGear !== null && parentGear !== gear) stepCost += GEAR_SWITCH_COST
        if (parentGear !== null) stepCost += STEER_CHANGE_COST * Math.abs(wheel - wheels[node])
        const cost = costs[node] + stepCost
        const reuse = penalised.has(cellOf(next.x, next.y)) ? REUSE_PENALTY : 0
        const g = gs[node] + stepCost + reuse
        const known = best.get(nextKey)
        if (known !== undefined && known <= g) continue
        best.set(nextKey, g)

        const index = xs.length
        xs.push(next.x)
        ys.push(next.y)
        ths.push(next.th)
        gs.push(g)
        costs.push(cost)
        parents.push(node)
        gears.push(gear)
        wheels.push(wheel)

        if (inGoal(next.x, next.y, next.th)) {
          return reconstruct(index, { xs, ys, ths, costs, parents, gears, wheels }, cellOf)
        }
        heap.push(index, g + heuristic(next.x, next.y, next.th))
      }
    }
  }
  return null
}

type Nodes = {
  xs: number[]
  ys: number[]
  ths: number[]
  costs: number[]
  parents: number[]
  gears: (Gear | null)[]
  wheels: number[]
}

function reconstruct(
  goal: number,
  nodes: Nodes,
  cellOf: (x: number, y: number) => number,
): SearchResult {
  const primitives: { gear: Gear; wheel: number }[] = []
  const cells: number[] = []
  for (let i = goal; i > 0; i = nodes.parents[i]) {
    primitives.push({ gear: nodes.gears[i] as Gear, wheel: nodes.wheels[i] })
    cells.push(cellOf(nodes.xs[i], nodes.ys[i]))
  }
  primitives.reverse()

  const steps: ControlStep[] = []
  const stepDuration = STEP_M / SPEED_MPS
  for (const primitive of primitives) {
    const last = steps.at(-1)
    if (last && last.gear === primitive.gear && last.wheel === primitive.wheel) {
      last.duration_s += stepDuration
    } else {
      steps.push({
        speed_mps: SPEED_MPS,
        wheel: primitive.wheel,
        duration_s: stepDuration,
        gear: primitive.gear,
      })
    }
  }
  return {
    steps,
    cost: nodes.costs[goal],
    end: { x: nodes.xs[goal], y: nodes.ys[goal], th: nodes.ths[goal] },
    cells,
  }
}

function signature(steps: readonly ControlStep[]): string {
  return steps.map((s) => `${s.gear}${s.wheel}:${s.duration_s}`).join('|')
}

/**
 * Plans up to `max` distinct candidate paths (labelled A, B, C in order of cost) that take the
 * car from `pose` into `slot` without touching any inflated obstacle. Later candidates are pushed
 * away from cells used by earlier ones. Returns [] when the car is already parked, the slot
 * itself is blocked, or no path is found within the search budget.
 * @param pose - Car pose in the map frame.
 * @param obstacles - Obstacle boxes in the map frame (the car itself must not be included).
 * @param slot - Target slot.
 * @param max - Maximum number of candidates (clamped to 1..3).
 * @returns Candidate paths sorted by cost, cheapest first.
 */
export function planCandidatePaths(
  pose: Pose,
  obstacles: readonly ObstacleBox[],
  slot: ParkingSlot,
  max = 3,
): CandidatePath[] {
  const limit = Math.max(1, Math.min(MAX_LABELS.length, Math.floor(max)))
  const start = toSlotFrame(pose, slot)
  if (inGoal(start.x, start.y, start.th)) return []
  const boxes = obstacles.map((obstacle) => toBox(obstacle, slot))
  if (collides(0, 0, 0, boxes)) return []

  const minX = Math.min(start.x, 0) - SEARCH_MARGIN_M
  const minY = Math.min(start.y, 0) - SEARCH_MARGIN_M
  const maxX = Math.max(start.x, 0) + SEARCH_MARGIN_M
  const maxY = Math.max(start.y, 0) + SEARCH_MARGIN_M
  const bounds: Bounds = { minX, minY, maxX, maxY, w: Math.ceil((maxX - minX) / CELL_M) + 1 }

  const penalised = new Set<number>()
  const seen = new Set<string>()
  const found: SearchResult[] = []
  for (let run = 0; run < limit; run++) {
    const result = search(start, boxes, bounds, penalised)
    if (!result) break
    const sig = signature(result.steps)
    if (seen.has(sig)) break
    seen.add(sig)
    found.push(result)
    for (const cell of result.cells) penalised.add(cell)
  }

  found.sort((a, b) => a.cost - b.cost)
  return found.map((result, index) => ({
    label: MAX_LABELS[index],
    steps: result.steps,
    cost: Math.round(result.cost * 1000) / 1000,
    end: fromSlotFrame(result.end, slot),
  }))
}
