// Batch orchestrator (T13): the state machine that runs one batch end to end.
// Owns the engine, wires the seven flows, records events, and produces the report.

import type { AiTaskFn } from '#/ai/tasks'
import { type LabResult, runLab } from '#/sim/lab'
import { FIXED_DT, SimEngine } from '../engine'
import { AssistanceDesk } from '../flows/assistance'
import { FunctionalChecker } from '../flows/functional'
import type { FlowEvent, FlowHost } from '../flows/host'
import { mergeVerdicts, ReportRouter } from '../flows/report'
import { VisualInspector } from '../flows/visual'
import { YardFlow } from '../flows/yard'
import type { IncidentActor, IncidentPlacement } from '../incident'
import { placeIncident } from '../incident'
import type { CapturePort } from '../ports'
import type {
  AssistanceCase,
  BatchCar,
  BatchEvent,
  BatchStatus,
  BuildSheet,
  FlowCounts,
  Manifest,
  Proposal,
  Verdict,
} from '../records'
import { planRework } from '../rework'
import { makeRng, type Rng } from '../rng'
import type { CarSnapshot } from '../vehicle'
import type { World } from '../world'
import { computeProgress, emptyCounts } from './counts'
import { buildDigest, buildFeedback } from './feedback'
import { buildManifest } from './manifest'
import { type BatchReport, buildReport, type LabelSummary } from './report'

export type Phase =
  | 'queued'
  | 'to_gate'
  | 'at_gate'
  | 'review_hold'
  | 'assist'
  | 'to_track'
  | 'at_track'
  | 'to_report'
  | 'at_report'
  | 'to_rework'
  | 'at_rework'
  | 'to_yard'
  | 'shipped'
  | 'held'

export type BatchRunnerOptions = {
  batchId: string
  cars: number
  seed?: number
  timeScale?: number
  ai: AiTaskFn
  capture: CapturePort
  world: World
  onEvent?: (ev: BatchEvent) => void
  model?: string
  generatedAt?: () => string
  degraded?: () => boolean
  /** Explicit case-library selection from the planner (falls back to a seeded sample). */
  caseIds?: string[]
  plan?: { source: 'gemini' | 'offline'; rationale: string }
}

export type BatchSnapshot = {
  status: BatchStatus
  progress: number
  finishedCars: number
  requestedCars: number
  counts: FlowCounts
  cars: BatchCar[]
  carSnapshots: CarSnapshot[]
  actors: IncidentActor[]
  simTime: number
  timeScale: number
}

type CarRT = {
  vin: string
  batchCar: BatchCar
  buildSheet: BuildSheet
  phase: Phase
  missionSeq: number
  vehicleDone: boolean
  routineDone: boolean
  trackQueue: Array<{ step: string; telemetry: Record<string, unknown> }>
  checker: FunctionalChecker
  visual?: Verdict
  functional?: Verdict
  incident?: IncidentPlacement
  incidentTriggered: boolean
  reworkPlan: string[]
  reworkIndex: number
  reworkCycles: number
  holdUntil?: number
  reviewOpenedAt?: number
  truckId?: string
}

const REWORK_HOLD_S = 12
const PROPOSAL_AUTO_S = 20
const DEFAULT_TIME_LIMIT_S = 3600

export class BatchRunner {
  readonly batchId: string
  readonly manifest: Manifest
  readonly counts: FlowCounts = emptyCounts()
  readonly events: BatchEvent[] = []
  readonly proposals: Proposal[] = []
  status: BatchStatus = 'queued'
  lab?: LabResult
  report?: BatchReport
  error?: string
  falseAlarms = 0

  private readonly engine: SimEngine
  private readonly host: FlowHost
  private readonly cars: CarRT[] = []
  private readonly byVin = new Map<string, CarRT>()
  private readonly actors: IncidentActor[] = []
  private readonly listeners = new Set<(ev: BatchEvent) => void>()
  private readonly desk: AssistanceDesk
  private readonly yard: YardFlow
  private readonly visualInspector: VisualInspector
  private readonly reportRouter: ReportRouter
  private readonly timeLimitS = DEFAULT_TIME_LIMIT_S
  private finished = 0
  private finalized = false

