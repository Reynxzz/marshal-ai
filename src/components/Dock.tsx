import type { Truck } from '#/lib/marshal/state'

/** Props for {@link Dock}. */
export type DockProps = { trucks: Truck[] }

/**
 * Status line for a truck card.
 * @param truck - The truck.
 * @returns e.g. 'Loading 3 cars'.
 */
export function truckStatus(truck: Truck): string {
  const cars = `${truck.slots.length} car${truck.slots.length === 1 ? '' : 's'}`
  switch (truck.state) {
    case 'loading':
      return `Loading ${cars}`
    case 'waiting':
      return 'Here early · waiting'
    case 'late':
      return 'Running late'
    case 'left':
      return `Left · ${cars}`
    default:
      return 'Due at the dock'
  }
}

/**
 * Dock (M8.6): one card per truck with its state class.
 * @param props - Trucks.
 * @returns The dock section.
 */
export function Dock({ trucks }: DockProps) {
  return (
    <section className="md-panel" aria-labelledby="md-dock-title">
      <h2 id="md-dock-title">Dock · trucks of 10</h2>
      {trucks.length === 0 ? (
        <div className="md-note">No trucks booked for this shift.</div>
      ) : (
        <ul className="md-trucks">
          {trucks.map((truck) => (
            <li
              key={truck.id}
              className={truck.state === 'truck' ? 'truck' : `truck ${truck.state}`}
            >
              <span className="md-truck-name">{truck.id}</span>
              <span className="md-truck-status">{truckStatus(truck)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
