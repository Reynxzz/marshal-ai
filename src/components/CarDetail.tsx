import type { CarState } from '#/lib/marshal/state'
import { formatClock, offlineLabel, whereText } from './format'

/** Props for {@link CarDetail}. */
export type CarDetailProps = {
  /** Selected car, or null when nothing is selected. */
  car: CarState | null
}

/**
 * Car detail (M8.7): where the car is and every decision made about it, newest first.
 * @param props - Selected car.
 * @returns The car detail section.
 */
export function CarDetail({ car }: CarDetailProps) {
  if (!car) {
    return (
      <section className="md-panel" aria-label="Car detail">
        <div className="md-note">
          Click any car, on the lanes or in the yard, to see every decision made about it.
        </div>
      </section>
    )
  }
  const mismatch = car.expectedColour !== car.colour
  const desc = mismatch
    ? `${car.colour} · build sheet says ${car.expectedColour}`
    : `${car.colour} · matches build sheet`
  const lines = car.decisions
    .map((decision, order) => ({ decision, id: `${car.vin}-${order}` }))
    .reverse()
  return (
    <section className="md-panel" aria-labelledby="md-car-title">
      <h2 id="md-car-title">
        Car {car.vin} · {desc}
      </h2>
      <div className="md-where">
        Where: <strong>{whereText(car)}</strong>
      </div>
      {lines.length === 0 ? (
        <div className="md-note">No decisions yet.</div>
      ) : (
        <ol className="md-lines" reversed>
          {lines.map(({ decision, id }) => (
            <li key={id} className="md-line">
              <span className="md-time">{formatClock(decision.at)}</span>
              <span className={`tag ${decision.tag}`}>{decision.tag}</span>
              <span>
                {decision.text}
                {decision.degraded ? (
                  <span className="md-offline"> {offlineLabel(decision)}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