  constructor(private readonly opts: BatchRunnerOptions) {
    this.batchId = opts.batchId
    this.manifest = buildManifest(opts.batchId, opts.cars, opts.seed, opts.caseIds)
    const timeScale = opts.timeScale ?? 4
    this.engine = new SimEngine(opts.world, { seed: this.manifest.seed, timeScale })
    const rng: Rng = makeRng(this.manifest.seed ^ 0x9e3779b9)
    this.host = {
      engine: this.engine,
      world: opts.world,
      ai: opts.ai,
      capture: opts.capture,
      rng,
      batchId: opts.batchId,
      emit: (ev: FlowEvent) => this.emit(ev),
    }
    this.desk = new AssistanceDesk(this.host)
    this.yard = new YardFlow(this.host, this.manifest.trucks)
    this.visualInspector = new VisualInspector(this.host)
    this.reportRouter = new ReportRouter(this.host)

    this.engine.on((ev) => {
      const rt = this.byVin.get(ev.vin)
      if (!rt) return
      if (ev.type === 'MissionDone') rt.vehicleDone = true
      else if (ev.type === 'TrackStep')
        rt.trackQueue.push({ step: ev.name, telemetry: ev.telemetry })
      else if (ev.type === 'RoutineDone') rt.routineDone = true
    })
  }

  onEvent(cb: (ev: BatchEvent) => void): () => void {
    this.listeners.add(cb)
    return () => {
      this.listeners.delete(cb)
    }
  }

  start(): void {
    if (this.opts.plan) {
      this.emit({
        source: 'batch',
        title: 'Batch planned',
        detail: `source=${this.opts.plan.source} ${this.opts.plan.rationale}`,
        level: 'info',
      })
    }
    for (const batchCar of this.manifest.cars) {
      const rt: CarRT = {
        vin: batchCar.vin,
        batchCar,
        buildSheet: batchCar.buildSheet ?? { model: 'M', colour: 'grey', options: [] },
        phase: 'queued',
        missionSeq: 0,
        vehicleDone: false,
        routineDone: false,
        trackQueue: [],
        checker: new FunctionalChecker(this.host),
        incidentTriggered: false,
        reworkPlan: [],
        reworkIndex: 0,
        reworkCycles: 0,
      }
      this.cars.push(rt)
      this.byVin.set(rt.vin, rt)
      this.engine.spawn(rt.vin, 'line_end', { colour: batchCar.colour ?? '#cccccc' })
      if (batchCar.expected.kind === 'dead_lamp' && batchCar.expected.faultStep) {
        this.engine.setFault(rt.vin, { step: batchCar.expected.faultStep })
      }
      this.emit({
        flow: 1,
        source: 'engine',
        title: 'Car created',
        detail: `${rt.vin} (${batchCar.expected.kind})`,
        level: 'info',
      })
    }
    this.status = 'running'
    this.emit({
      flow: 1,
      source: 'batch',
      title: 'Batch started',
      detail: `${this.manifest.cars.length} car(s)`,
      level: 'info',
    })
  }

  async step(dt = FIXED_DT): Promise<void> {
    if (this.status !== 'running') return
    this.engine.step(dt)
    this.desk.tick(this.engine.simTime)
    this.applyDecisions()
    this.autoResolveProposals()

    for (const rt of this.cars) {
      if (rt.phase === 'shipped' || rt.phase === 'held') continue
      await this.advance(rt)
    }
    this.updateProgress()

    if (this.engine.simTime > this.timeLimitS) {
      this.status = 'failed'
      this.error = `batch exceeded ${this.timeLimitS}s simulated time`
      this.emit({ source: 'batch', title: 'Batch timeout', detail: this.error, level: 'bad' })
      return
    }
    if (!this.finalized && this.cars.every((c) => c.phase === 'shipped' || c.phase === 'held')) {
      await this.finalize()
    }
  }

  cancel(): void {
    if (this.status === 'done' || this.status === 'failed' || this.status === 'cancelled') return
    this.status = 'cancelled'
    this.actors.length = 0
    this.emit({ source: 'batch', title: 'Batch cancelled', detail: '', level: 'warn' })
  }

  /** Stop the batch on an unrecoverable error (e.g. a failed Gemini call in forced mode). */
  fail(error: string): void {
    if (this.status !== 'running' && this.status !== 'queued') return
    this.status = 'failed'
    this.error = error
    this.actors.length = 0
    this.emit({ source: 'batch', title: 'Batch failed', detail: error, level: 'bad' })
  }

