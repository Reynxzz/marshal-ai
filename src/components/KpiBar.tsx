import type { DecisionTag, Kpis } from '#/lib/marshal/state'

/** Props for {@link KpiBar}. */
export type KpiBarProps = {
  kpis: Kpis
  /** Cars planned for the shift (defaults to the cars off the line). */
  totalCars?: number
  /** Yard capacity (default 80). */
  yardSlots?: number
  /** Decision counts per source. */
  byTag?: Partial<Record<DecisionTag, number>>
  /** Trucks that left the dock. */
  trucksGone?: number
}

type Tile = { key: string; label: string; value: string; sub: string; tone: string }

/**
 * The six KPI tiles (M8.3).
 * @param props - KPIs and context for the sub-lines.
 * @returns A description list with one tile per KPI.
 */
export function KpiBar({
  kpis,
  totalCars,
  yardSlots = 80,
  byTag = {},
  trucksGone = 0,
}: KpiBarProps) {
  const handled =
    kpis.decisions === 0
      ? 100
      : Math.floor((1000 * Math.max(0, kpis.decisions - kpis.neededYou)) / kpis.decisions) / 10
  const tiles: Tile[] = [
    {
      key: 'off',
      label: 'Off the line',
      value: String(kpis.offLine),
      sub: `of ${totalCars ?? kpis.offLine} on this shift`,
      tone: 'text',
    },
    {
      key: 'decisions',
      label: 'Decisions made',
      value: String(kpis.decisions),
      sub: `${byTag.GEMINI ?? 0} by Gemini · ${byTag.YARD ?? 0} by yard rules`,
      tone: 'blue',
    },
    {
      key: 'you',
      label: 'Needed you',
      value: String(kpis.neededYou),
      sub: `${handled}% handled without you`,
      tone: 'gold',
    },
    {
      key: 'yard',
      label: 'In the yard',
      value: `${kpis.inYard} / ${yardSlots}`,
      sub: 'parked cars',
      tone: 'text',
    },
    {
      key: 'shipped',
      label: 'Shipped',
      value: String(kpis.shipped),
      sub: `${trucksGone} trucks gone`,
      tone: 'text',
    },
    {
      key: 'rework',
      label: 'Rework',
      value: String(kpis.rework),
      sub: 'found at the gate or lamp check',
      tone: 'rework',
    },
  ]
  return (
    <dl className="md-kpis">
      {tiles.map((tile) => (
        <div key={tile.key} className="md-kpi">
          <dt className="md-kpi-label">{tile.label}</dt>
          <dd className={`md-kpi-value md-tone-${tile.tone}`}>{tile.value}</dd>
          <dd className="md-kpi-sub">{tile.sub}</dd>
        </div>
      ))}
    </dl>
  )
}
