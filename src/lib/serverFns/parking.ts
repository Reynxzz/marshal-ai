// planParkingStep (TEAM_PLAN M7.5): A* always plans up to 3 candidate paths server-side; Gemini
// only picks a candidate label, or says "parked" / "ask_person". The control sequence always
// comes from A*, never from the model. Falls back to the cheapest path (degraded: true).

import { createServerFn } from '@tanstack/react-start'
import { type CandidatePath, isParked, planCandidatePaths } from '#/lib/marshal/parking/astar'
import { type ParkingInput, parkingInputSchema, parkingModelSchema } from '#/lib/marshal/schemas'
import type { ControlStep, ParkingStep } from '#/lib/marshal/types'
import { enforceParking, offlineParking } from './logic'

export { offlineParking }

function gearChanges(steps: readonly ControlStep[]): number {
  let changes = 0
  for (let i = 1; i < steps.length; i++) if (steps[i].gear !== steps[i - 1].gear) changes++
  return changes
}

function parkingPrompt(input: ParkingInput, candidates: readonly CandidatePath[]): string {
  const summary = candidates.map((candidate) => ({
    label: candidate.label,
    cost_m: candidate.cost,
    gear_changes: gearChanges(candidate.steps),
    steps: candidate.steps,
    end: candidate.end,
  }))
  return (
    `You supervise an autonomous car parking into slot ${input.slot.id}.\n` +
    `Car pose: ${JSON.stringify(input.pose)}. Slot pose: ${JSON.stringify(input.slot)}.\n` +
    `Obstacles (map frame, yaw deg): ${JSON.stringify(input.obstacles)}.\n` +
    `A* candidate paths: ${JSON.stringify(summary)}.\n` +
    'Pick the safest candidate (prefer fewer gear changes and more clearance to moving ' +
    'obstacles), or "ask_person" if none is safe. Reply with ONLY JSON: ' +
    '{"decision":"path_a"|"path_b"|"path_c"|"ask_person","confidence":number between 0 and 1}'
  )
}

async function online(data: ParkingInput, candidates: CandidatePath[]): Promise<ParkingStep> {
  const { generateJson } = await import('./gemini')
  const model = await generateJson(parkingPrompt(data, candidates), [], parkingModelSchema)
  return enforceParking(data, candidates, model)
}

/**
 * Server function: plans the next parking step.
 * @param data - {@link ParkingInput}: vin, current pose, obstacle boxes and target slot.
 * @returns {@link ParkingStep}; `degraded: true` when answered offline.
 */
export const planParkingStep = createServerFn({ method: 'POST', strict: false })
  .validator((input: unknown): ParkingInput => parkingInputSchema.parse(input))
  .handler(async ({ data }): Promise<ParkingStep> => {
    try {
      const candidates = planCandidatePaths(data.pose, data.obstacles, data.slot)
      const offline = () => offlineParking(data, candidates)
      // Nothing for Gemini to choose: deterministic outcome, not a degraded answer.
      if (isParked(data.pose, data.slot) || candidates.length === 0) {
        const { isGeminiConfigured } = await import('./gemini')
        return { ...offline(), degraded: !isGeminiConfigured() }
      }
      const { runGemini } = await import('./gemini')
      return await runGemini('planParkingStep', offline, () => online(data, candidates))
    } catch {
      return offlineParking(data)
    }
  })
