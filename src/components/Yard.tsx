import { YARD_ROWS, type YardCellState, yardSlotIds } from '#/lib/marshal/state'

const CELL_TEXT: Record<YardCellState, string> = {
  empty: 'empty',
  parked: 'parked',
  fresh: 'just parked',
  res: 'car on its way',
  alert: 'needs you',
}

/** Props for {@link Yard}. */
export type YardProps = {
  /** Slot id -> cell state (missing slots render empty). */
  cells: Record<string, YardCellState>
  /** Slot id -> VIN parked in or heading to the slot. */
  occupants: Record<string, string>
  /** Selected VIN. */
  selected?: string | null
  onSelect: (vin: string) => void
}

/**
 * Yard (M8.5): 8x10 slot grid with row labels A-H.
 * @param props - Cell states, occupants, selection and select callback.
 * @returns The yard section.
 */
export function Yard({ cells, occupants, selected, onSelect }: YardProps) {
  const slots = yardSlotIds()
  const inUse = slots.filter((slot) => (cells[slot] ?? 'empty') !== 'empty').length
  return (
    <section className="md-panel" aria-labelledby="md-yard-title">
      <div className="md-panel-head">
        <h2 id="md-yard-title">
          Yard · {inUse} of {slots.length} slots in use
        </h2>
        <div className="md-legend">
          <span>
            <span className="md-swatch res" aria-hidden="true" />
            Car on its way
          </span>
          <span>
            <span className="md-swatch fresh" aria-hidden="true" />
            Just parked
          </span>
          <span>
            <span className="md-swatch parked" aria-hidden="true" />
            Parked
          </span>
          <span>
            <span className="md-swatch alert" aria-hidden="true" />
            Needs you
          </span>
        </div>
      </div>
      <div className="md-yard-scroll">
        <div className="md-yard">
          <div className="md-yard-rows" aria-hidden="true">
            {YARD_ROWS.map((row) => (
              <div key={row} className="md-yard-row">
                {row}
              </div>
            ))}
          </div>
          <div className="md-yard-grid">
            {slots.map((slot) => {
              const state = cells[slot] ?? 'empty'
              const vin = state === 'empty' ? undefined : occupants[slot]
              const sel = vin !== undefined && vin === selected
              return (
                <button
                  key={slot}
                  type="button"
                  className={`cell ${state}${sel ? ' sel' : ''}`}
                  aria-label={`Slot ${slot}, ${vin ? `${vin}, ` : ''}${CELL_TEXT[state]}`}
                  aria-pressed={vin ? sel : undefined}
                  disabled={!vin}
                  onClick={() => {
                    if (vin) onSelect(vin)
                  }}
                >
                  {vin ? vin.slice(-3) : ''}
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </section>
  )
}
