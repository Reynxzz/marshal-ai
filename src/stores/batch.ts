// Client batch store (zustand). Owns the BatchRunner, drives its fixed-step loop with
// requestAnimationFrame, and publishes snapshots to the dashboard. No polling once the
// batch is terminal.

import { create } from 'zustand'
import { createAiClient, onAiCall } from '#/ai/client'
import { truthCapture } from '#/ai/truth'
import { downloadJson, STORAGE_KEYS, safeGet, safeSet } from '#/lib/storage'
import { createCapture } from '#/render/capture'
import { BatchRunner, type BatchSnapshot } from '#/sim/batch/orchestrator'
import { planBatch } from '#/sim/batch/plan'
import type { BatchReport } from '#/sim/batch/report'
import { FIXED_DT } from '#/sim/engine'
import type { CapturePort } from '#/sim/ports'
import type { AssistanceCase, BatchEvent, BatchStatus, FlowCounts, Proposal } from '#/sim/records'
import { world } from '#/sim/world'

const ai = createAiClient()
let degradedFlag = false
onAiCall((meta) => {
  if (meta.degraded) degradedFlag = true
})

let runner: BatchRunner | null = null
let rafId = 0
let stepping = false
let planning = false

let batchCounter = safeGet<number>(STORAGE_KEYS.counter, 0)
function nextBatchId(): string {
  const n = Number.isFinite(batchCounter) ? batchCounter + 1 : 1
  batchCounter = n
  safeSet(STORAGE_KEYS.counter, n)
  return `BAT-${String(n).padStart(2, '0')}`
}

export type BatchStore = {
  status: BatchStatus
  progress: number
  finishedCars: number
  requestedCars: number
  counts: FlowCounts | null
  events: BatchEvent[]
  cases: AssistanceCase[]
  proposals: Proposal[]
  snapshot: BatchSnapshot | null
  report: BatchReport | null
  pastReport: BatchReport | null
  paused: boolean
  timeScale: number
  degraded: boolean
  start: (cars: number, seed?: number) => Promise<void>
  cancel: () => void
  decide: (caseId: string, option: string, by?: string) => void
  resolveProposal: (id: string, approve: boolean, by?: string) => void
  togglePause: () => void
  setTimeScale: (n: number) => void
  reset: () => void
}

const lastReport = safeGet<BatchReport | null>(STORAGE_KEYS.report, null)

export const useBatchStore = create<BatchStore>((set, get) => ({
  status: 'queued',
  progress: 0,
  finishedCars: 0,
  requestedCars: 0,
  counts: null,
  events: [],
  cases: [],
  proposals: [],
  snapshot: null,
  report: null,
  pastReport: lastReport,
  paused: false,
  timeScale: 4,
  degraded: false,

  async start(cars, seed) {
    if (planning) return
    if (runner && (runner.status === 'running' || runner.status === 'queued')) return
    planning = true
    try {
      degradedFlag = false
      let capture: CapturePort
      try {
        capture = createCapture(world, { mode: 'render' })
      } catch (error) {
        console.error('capture init failed, using truth frames', error)
        capture = truthCapture
      }
      const batchId = nextBatchId()
      const plan = await planBatch(ai, { batchId, cars }, () => degradedFlag)
      runner = new BatchRunner({
        batchId,
        cars,
        seed,
        timeScale: get().timeScale,
        ai,
        capture,
        world,
        model: 'gemini-2.5-flash',
        generatedAt: () => new Date().toISOString(),
        degraded: () => degradedFlag,
        caseIds: plan.caseIds,
        plan: { source: plan.source, rationale: plan.rationale },
      })
      runner.start()
      set({
        status: 'running',
        report: null,
        events: [],
        cases: [],
        proposals: [],
        progress: 0,
        paused: false,
      })
      schedule()
    } finally {
      planning = false
    }
  },

  cancel() {
    runner?.cancel()
    publish(set)
  },

  decide(caseId, option, by = 'supervisor') {
    runner?.decideCase(caseId, option, by)
    publish(set)
  },

  resolveProposal(id, approve, by = 'supervisor') {
    runner?.decideProposal(id, approve, by)
    publish(set)
  },

  togglePause() {
    set({ paused: !get().paused })
  },

  setTimeScale(n) {
    const scale = Math.max(1, Math.min(8, n))
    runner?.setTimeScale(scale)
    set({ timeScale: scale })
  },

  reset() {
    set({
      report: null,
      events: [],
      cases: [],
      proposals: [],
      progress: 0,
      status: 'queued',
      finishedCars: 0,
      snapshot: null,
    })
  },
}))

export function exportCurrentReport(): void {
  const report = useBatchStore.getState().report ?? useBatchStore.getState().pastReport
  if (report) downloadJson(`${report.batchId}-report.json`, report)
}

function schedule(): void {
  if (typeof requestAnimationFrame === 'undefined') return
  if (rafId) return
  rafId = requestAnimationFrame(loop)
}

async function loop(): Promise<void> {
  rafId = 0
  const store = useBatchStore.getState()
  const current = runner
  if (!current) return
  if (current.status !== 'running') {
    finish()
    return
  }
  if (!store.paused && !stepping) {
    stepping = true
    try {
      for (let i = 0; i < store.timeScale; i++) {
        await current.step(FIXED_DT)
        if (current.status !== 'running') break
      }
    } catch (error) {
      // Forced-Gemini mode: an AI failure aborts the batch instead of degrading silently.
      current.fail(error instanceof Error ? error.message : String(error))
    } finally {
      stepping = false
    }
  }
  publish(useBatchStore.setState)
  if (current.status === 'running') schedule()
  else finish()
}

function finish(): void {
  publish(useBatchStore.setState)
  const report = runner?.report ?? null
  if (report) {
    safeSet(STORAGE_KEYS.report, report)
    useBatchStore.setState({ pastReport: report })
  }
}

function publish(set: (partial: Partial<BatchStore>) => void): void {
  if (!runner) return
  const snap = runner.snapshot()
  set({
    status: snap.status,
    progress: snap.progress,
    finishedCars: snap.finishedCars,
    requestedCars: snap.requestedCars,
    counts: snap.counts ? { ...snap.counts } : null,
    snapshot: snap,
    events: runner.events.slice(-200),
    // Fresh identities: the sim mutates these in place, zustand would otherwise skip re-renders.
    cases: [...runner.getCases()],
    proposals: [...runner.proposals],
    report: runner.report ?? null,
    degraded: degradedFlag,
    timeScale: snap.timeScale,
  })
}
