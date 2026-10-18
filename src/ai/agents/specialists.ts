// ADK specialist agents: one per AI task group, plus the runner used by the server.
// Imports the beta @google/adk SDK; only ever loaded server-side (see src/ai/server.ts).

import { InMemoryRunner, LlmAgent, stringifyContent } from '@google/adk'
import type { AiTaskName } from '#/ai/tasks'

export type AgentGroup =
  | 'visual'
  | 'functional'
  | 'assistance'
  | 'yard'
  | 'report'
  | 'scenario'
  | 'adversarial'
  | 'batch'

export const TASK_GROUP: Record<AiTaskName, AgentGroup> = {
  'visual.screen': 'visual',
  'visual.defects': 'visual',
  'visual.match': 'visual',
  'track.decide': 'functional',
  'assist.assess': 'assistance',
  'yard.exception': 'yard',
  'report.write': 'report',
  'scenario.spec': 'scenario',
  'adversarial.propose': 'adversarial',
  'adversarial.report': 'adversarial',
  'batch.plan': 'batch',
  'batch.feedback': 'batch',
}

const GROUP_PROMPTS: Record<AgentGroup, { description: string; instruction: string }> = {
  visual: {
    description: 'Inspects gate photos for defects and build-sheet mismatches.',
    instruction:
      'You inspect car photos at the inspection gate. Reply with ONLY JSON.\n' +
      'visual.screen -> {"hasDefect":boolean,"confidence":number}\n' +
      'visual.defects -> {"findings":[{"kind":string,"zone"?:string,"view"?:string}]}\n' +
      'visual.match -> {"mismatches":[{"field":string,"declared":string,"seen":string}],"confidence":number}',
  },
  functional: {
    description: 'Judges whether a routine step worked on the test track.',
    instruction:
      'You judge whether a track routine step worked from a photo. Reply with ONLY JSON: ' +
      '{"works":boolean,"confidence":number}',
  },
  assistance: {
    description: 'Ranks recovery options for a plant incident.',
    instruction:
      'You rank safe recovery options for a plant incident. Reply with ONLY JSON: ' +
      '{"ranked":string[],"recommended":string,"risk":string,"peopleOrEquipment":boolean}',
  },
  yard: {
    description: 'Assigns free yard slots for an exception.',
    instruction:
      'You assign free yard slots when a truck is late. Reply with ONLY JSON: ' +
      '{"slots":string[],"reason":string}',
  },
  report: {
    description: 'Writes a short rework report.',
    instruction:
      'You write a concise rework report. Reply with ONLY JSON: ' +
      '{"summary":string,"steps":string[]}',
  },
  scenario: {
    description: 'Turns a request into a test scenario spec.',
    instruction:
      'You turn a request into a test scenario spec. Reply with ONLY JSON: ' +
      '{"id":string,"request":string,"zone":string,' +
      '"actors":[{"kind":"walker"|"box"|"vehicle","at":string,"offsetM":number}],' +
      '"timeOfDay":"day"|"night","metrics":string[],' +
      '"expect":{"clearsWithinS"?:number,"noContact":boolean}}',
  },
  adversarial: {
    description: 'Proposes adversarial variants and explains failures.',
    instruction:
      'You stress-test the plant with adversarial variants. Reply with ONLY JSON.\n' +
      'adversarial.propose -> {"variations":unknown[],"reasoning":string}\n' +
      'adversarial.report -> {"explanation":string,"likelyCause":string,"suggestedFix":string}',
  },
  batch: {
    description: 'Plans a batch from the case library and summarises the production digest.',
    instruction:
      'You plan and summarise car-production batches. Reply with ONLY JSON.\n' +
      'batch.plan -> {"caseIds":string[],"rationale":string} — pick exactly `cars` unique ids ' +
      'from the menu, never more than one incident kind, and prefer a realistic mix of faults ' +
      'and quiet cars.\n' +
      'batch.feedback -> {"batchSize":number,"summary":string,' +
      '"perFlow":[{"flow":number,"name":string,"observation":string,"issues":string[],"recommendation":string}],' +
      '"topIssues":string[],"recommendations":string[],"risk":"low"|"medium"|"high"}',
  },
}

export function createSpecialists(model: string): Record<AgentGroup, LlmAgent> {
  const make = (name: AgentGroup) => {
    const { description, instruction } = GROUP_PROMPTS[name]
    return new LlmAgent({ name, description, instruction, model })
  }
  return {
    visual: make('visual'),
    functional: make('functional'),
    assistance: make('assistance'),
    yard: make('yard'),
    report: make('report'),
    scenario: make('scenario'),
    adversarial: make('adversarial'),
    batch: make('batch'),
  }
}

/** Runs the specialist that owns `task` and returns its parsed JSON proposal. */
export async function runTaskAgent(
  task: AiTaskName,
  payload: unknown,
  model: string,
): Promise<unknown> {
  const agent = createSpecialists(model)[TASK_GROUP[task]]
  const runner = new InMemoryRunner({ agent, appName: 'marshal' })
  const prompt = `Task: ${task}\nInput JSON:\n${JSON.stringify(payload)}\nReply with ONLY the JSON object for this task.`
  let text = ''
  let output: unknown
  for await (const event of runner.runEphemeral({
    userId: 'marshal',
    newMessage: { role: 'user', parts: [{ text: prompt }] },
  })) {
    if (event.output !== undefined) output = event.output
    const chunk = stringifyContent(event)
    if (chunk.trim()) text = chunk
  }
  return output !== undefined ? output : parseJson(text)
}

export function parseJson(text: string): unknown {
  const fenced = text.replace(/```(?:json)?/gi, '').trim()
  const start = fenced.indexOf('{')
  const end = fenced.lastIndexOf('}')
  if (start < 0 || end < start) throw new Error('model did not return JSON')
  return JSON.parse(fenced.slice(start, end + 1))
}
