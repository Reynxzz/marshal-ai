// Case library (T13 extension): the ~20 planned scenarios a batch is assembled from.
// Pure data + seeded sampling, shared by the batch planner, the manifest and the offline AI.

import type { WrongColourName } from '#/lib/colors'
import type { BuildColour, Expected, ExpectedLabel } from '../records'
import type { Rng } from '../rng'

export type CaseDef = {
  id: string
  label: string
  expected: Expected
  /** Build-sheet colour override (wrong_colour cases declare an explicit sheet colour). */
  declaredColour?: BuildColour
  /** Spawn colour that contradicts the build sheet (wrong_colour only). */
  spawnColour?: WrongColourName
}

export const CASE_LIBRARY: CaseDef[] = [
  // wrong colour — declared sheet colour vs off-sheet paint
  {
    id: 'wc-blue-red',
    label: 'wrong colour (blue -> red)',
    expected: { kind: 'wrong_colour' },
    declaredColour: 'blue',
    spawnColour: 'red',
  },
  {
    id: 'wc-white-red',
    label: 'wrong colour (white -> red)',
    expected: { kind: 'wrong_colour' },
    declaredColour: 'white',
    spawnColour: 'red',
  },
  {
    id: 'wc-grey-red',
    label: 'wrong colour (grey -> red)',
    expected: { kind: 'wrong_colour' },
    declaredColour: 'grey',
    spawnColour: 'red',
  },
  {
    id: 'wc-black-red',
    label: 'wrong colour (black -> red)',
    expected: { kind: 'wrong_colour' },
    declaredColour: 'black',
    spawnColour: 'red',
  },
  {
    id: 'wc-blue-green',
    label: 'wrong colour (blue -> green)',
    expected: { kind: 'wrong_colour' },
    declaredColour: 'blue',
    spawnColour: 'green',
  },
  {
    id: 'wc-white-orange',
    label: 'wrong colour (white -> orange)',
    expected: { kind: 'wrong_colour' },
    declaredColour: 'white',
    spawnColour: 'orange',
  },
  {
    id: 'wc-grey-green',
    label: 'wrong colour (grey -> green)',
    expected: { kind: 'wrong_colour' },
    declaredColour: 'grey',
    spawnColour: 'green',
  },
  {
    id: 'wc-black-orange',
    label: 'wrong colour (black -> orange)',
    expected: { kind: 'wrong_colour' },
    declaredColour: 'black',
    spawnColour: 'orange',
  },

  // dead lamps — caught on the R-12 track routine
  {
    id: 'dl-headlights',
    label: 'dead lamp (headlights)',
    expected: { kind: 'dead_lamp', faultStep: 'headlights' },
  },
  {
    id: 'dl-brake-lights',
    label: 'dead lamp (brake lights)',
    expected: { kind: 'dead_lamp', faultStep: 'brake_lights' },
  },
  {
    id: 'dl-both',
    label: 'dead lamps (both)',
    expected: { kind: 'dead_lamp', faultStep: 'lamps' },
  },

  // incidents — blocked lane after the gate
  {
    id: 'inc-box-worker',
    label: 'incident (box + worker)',
    expected: { kind: 'incident', blockers: ['box', 'walker'] },
  },
  { id: 'inc-box', label: 'incident (box)', expected: { kind: 'incident', blockers: ['box'] } },
  {
    id: 'inc-worker',
    label: 'incident (worker)',
    expected: { kind: 'incident', blockers: ['walker'] },
  },
  {
    id: 'inc-vehicle',
    label: 'incident (forklift)',
    expected: { kind: 'incident', blockers: ['vehicle'] },
  },
  {
    id: 'inc-workzone',
    label: 'incident (forklift work zone)',
    expected: { kind: 'incident', blockers: ['vehicle', 'box', 'walker'] },
  },

  // quiet cars — colour variety for the rest of the batch
  { id: 'clean-blue', label: 'clean (blue)', expected: { kind: 'clean' }, declaredColour: 'blue' },
  {
    id: 'clean-white',
    label: 'clean (white)',
    expected: { kind: 'clean' },
    declaredColour: 'white',
  },
  { id: 'clean-grey', label: 'clean (grey)', expected: { kind: 'clean' }, declaredColour: 'grey' },
  {
    id: 'clean-black',
    label: 'clean (black)',
    expected: { kind: 'clean' },
    declaredColour: 'black',
  },
]

export const CASE_IDS: string[] = CASE_LIBRARY.map((c) => c.id)

export function caseById(id: string): CaseDef | undefined {
  return CASE_LIBRARY.find((c) => c.id === id)
}

/** Menu for the batch planner: ids, labels and kinds (the AI only ever picks from this). */
export function caseMenu(): Array<{ id: string; label: string; kind: ExpectedLabel }> {
  return CASE_LIBRARY.map((c) => ({ id: c.id, label: c.label, kind: c.expected.kind }))
}

/** Unique seeded sample of the library; at most one incident (the lane holds one blockage). */
export function sampleCaseIds(rng: Rng, cars: number): string[] {
  const take = Math.min(cars, CASE_LIBRARY.length)
  const pool = rng.sample(CASE_LIBRARY, CASE_LIBRARY.length)
  const out: string[] = []
  let incidents = 0
  for (const c of pool) {
    if (out.length === take) break
    if (c.expected.kind === 'incident') {
      if (incidents >= 1) continue
      incidents++
    }
    out.push(c.id)
  }
  return out
}

/** Validate an explicit selection (e.g. from the model); null when it violates the rules. */
export function validateCaseIds(ids: unknown, cars: number): string[] | null {
  if (!Array.isArray(ids) || ids.length !== cars) return null
  const unique = [...new Set(ids)]
  const stringIds = unique.filter((id): id is string => typeof id === 'string')
  if (stringIds.length !== cars) return null
  if (!stringIds.every((id) => CASE_IDS.includes(id))) return null
  const incidents = stringIds.filter((id) => caseById(id)?.expected.kind === 'incident').length
  if (incidents > 1) return null
  return stringIds
}

export function resolveCaseIds(ids: string[] | undefined, rng: Rng, cars: number): string[] {
  return (ids ? validateCaseIds(ids, cars) : null) ?? sampleCaseIds(rng, cars)
}
