import { describe, expect, it } from 'vitest'
import { caseMenu } from '#/sim/batch/cases'
import type { FlowCounts } from '#/sim/records'
import { offlineTask } from '../offline'
import { AI_TASK_NAMES, type AiFrame, type AiTaskName, aiTasks } from '../tasks'

const frame: AiFrame = {
  view: 'front',
  mime: 'image/jpeg',
  dataUrl: 'data:image/jpeg;base64,AAAA',
  width: 640,
  height: 400,
  truth: 'colour=red; headlights=off; brake_lights=off; workers=0',
}

const counts: FlowCounts = {
  flow1: { released: 0, waiting: 0, inYard: 0, shipped: 0 },
  flow2: { cases: 0, open: 0, resolved: 0, escalated: 0 },
  flow3: { pass: 0, review: 0, fail: 1 },
  flow4: { pass: 0, fail: 1, disagreements: 0 },
  flow5: { pass: 0, rework: 0, shipped: 0 },
  flow6: { testRequests: 0, generated: 0, status: 'not_triggered' },
  flow7: { runs: 0, failures: 0, status: 'not_triggered' },
}

const payloads: Record<AiTaskName, unknown> = {
  'visual.screen': { frame },
  'visual.defects': { frame },
  'visual.match': { frame, buildSheet: { model: 'M', colour: 'blue', options: [] } },
  'track.decide': { frame, step: 'headlights_on' },
  'assist.assess': {
    situation: 'box on lane',
    options: ['wait_for_removal', 'reroute', 'manual_override'],
    description: 'worker next to box',
  },
  'yard.exception': { exception: 'late truck', yard: { freeSlots: ['yard.Y1'] } },
  'report.write': { findings: ['colour mismatch'], bays: ['rework.paint'] },
  'scenario.spec': { request: 'box blocks the lane' },
  'adversarial.propose': { history: [], metrics: ['contact'] },
  'adversarial.report': {
    run: {
      scenarioId: 'S1',
      variant: {},
      metrics: {},
      passed: false,
      failedCriteria: ['noContact'],
    },
    metrics: {},
  },
  'batch.plan': { cars: 3, seed: 7, menu: caseMenu() },
  'batch.feedback': { digest: { batchId: 'B01', batchSize: 3, counts, exceptions: [] } },
}

describe('offline AI', () => {
  it('has a valid handler for every registered task', async () => {
    for (const name of AI_TASK_NAMES) {
      const input = aiTasks[name].input.parse(payloads[name])
      const output = await offlineTask(name, input as never)
      expect(() => aiTasks[name].output.parse(output)).not.toThrow()
    }
  })

  it('detects a colour mismatch from frame truth', async () => {
    const out = await offlineTask('visual.match', {
      frame,
      buildSheet: { model: 'M', colour: 'blue', options: [] },
    })
    expect(out.mismatches).toEqual([{ field: 'colour', declared: 'blue', seen: 'red' }])
  })

  it('reports a dead headlight as not working', async () => {
    const out = await offlineTask('track.decide', { frame, step: 'headlights_on' })
    expect(out.works).toBe(false)
  })

  it('plans a unique batch from the case library menu', async () => {
    const out = await offlineTask('batch.plan', { cars: 3, seed: 7, menu: caseMenu() })
    expect(out.caseIds).toHaveLength(3)
    expect(new Set(out.caseIds).size).toBe(3)
    const kinds = out.caseIds.map((id) => caseMenu().find((c) => c.id === id)?.kind)
    expect(kinds.filter((k) => k === 'incident').length).toBeLessThanOrEqual(1)
  })

  it('does not recommend wait_for_removal when nobody is present', async () => {
    const out = await offlineTask('assist.assess', {
      situation: 'box on lane',
      options: ['wait_for_removal', 'reroute', 'manual_override'],
      description: 'a lone box blocks the lane',
    })
    expect(out.recommended).not.toBe('wait_for_removal')
    expect(out.peopleOrEquipment).toBe(false)
  })
})
