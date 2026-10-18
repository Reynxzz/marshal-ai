import { describe, expect, it } from 'vitest'
import { emptyCounts } from '../batch/counts'
import { buildReport } from '../batch/report'
import { mergeVerdicts } from '../flows/report'
import { YardFlow } from '../flows/yard'
import type { BatchEvent } from '../records'
import { buildTrucks } from '../trucks'
import { makeHost } from './helpers'

describe('mergeVerdicts', () => {
  it('FAIL wins, then REVIEW, then PASS', () => {
    expect(mergeVerdicts('PASS', 'REVIEW', 'PASS')).toBe('REVIEW')
    expect(mergeVerdicts('PASS', 'FAIL', 'REVIEW')).toBe('FAIL')
    expect(mergeVerdicts('PASS', 'PASS')).toBe('PASS')
  })
})

describe('buildReport', () => {
  it('counts exceptions and strips media payloads', () => {
    const events: BatchEvent[] = [
      { batchId: 'B', seq: 1, at: 0, source: 'x', title: 'ok', detail: '', level: 'info' },
      {
        batchId: 'B',
        seq: 2,
        at: 1,
        source: 'x',
        title: 'bad',
        detail: '',
        level: 'bad',
        payload: { frame: { dataUrl: 'data:image/jpeg;base64,AAAA' } },
      },
    ]
    const report = buildReport({
      batchId: 'B',
      status: 'done',
      generatedAt: '2026-01-01T00:00:00.000Z',
      model: 'gemini-2.5-flash',
      createdCars: 2,
      requestedCars: 2,
      finishedCars: 2,
      durationS: 12.4,
      counts: emptyCounts(),
      labels: { total: 1, caught: 1, missed: 0, falseAlarms: 0, cars: [] },
      gemini: {
        batchSize: 2,
        summary: 's',
        perFlow: [],
        topIssues: [],
        recommendations: [],
        risk: 'low',
        degraded: true,
      },
      events,
    })
    expect(report.batchSize).toBe(2)
    expect(report.counts.exceptions).toBe(1)
    expect(JSON.stringify(report.log)).not.toContain('base64')
    expect(report.durationS).toBe(12)
  })
})

describe('YardFlow', () => {
  it('only ships REPORT-PASS cars', () => {
    const yard = new YardFlow(makeHost(), buildTrucks(1))
    const truck = yard.assignTruck('V1')
    yard.assignTruck('V2')
    const result = yard.ship(
      truck,
      new Map([
        ['V1', 'PASS'],
        ['V2', 'FAIL'],
      ]),
    )
    expect(result.shipped).toEqual(['V1'])
    expect(result.rejected).toEqual(['V2'])
  })

  it('assigns distinct nearest slots', () => {
    const yard = new YardFlow(makeHost(), buildTrucks(1))
    const a = yard.assignSlot()
    const b = yard.assignSlot()
    expect(a).toBeTruthy()
    expect(a).not.toBe(b)
  })

  it('routes around a closed zone', () => {
    const yard = new YardFlow(makeHost(), buildTrucks(1))
    yard.closeZone('finishing')
    const route = yard.routeAvoiding('report_point', 'finishing')
    expect(route?.usedAlt).toBe(true)
    expect(route?.path).toEqual(['report_point', 'lane_c', 'finishing'])
  })
})