  setTimeScale(n: number): void {
    this.engine.timeScale = n
  }

  closeZone(zone: string): void {
    this.yard.closeZone(zone)
    this.emit({ flow: 1, source: 'supervisor', title: 'Zone closed', detail: zone, level: 'warn' })
  }

  decideCase(caseId: string, option: string, by: string): void {
    this.desk.decide(caseId, option, by)
    this.applyDecisions()
  }

  decideProposal(id: string, approve: boolean, by: string): void {
    const p = this.proposals.find((x) => x.id === id)
    if (p?.status !== 'pending') return
    p.status = approve ? 'approved' : 'rejected'
    p.decidedBy = by
    const rt = this.cars.find((c) => c.phase === 'review_hold')
    if (rt && !approve) this.falseAlarms++
    this.emit({
      flow: 3,
      source: 'supervisor',
      title: `Proposal ${p.status}`,
      detail: p.summary,
      level: 'info',
    })
  }

  getCases(): AssistanceCase[] {
    return this.desk.cases
  }

  getActors(): IncidentActor[] {
    return this.actors
  }

  snapshot(): BatchSnapshot {
    return {
      status: this.status,
      progress: computeProgress(this.finished, this.manifest.cars.length),
      finishedCars: this.finished,
      requestedCars: this.manifest.cars.length,
      counts: this.counts,
      cars: this.cars.map((c) => c.batchCar),
      carSnapshots: this.engine.snapshot(),
      actors: [...this.actors],
      simTime: this.engine.simTime,
      timeScale: this.engine.timeScale,
    }
  }

  private carSnapshot(vin: string): CarSnapshot {
    const snap = this.engine.snapshot().find((s) => s.vin === vin)
    if (snap) return snap
    return {
      vin,
      x: 0,
      y: 0,
      yaw: 0,
      speedKmh: 0,
      state: 'stopped',
      colour: '#cccccc',
      lamps: { headlights: true, brake_lights: false },
    }
  }

  private dispatch(
    rt: CarRT,
    waypoints: string[],
    opts: { routine?: string; routineSteps?: string[] } = {},
  ): void {
    rt.missionSeq++
    rt.vehicleDone = false
    this.engine.send({
      type: 'go',
      vin: rt.vin,
      mission: {
        id: `${this.batchId}-${rt.vin}-${rt.missionSeq}`,
        waypoints,
        routine: opts.routine,
        routineSteps: opts.routineSteps,
      },
    })
  }

  private async advance(rt: CarRT): Promise<void> {
    await this.drainTrack(rt)
    switch (rt.phase) {
      case 'queued':
        this.tryRelease(rt)
        break
      case 'to_gate':
        await this.onToGate(rt)
        break
      case 'at_gate':
        break
      case 'assist':
      case 'review_hold':
      case 'held':
        break
      case 'at_track':
        this.onAtTrack(rt)
        break
      case 'to_track':
        this.onToTrack(rt)
        break
      case 'at_report':
        await this.onAtReport(rt)
        break
      case 'to_report':
        if (rt.vehicleDone) {
          rt.vehicleDone = false
          rt.phase = 'at_report'
        }
        break
      case 'to_rework':
        if (rt.vehicleDone) {
          rt.vehicleDone = false
          rt.phase = 'at_rework'
          rt.holdUntil = this.engine.simTime + REWORK_HOLD_S
        }
        break
      case 'at_rework':
        this.onAtRework(rt)
        break
      case 'to_yard':
        this.onToYard(rt)
        break
      case 'shipped':
        break
    }
  }

  private tryRelease(rt: CarRT): void {
    if (this.gateOccupied()) return
    if (rt.batchCar.expected.kind === 'incident') {
      const route = this.engine.world.route('line_end', 'inspection_gate')
      const placement = placeIncident(this.engine.world, route, rt.batchCar.expected.blockers)
      if (placement) {
        rt.incident = placement
        this.actors.push(...placement.actors)
      } else {
        this.emit({
          flow: 2,
          source: 'incident',
          title: 'incident_not_placed',
          detail: rt.vin,
          level: 'warn',
        })
      }
    }
    this.dispatch(rt, ['inspection_gate'])
    rt.phase = 'to_gate'
    this.counts.flow1.released++
    this.emit({
      flow: 1,
      source: 'yard',
      title: 'Released',
      detail: `${rt.vin} -> inspection_gate`,
      level: 'info',
    })
  }

