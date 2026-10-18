import { type CSSProperties, type ReactNode, useEffect, useMemo } from 'react'
import { tokenCssVars } from '#/lib/designTokens'
import { type DecisionTag, type FeedEntry, type MarshalState, yardCells } from '#/lib/marshal/state'
import { useShiftStore } from '#/stores/shift'
import { CarDetail } from './CarDetail'
import { DecisionFeed } from './DecisionFeed'
import { Dock } from './Dock'
import { formatClock } from './format'
import { GateLanes } from './GateLanes'
import { Header } from './Header'
import { KpiBar } from './KpiBar'
import { ProgressBar } from './ProgressBar'
import { WaitingOnYou } from './WaitingOnYou'
import { Yard } from './Yard'
import './dashboard.css'

/** Props for {@link Dashboard}. */
export type DashboardProps = {
  /** The three.js simulator view (SimCanvas); a placeholder is shown without it. */
  simCanvas?: ReactNode
}

/**
 * Slot id -> VIN parked in or heading to the slot.
 * @param state - Orchestrator state.
 * @returns Occupant per slot.
 */
export function yardOccupants(state: MarshalState): Record<string, string> {
  const occupants: Record<string, string> = {}
  for (const [slot, vin] of Object.entries(state.layout)) if (vin) occupants[slot] = vin
  for (const car of Object.values(state.cars)) {
    if (car.slot && !occupants[car.slot]) occupants[car.slot] = car.vin
  }
  return occupants
}

function decisionsByTag(state: MarshalState): Partial<Record<DecisionTag, number>> {
  const counts: Partial<Record<DecisionTag, number>> = {}
  for (const entry of state.feed) counts[entry.tag] = (counts[entry.tag] ?? 0) + 1
  return counts
}

function latestException(state: MarshalState): FeedEntry | undefined {
  for (let index = state.feed.length - 1; index >= 0; index--) {
    if (state.feed[index].exception) return state.feed[index]
  }
  return undefined
}

/**
 * The live shift dashboard (Main.dc.html layout), wired to the shift store.
 * @param props - Optional simulator view for the 3D slot.
 * @returns The dashboard root.
 */
export function Dashboard({ simCanvas }: DashboardProps) {
  const store = useShiftStore()
  const { shift, ensureStarted, pause } = store

  useEffect(() => {
    ensureStarted()
    return () => pause()
  }, [ensureStarted, pause])

  const style = useMemo(() => tokenCssVars() as CSSProperties, [])
  const cars = shift.order.map((vin) => shift.cars[vin]).filter(Boolean)
  const openCases = Object.values(shift.cases)
    .filter((item) => item.status === 'open')
    .sort((a, b) => a.opened_at - b.opened_at)
  const latest = latestException(shift)
  const selectedCar = store.selected ? (shift.cars[store.selected] ?? null) : null

  return (
    <div className="marshal-dash" style={style}>
      <div className="md-wrap">
        <Header
          simTime={shift.simTime}
          playing={store.playing}
          done={shift.order.length > 0 && shift.progress >= 1}
          pauseWhenNeeded={shift.pauseWhenNeeded}
          speed={store.speed}
          onPlayPause={store.togglePlay}
          onNextException={store.nextException}
          onRestart={store.restart}
          onPauseWhenNeeded={store.setPauseWhenNeeded}
          onSpeed={store.setSpeed}
        />
        <ProgressBar progress={shift.progress} />
        <div className="md-headline" aria-live="polite">
          <div className="md-headline-label">LATEST EXCEPTION</div>
          <div className="md-headline-text">
            {latest
              ? `${formatClock(latest.at)} · ${latest.vin}: ${latest.text}`
              : 'None yet. Every car so far has been routine.'}
          </div>
        </div>
        <KpiBar
          kpis={shift.kpis}
          totalCars={store.totalCars}
          byTag={decisionsByTag(shift)}
          trucksGone={shift.trucks.filter((truck) => truck.state === 'left').length}
        />
        <div className="md-columns">
          <div className="md-main">
            <section className="md-panel md-sim" aria-label="Simulator view">
              {simCanvas ?? <div className="md-note">The 3D simulator view appears here.</div>}
            </section>
            <GateLanes cars={cars} selected={store.selected} onSelect={store.select} />
            <Yard
              cells={yardCells(shift)}
              occupants={yardOccupants(shift)}
              selected={store.selected}
              onSelect={store.select}
            />
            <Dock trucks={shift.trucks} />
            <CarDetail car={selectedCar} />
          </div>
          <div className="md-side">
            <WaitingOnYou
              cases={openCases}
              onFocus={store.focusCase}
              focusedCase={store.focusedCase}
              onChoose={(case_id, option) => {
                store.choose(case_id, option)
              }}
            />
            <DecisionFeed feed={shift.feed} />
          </div>
        </div>
        <footer className="md-footer">
          Simulated shift. Gemini answers come from server functions; without a key every check runs
          offline and is marked (offline).
        </footer>
      </div>
    </div>
  )
}
