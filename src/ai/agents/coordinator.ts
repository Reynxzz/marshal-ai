// Plant Ops Coordinator: the root ADK agent with every specialist registered as a sub-agent.

import { LlmAgent } from '@google/adk'
import { createSpecialists } from './specialists'

export function createCoordinator(model: string): LlmAgent {
  const specialists = createSpecialists(model)
  return new LlmAgent({
    name: 'plant_ops_coordinator',
    description: 'Plant Ops Coordinator that routes work to the specialist agents.',
    model,
    instruction:
      'You coordinate the plant operations specialists (visual, functional, assistance, yard, ' +
      'report, scenario, adversarial, batch). Delegate each request to the matching sub-agent ' +
      'and return its JSON output unchanged.',
    subAgents: Object.values(specialists),
  })
}