  private gateOccupied(): boolean {
    return this.cars.some((c) =>
      ['to_gate', 'at_gate', 'review_hold', 'assist', 'to_track'].includes(c.phase),
    )
  }

  private async onToGate(rt: CarRT): Promise<void> {
    const vehicle = this.engine.get(rt.vin)
    if (
      rt.incident &&
      !rt.incidentTriggered &&
      vehicle &&
      vehicle.distance >= rt.incident.blockDistanceM
    ) {
      rt.incidentTriggered = true
      rt.phase = 'assist'
      this.engine.stop(rt.vin, 'assist')
      await this.desk.assess(rt.vin, rt.incident.situation)
      this.counts.flow2.cases++
      this.counts.flow2.open++
      return
    }

    if (!rt.vehicleDone) return
    rt.vehicleDone = false
    rt.phase = 'at_gate'
    const result = await this.visualInspector.inspect(this.carSnapshot(rt.vin), rt.buildSheet)
    rt.visual = result.verdict
    rt.batchCar.gateVerdict = result.verdict
    if (result.verdict === 'FAIL') this.counts.flow3.fail++
    else if (result.verdict === 'REVIEW') this.counts.flow3.review++
    else this.counts.flow3.pass++

    if (result.verdict === 'FAIL') {
      this.dispatch(rt, ['report_point'])
      rt.phase = 'to_report'
    } else if (result.verdict === 'REVIEW') {
      const proposal: Proposal = {
        id: `${this.batchId}-REV-${this.proposals.length + 1}`,
        kind: 'review',
        summary: `Review ${rt.vin} gate inspection`,
        payload: { vin: rt.vin },
        status: 'pending',
      }
      this.proposals.push(proposal)
      rt.reviewOpenedAt = this.engine.simTime
      rt.phase = 'review_hold'
    } else {
      this.startFunctional(rt)
    }
  }

  private startFunctional(rt: CarRT): void {
    rt.checker.reset()
    rt.routineDone = false
    this.dispatch(rt, ['test_track'], { routine: 'R-12' })
    rt.phase = 'to_track'
  }

  private onToTrack(rt: CarRT): void {
    if (rt.vehicleDone) {
      rt.vehicleDone = false
      rt.phase = 'at_track'
    }
  }

  private onAtTrack(rt: CarRT): void {
    if (!rt.routineDone) return
    rt.routineDone = false
    const result = rt.checker.result(rt.vin)
    rt.functional = result.verdict
    if (result.verdict === 'FAIL') this.counts.flow4.fail++
    else if (result.verdict === 'REVIEW') this.counts.flow4.disagreements++
    else this.counts.flow4.pass++
    this.dispatch(rt, ['report_point'])
    rt.phase = 'to_report'
  }

  private async drainTrack(rt: CarRT): Promise<void> {
    if (rt.phase !== 'to_track' && rt.phase !== 'at_track') return
    while (rt.trackQueue.length > 0) {
      const item = rt.trackQueue.shift()
      if (!item) break
      await rt.checker.decideStep(this.carSnapshot(rt.vin), item.step, item.telemetry)
    }
  }

  private async onAtReport(rt: CarRT): Promise<void> {
    const verdict = mergeVerdicts(rt.visual ?? 'PASS', rt.functional ?? 'PASS')
    rt.batchCar.reportVerdict = verdict
    rt.batchCar.state = 'report'

    if (verdict === 'PASS') {
      this.counts.flow5.pass++
      const slot = this.yard.assignSlot()
      if (!slot) {
        rt.phase = 'held'
        rt.batchCar.state = 'held'
        this.engine.despawn(rt.vin)
        this.emit({
          flow: 5,
          source: 'report',
          title: 'No yard slot',
          detail: rt.vin,
          level: 'bad',
        })
        return
      }
      rt.batchCar.slot = slot
      this.dispatch(rt, ['yard.entry', slot])
      rt.phase = 'to_yard'
      return
    }

    this.counts.flow5.rework++
    const findings = this.collectFindings(rt)
    const bays = planRework(findings)
    rt.reworkPlan = bays
    rt.reworkIndex = 0
    await this.reportRouter.write(findings, bays)
    const proposal: Proposal = {
      id: `${this.batchId}-RW-${this.proposals.length + 1}`,
      kind: 'rework',
      summary: `Rework ${rt.vin}: ${findings.join(', ') || 'reinspect'}`,
      payload: { vin: rt.vin, bays },
      status: 'approved',
      decidedBy: 'auto',
    }
    this.proposals.push(proposal)
    this.emit({
      flow: 5,
      source: 'report',
      title: 'Rework required',
      detail: `${rt.vin} verdict=${verdict} bays=${bays.join('->')}`,
      level: 'warn',
    })
    this.sendToBay(rt)
  }

