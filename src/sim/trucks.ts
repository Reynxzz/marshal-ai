// Truck schedule for yard traffic (flow 1). ETA is staggered so a late truck can trigger
// a replan proposal.

import type { Truck } from './records'

export function buildTrucks(cars: number): Truck[] {
  const count = cars >= 2 ? 2 : 1
  return Array.from({ length: count }, (_, i) => ({
    truckId: `T${i + 1}`,
    etaMin: 20 + 20 * i,
    cars: [],
  }))
}

/** Push a truck's ETA out; returns the updated truck (mutates in place too). */
export function delayTruck(trucks: Truck[], truckId: string, byMin: number): Truck | undefined {
  const truck = trucks.find((t) => t.truckId === truckId)
  if (!truck) return undefined
  truck.etaMin += byMin
  return truck
}
