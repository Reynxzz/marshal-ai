import { describe, expect, it } from 'vitest'
import * as R from '../records'

const buildSheet = { model: 'M-1', colour: 'blue' as const, options: ['ac'] }

describe('record schemas round-trip', () => {
  it('parses one sample of each record', () => {
    const car: R.Car = {
      vin: 'VIN-B01-1',
      buildSheet,
      state: 'line_end',
      location: 'line_end',
      spawnColour: '#ff0000',
    }
    expect(R.carSchema.parse(car)).toEqual(car)

    const mission: R.Mission = {
      id: 'M1',
      vin: car.vin,
      waypoints: ['line_end', 'inspection_gate'],
      status: 'queued',
      routine: 'R-12',
      routineSteps: ['headlights_on'],
    }
    expect(R.missionSchema.parse(mission)).toEqual(mission)

    const event: R.Event = {
      seq: 1,
      at: 0,
      flow: 1,
      source: 'engine',
      title: 'spawn',
      detail: 'car spawned',
      level: 'info',
      payload: { vin: car.vin },
    }
    expect(R.eventSchema.parse(event)).toEqual(event)

    const batchEvent: R.BatchEvent = { ...event, batchId: 'B01' }
    expect(R.batchEventSchema.parse(batchEvent)).toEqual(batchEvent)

    const inspection: R.InspectionResult = {
      vin: car.vin,
      kind: 'visual',
      verdict: 'FAIL',
      findings: [{ kind: 'colour mismatch', view: 'front' }],
      mismatches: [{ field: 'colour', declared: 'blue', seen: 'red' }],
      confidence: 0.95,
    }
    expect(R.inspectionResultSchema.parse(inspection)).toEqual(inspection)

    const proposal: R.Proposal = {
      id: 'P1',
      kind: 'rework',
      summary: 'repaint',
      payload: { bay: 'rework.paint' },
      status: 'pending',
    }
    expect(R.proposalSchema.parse(proposal)).toEqual(proposal)

    const assistance: R.AssistanceCase = {
      caseId: 'C1',
      vin: car.vin,
      situation: 'box on lane',
      options: ['wait_for_removal', 'reroute', 'manual_override'],
      recommended: 'reroute',
      risk: 'low',
      peopleOrEquipment: false,
      status: 'open',
      openedAt: 10,
    }
    expect(R.assistanceCaseSchema.parse(assistance)).toEqual(assistance)

    const carReport: R.CarReport = {
      vin: car.vin,
      verdict: 'PASS',
      summary: 'ok',
      findings: [],
      failedChecks: [],
      shipped: true,
    }
    expect(R.carReportSchema.parse(carReport)).toEqual(carReport)

    const batchCar: R.BatchCar = {
      vin: car.vin,
      expected: { kind: 'dead_lamp', faultStep: 'headlights' },
      state: 'line_end',
      stage: 1,
      location: 'line_end',
    }
    expect(R.batchCarSchema.parse(batchCar)).toEqual(batchCar)

    const manifest: R.Manifest = {
      batchId: 'B01',
      seed: 7,
      cars: [batchCar],
      trucks: [{ truckId: 'T1', etaMin: 20, cars: [] }],
    }
    expect(R.manifestSchema.parse(manifest)).toEqual(manifest)

    const counts: R.FlowCounts = {
      flow1: { released: 0, waiting: 0, inYard: 0, shipped: 0 },
      flow2: { cases: 0, open: 0, resolved: 0, escalated: 0 },
      flow3: { pass: 0, review: 0, fail: 0 },
      flow4: { pass: 0, fail: 0, disagreements: 0 },
      flow5: { pass: 0, rework: 0, shipped: 0 },
      flow6: { testRequests: 0, generated: 0, status: 'not_triggered' },
      flow7: { runs: 0, failures: 0, status: 'not_triggered' },
    }
    expect(R.flowCountsSchema.parse(counts)).toEqual(counts)

    const scenario: R.Scenario = {
      id: 'S1',
      request: 'box blocks lane',
      zone: 'lane',
      actors: [{ kind: 'box', at: 'lane_c', offsetM: 5 }],
      timeOfDay: 'day',
      metrics: ['contact'],
      expect: { noContact: true },
    }
    expect(R.scenarioSchema.parse(scenario)).toEqual(scenario)

    const testRun: R.TestRun = {
      scenarioId: 'S1',
      variant: { offsetM: 5 },
      metrics: { time_to_clear_s: 12 },
      passed: false,
      failedCriteria: ['noContact'],
    }
    expect(R.testRunSchema.parse(testRun)).toEqual(testRun)

    const batch: R.Batch = {
      batchId: 'B01',
      status: 'queued',
      requestedCars: 3,
      createdCars: 3,
      finishedCars: 0,
      progress: 0,
      seed: 7,
      engine: { timeScale: 4, simTimeS: 0 },
      incident: { enabled: true, vin: car.vin },
      manifest,
      counts,
    }
    expect(R.batchSchema.parse(batch)).toEqual(batch)
  })

  it('rejects invalid enum values', () => {
    expect(() => R.carStateSchema.parse('flying')).toThrow()
    expect(() => R.verdictSchema.parse('MAYBE')).toThrow()
  })
})