  private collectFindings(rt: CarRT): string[] {
    const findings: string[] = []
    const visual = rt.visual
    if (visual === 'FAIL') findings.push('colour mismatch')
    const functional = rt.functional
    if (functional === 'FAIL') findings.push('lamp/electrical failure')
    if (rt.batchCar.expected.kind === 'wrong_colour') findings.push('colour mismatch')
    if (rt.batchCar.expected.kind === 'dead_lamp') findings.push('lamp/electrical failure')
    return [...new Set(findings)]
  }

  private sendToBay(rt: CarRT): void {
    const bay = rt.reworkPlan[rt.reworkIndex] ?? 'rework.body'
    this.dispatch(rt, [bay])
    rt.phase = 'to_rework'
    rt.batchCar.state = 'rework'
  }

  private onAtRework(rt: CarRT): void {
    if (rt.holdUntil === undefined || this.engine.simTime < rt.holdUntil) return
    rt.holdUntil = undefined
    rt.reworkIndex++
    if (rt.reworkIndex < rt.reworkPlan.length) {
      this.sendToBay(rt)
      return
    }
    // Reinspection after the last bay.
    const rebuilt = rt.reworkPlan.includes('rework.electrical')
    if (rt.batchCar.expected.kind === 'dead_lamp' && rebuilt) {
      this.engine.clearFault(rt.vin)
    }
    const fixed = rt.batchCar.expected.kind !== 'wrong_colour'
    if (fixed) {
      rt.batchCar.reportVerdict = 'PASS'
      this.counts.flow5.pass++
      const slot = this.yard.assignSlot()
      if (!slot) {
        rt.phase = 'held'
        return
      }
      rt.batchCar.slot = slot
      this.dispatch(rt, ['yard.entry', slot])
      rt.phase = 'to_yard'
      this.emit({
        flow: 5,
        source: 'report',
        title: 'Reinspection PASS',
        detail: rt.vin,
        level: 'good',
      })
      return
    }
    rt.reworkCycles++
    if (rt.reworkCycles < 2) {
      rt.reworkIndex = 0
      this.sendToBay(rt)
    } else {
      rt.phase = 'held'
      rt.batchCar.state = 'held'
      this.engine.despawn(rt.vin)
      this.emit({
        flow: 5,
        source: 'report',
        title: 'Rework failed',
        detail: `${rt.vin} still non-PASS`,
        level: 'bad',
      })
    }
  }

  private onToYard(rt: CarRT): void {
    if (!rt.vehicleDone) return
    rt.vehicleDone = false
    rt.phase = 'shipped'
    rt.batchCar.state = 'shipped'
    this.counts.flow1.inYard++
    this.counts.flow1.shipped++
    this.counts.flow5.shipped++
    const truck = this.yard.assignTruck(rt.vin)
    rt.batchCar.truckId = truck.truckId
    this.emit({
      flow: 1,
      source: 'yard',
      title: 'Shipped',
      detail: `${rt.vin} via ${truck.truckId}`,
      level: 'good',
    })
  }

