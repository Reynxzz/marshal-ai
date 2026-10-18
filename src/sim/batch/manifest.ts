// Deterministic batch manifest (T13): VINs, build sheets, colours, trucks and case labels.
// Same (batchId, cars, seed, caseIds) always yields the same manifest.

import { colourHex } from '#/lib/colors'
import type { BatchCar, BuildColour, Manifest } from '../records'
import { makeRng } from '../rng'
import { buildTrucks } from '../trucks'
import { type CaseDef, caseById, resolveCaseIds } from './cases'

const BUILD_COLOURS: BuildColour[] = ['blue', 'white', 'grey', 'black']

export function deriveSeed(batchId: string): number {
  let h = 2166136261
  for (let i = 0; i < batchId.length; i++) {
    h ^= batchId.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return Math.abs(h) % 2147483647
}

export function buildManifest(
  batchId: string,
  cars: number,
  seed?: number,
  caseIds?: string[],
): Manifest {
  const resolvedSeed = seed ?? deriveSeed(batchId)
  const rng = makeRng(resolvedSeed)
  const code = `B${batchId.replace(/\D/g, '').slice(-2).padStart(2, '0')}`
  const ids = resolveCaseIds(caseIds, rng, cars)

  const batchCars: BatchCar[] = ids.map((caseId, i) => {
    const def = caseById(caseId) as CaseDef
    const declared = def.declaredColour ?? rng.pick(BUILD_COLOURS)
    const spawn = def.spawnColour ?? declared
    return {
      vin: `VIN-${code}-${i + 1}`,
      expected: { ...def.expected, caseId: def.id },
      state: 'waiting_dispatch',
      stage: 1,
      location: 'line_end',
      buildSheet: { model: `M-${100 + i}`, colour: declared, options: [] },
      colour: colourHex(spawn),
    }
  })

  return {
    batchId,
    seed: resolvedSeed,
    cars: batchCars,
    trucks: buildTrucks(batchCars.length),
  }
}

export function incidentVin(manifest: Manifest): string | undefined {
  return manifest.cars.find((c) => c.expected.kind === 'incident')?.vin
}
