import type { CarStage, CarState } from '#/lib/marshal/state'
import { whereText } from './format'

/** Chip style per lane group (`.chip.*`). */
export type ChipKind = 'gate' | 'lane' | 'alert' | 'held' | 'rework'

type Group = { key: string; label: string; kind: ChipKind; stages: readonly CarStage[] }

/**
 * Lane groups in display order; parked and shipped cars live in the yard and dock. Only cars
 * with an open assist case ('waiting') are under "Waiting on you"; cars automation gave up on
 * ('held') are the yard crew's, not the supervisor's.
 */
export const LANE_GROUPS: readonly Group[] = [
  {
    key: 'gate',
    label: 'At the gate',
    kind: 'gate',
    stages: ['queued', 'to_gate', 'at_gate', 'test_track', 'report'],
  },
  { key: 'lane', label: 'Driving to a slot', kind: 'lane', stages: ['to_slot', 'parking'] },
  { key: 'you', label: 'Waiting on you', kind: 'alert', stages: ['waiting'] },
  { key: 'held', label: 'Held for the yard crew', kind: 'held', stages: ['held'] },
  { key: 'rework', label: 'Rework bay', kind: 'rework', stages: ['rework'] },
]

/** Props for {@link GateLanes}. */
export type GateLanesProps = {
  /** Cars in spawn order. */
  cars: CarState[]
  /** Selected VIN. */
  selected?: string | null
  onSelect: (vin: string) => void
}

/**
 * Gate and lanes (M8.4): cars grouped by stage as clickable chips.
 * @param props - Cars, selection and select callback.
 * @returns The lanes section.
 */
export function GateLanes({ cars, selected, onSelect }: GateLanesProps) {
  return (
    <section className="md-panel" aria-labelledby="md-lanes-title">
      <div className="md-panel-head">
        <h2 id="md-lanes-title">Gate and lanes</h2>
        <div className="md-note">Line rate 1 car a minute · 3 Gemini checks a car</div>
      </div>
      <div className="md-groups">
        {LANE_GROUPS.map((group) => {
          const members = cars.filter((car) => group.stages.includes(car.stage))
          const titleId = `md-group-${group.key}`
          return (
            <section key={group.key} className="md-group" aria-labelledby={titleId}>
              <div className="md-group-head">
                <h3 id={titleId}>{group.label}</h3>
                <span className="md-count">{members.length}</span>
              </div>
              <div className="md-chips">
                {members.map((car) => (
                  <button
                    key={car.vin}
                    type="button"
                    className={`chip ${group.kind}${selected === car.vin ? ' sel' : ''}`}
                    aria-pressed={selected === car.vin}
                    aria-label={`${car.vin}, ${whereText(car)}`}
                    title={whereText(car)}
                    onClick={() => onSelect(car.vin)}
                  >
                    {car.vin}
                  </button>
                ))}
              </div>
            </section>
          )
        })}
      </div>
    </section>
  )
}