  private applyDecisions(): void {
    for (const d of this.desk.drainDecisions()) {
      const rt = this.byVin.get(d.vin)
      if (!rt) continue
      this.counts.flow2.resolved++
      this.counts.flow2.open = Math.max(0, this.counts.flow2.open - 1)
      // Box + worker are cleared when the lane is freed or the car is rerouted.
      if (rt.incident) {
        for (const actor of rt.incident.actors) {
          const i = this.actors.findIndex((a) => a.id === actor.id)
          if (i >= 0) this.actors.splice(i, 1)
        }
      }
      if (d.option === 'wait_for_removal') {
        this.emit({
          flow: 2,
          source: 'assist',
          title: 'Worker cleared',
          detail: `${d.vin} waited for removal`,
          level: 'info',
        })
      }
      this.engine.resume(d.vin)
      rt.phase = 'to_gate'
      this.emit({
        flow: 2,
        source: 'assist',
        title: 'Lane cleared',
        detail: `${d.vin} option=${d.option} by=${d.by}`,
        level: 'good',
      })
    }
    for (const c of this.desk.cases) {
      if (c.status === 'escalated')
        this.counts.flow2.escalated = Math.max(this.counts.flow2.escalated, 1)
    }
  }

  private autoResolveProposals(): void {
    for (const rt of this.cars) {
      if (rt.phase !== 'review_hold') continue
      if (rt.reviewOpenedAt === undefined) continue
      if (this.engine.simTime - rt.reviewOpenedAt < PROPOSAL_AUTO_S) continue
      const proposal = this.proposals.find((p) => p.status === 'pending' && p.kind === 'review')
      if (proposal) {
        proposal.status = 'approved'
        proposal.decidedBy = 'auto'
      }
      this.falseAlarms++
      rt.phase = 'at_gate'
      this.startFunctional(rt)
    }
  }

  private updateProgress(): void {
    this.finished = this.cars.filter((c) => c.phase === 'shipped' || c.phase === 'held').length
  }

  private async finalize(): Promise<void> {
    this.finalized = true
    const incident = this.manifest.cars.some((c) => c.expected.kind === 'incident')
    this.lab = await runLab({
      host: this.host,
      incident,
      request: this.desk.cases[0]?.situation,
      seed: this.manifest.seed,
    })
    this.counts.flow6 = this.lab.flow6
    this.counts.flow7 = this.lab.flow7
    this.status = 'done'

    const labels = this.computeLabels()
    const exceptions = this.events
      .filter((e) => e.level === 'warn' || e.level === 'bad')
      .map((e) => e.title)
    const digest = buildDigest({
      batchId: this.batchId,
      batchSize: this.manifest.cars.length,
      counts: this.counts,
      labels,
      exceptions,
    })
    const gemini = await buildFeedback(
      this.opts.ai,
      digest,
      this.manifest.cars.length,
      this.opts.degraded?.() ?? false,
    )
    this.report = buildReport({
      batchId: this.batchId,
      status: this.status,
      generatedAt: this.opts.generatedAt?.() ?? new Date(0).toISOString(),
      model: this.opts.model ?? 'gemini-2.5-flash',
      createdCars: this.manifest.cars.length,
      requestedCars: this.manifest.cars.length,
      finishedCars: this.finished,
      durationS: this.engine.simTime,
      counts: this.counts,
      labels,
      gemini: { ...gemini.feedback, degraded: gemini.degraded, error: gemini.error },
      events: this.events,
    })

    this.emit({
      source: 'batch',
      title: 'Batch done',
      detail: `finished=${this.finished}`,
      level: 'good',
    })
  }

  private computeLabels(): LabelSummary {
    const cars = this.cars.map((rt) => {
      const kind = rt.batchCar.expected.kind
      let caught = false
      if (kind === 'wrong_colour') caught = rt.visual === 'FAIL'
      else if (kind === 'dead_lamp') caught = rt.functional === 'FAIL'
      else if (kind === 'incident')
        caught = this.desk.cases.some((c) => c.vin === rt.vin && c.status !== 'failed')
      return { vin: rt.vin, expected: kind, caught }
    })
    const relevant = cars.filter((c) => c.expected !== 'clean')
    return {
      total: relevant.length,
      caught: relevant.filter((c) => c.caught).length,
      missed: relevant.filter((c) => !c.caught).length,
      falseAlarms: this.falseAlarms,
      cars,
    }
  }

  private emit(ev: FlowEvent): void {
    const batchEvent: BatchEvent = {
      batchId: this.batchId,
      seq: this.events.length + 1,
      at: this.engine.simTime,
      ...ev,
    }
    this.events.push(batchEvent)
    for (const cb of this.listeners) cb(batchEvent)
    this.opts.onEvent?.(batchEvent)
  }
}
