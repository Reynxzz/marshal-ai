// Flow 1 — Yard traffic: release spacing, slot allocation, zone closure and shipping guard.

import type { Truck, Verdict } from '../records'
import { YARD_SLOTS } from '../world'
import type { FlowHost } from './host'

export class YardFlow {
  readonly closedZones = new Set<string>()
  private readonly slotsUsed = new Set<string>()
  private truckCursor = 0

  constructor(
    private readonly host: FlowHost,
    readonly trucks: Truck[],
  ) {}

  private slotDistance(slot: string): number {
    const p = this.host.world.point(slot)
    const b = this.host.world.point('loading_bay')
    return Math.hypot(p.x - b.x, p.y - b.y)
  }

  freeSlots(): string[] {
    return YARD_SLOTS.filter((s) => !this.slotsUsed.has(s))
  }

  /** Nearest free slot to the loading bay. */
  assignSlot(): string | null {
    const free = this.freeSlots().sort(
      (a, b) => this.slotDistance(a) - this.slotDistance(b) || (a < b ? -1 : 1),
    )
    const slot = free[0]
    if (!slot) return null
    this.slotsUsed.add(slot)
    return slot
  }

  closeZone(zone: string): void {
    this.closedZones.add(zone)
  }

  reopenZone(zone: string): void {
    this.closedZones.delete(zone)
  }

  /** Shortest path avoiding closed zones; falls back to null when no alternative exists. */
  routeAvoiding(from: string, to: string): { path: string[]; usedAlt: boolean } | null {
    try {
      const direct = this.host.world.route(from, to)
      const usesClosed = direct.some((n) => this.closedZones.has(this.host.world.point(n).zone))
      if (!usesClosed) return { path: direct, usedAlt: false }
      const alt = this.host.world.route(from, to, { avoid: [...this.closedZones] })
      return { path: alt, usedAlt: true }
    } catch {
      return null
    }
  }

  /** Round-robin truck assignment. */
  assignTruck(vin: string): Truck {
    const truck = this.trucks[this.truckCursor % this.trucks.length] as Truck
    this.truckCursor++
    truck.cars.push(vin)
    return truck
  }

  /** Shipping guard: only cars whose report verdict is PASS may ship. */
  ship(truck: Truck, verdicts: Map<string, Verdict>): { shipped: string[]; rejected: string[] } {
    const shipped: string[] = []
    const rejected: string[] = []
    for (const vin of truck.cars) {
      if (verdicts.get(vin) === 'PASS') shipped.push(vin)
      else rejected.push(vin)
    }
    return { shipped, rejected }
  }
}
