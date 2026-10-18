import { useState } from 'react'
import type { FeedEntry } from '#/lib/marshal/state'
import { formatClock, offlineLabel } from './format'

/** Newest entries kept on screen. */
export const FEED_LIMIT = 200

/** Props for {@link DecisionFeed}. */
export type DecisionFeedProps = {
  /** Every decision, oldest first. */
  feed: FeedEntry[]
  /** Initial filter (default: everything). */
  exceptionsOnly?: boolean
}

/**
 * Decision feed (M8.9): newest first, with an "Everything" / "Exceptions only" toggle.
 * @param props - Feed entries and initial filter.
 * @returns The feed section.
 */
export function DecisionFeed({ feed, exceptionsOnly = false }: DecisionFeedProps) {
  const [onlyExceptions, setOnlyExceptions] = useState(exceptionsOnly)
  const entries = feed
    .map((entry, order) => ({ entry, id: `f${order}` }))
    .filter(({ entry }) => !onlyExceptions || entry.exception)
    .slice(-FEED_LIMIT)
    .reverse()
  return (
    <section className="md-panel" aria-labelledby="md-feed-title">
      <div className="md-panel-head">
        <h2 id="md-feed-title">Decision feed</h2>
        <div className="md-segs">
          <button
            type="button"
            className={onlyExceptions ? 'seg' : 'seg on'}
            aria-pressed={!onlyExceptions}
            onClick={() => setOnlyExceptions(false)}
          >
            Everything
          </button>
          <button
            type="button"
            className={onlyExceptions ? 'seg on' : 'seg'}
            aria-pressed={onlyExceptions}
            onClick={() => setOnlyExceptions(true)}
          >
            Exceptions only
          </button>
        </div>
      </div>
      <ul className="md-feed" aria-label="Decisions, newest first">
        {entries.length === 0 ? (
          <li className="md-note">No decisions yet.</li>
        ) : (
          entries.map(({ entry, id }) => (
            <li key={id} className="md-line">
              <span className="md-time">{formatClock(entry.at)}</span>
              <span className={`tag ${entry.tag}`}>{entry.tag}</span>
              <span>
                {entry.vin ? `${entry.vin}: ` : ''}
                {entry.text}
                {entry.degraded ? <span className="md-offline"> {offlineLabel(entry)}</span> : null}
              </span>
            </li>
          ))
        )}
      </ul>
    </section>
  )
}
