// chooseRouteHazard (TEAM_PLAN M9.1): at report_point, Gemini chooses Lane C or the finishing
// corner from a hazard description. The route must be one of the offered options. Falls back
// offline (degraded: true).

import { createServerFn } from '@tanstack/react-start'
import { type RouteInput, routeInputSchema, routeModelSchema } from '#/lib/marshal/schemas'
import type { RouteChoice } from '#/lib/marshal/types'
import { enforceRoute, offlineRoute } from './logic'

export { offlineRoute }

function routePrompt(input: RouteInput): string {
  return (
    'An autonomous plant car at report_point must drive to finishing, either via the ' +
    'finishing corner (shorter) or via Lane C (detour).\n' +
    `Hazard report: ${input.hazardDescription.trim() || 'none'}.\n` +
    `Offered options: ${input.options.join(', ')}.\n` +
    'Choose exactly one offered option. Reply with ONLY JSON: ' +
    '{"route":string,"confidence":number between 0 and 1,"reason":string}'
  )
}

async function online(data: RouteInput): Promise<RouteChoice> {
  const { generateJson } = await import('./gemini')
  const model = await generateJson(routePrompt(data), [], routeModelSchema)
  return enforceRoute(data, model)
}

/**
 * Server function: chooses the route at report_point.
 * @param data - {@link RouteInput}: hazard description and offered route options.
 * @returns {@link RouteChoice}; `degraded: true` when answered offline.
 */
export const chooseRouteHazard = createServerFn({ method: 'POST', strict: false })
  .validator((input: unknown): RouteInput => routeInputSchema.parse(input))
  .handler(async ({ data }): Promise<RouteChoice> => {
    try {
      const { runGemini } = await import('./gemini')
      return await runGemini(
        'chooseRouteHazard',
        () => offlineRoute(data),
        () => online(data),
      )
    } catch {
      return offlineRoute(data)
    }
  })
