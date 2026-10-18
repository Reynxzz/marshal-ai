import { useState } from 'react'
import type { AssistCase } from '#/lib/marshal/state'
import type { AssistOption } from '#/lib/marshal/types'
import { OPTION_LABELS } from './format'

/** Props for {@link WaitingOnYou}. */
export type WaitingOnYouProps = {
  /** Open assist cases, oldest first. */
  cases: AssistCase[]
  onChoose: (case_id: string, option: AssistOption) => void
  /** Focuses the 3D view on the case's car ("Show in 3D"); the button is hidden without it. */
  onFocus?: (case_id: string) => void
  /** Case the 3D view is focused on. */
  focusedCase?: string | null
}

/**
 * "Waiting on you" (M8.8): one action card per open assist case. The recommended option
 * (Gemini's analysis) is highlighted with `.opt.rec`.
 * @param props - Open cases and choose callback.
 * @returns The waiting-on-you section.
 */
export function WaitingOnYou({ cases, onChoose, onFocus, focusedCase }: WaitingOnYouProps) {
  return (
    <section className="md-panel" aria-labelledby="md-wait-title">
      <div className="md-panel-head">
        <h2 id="md-wait-title">Waiting on you</h2>
        <span className={`md-pill${cases.length ? ' on' : ''}`}>
          {cases.length}
          <span className="md-sr-only"> open</span>
        </span>
      </div>
      {cases.length === 0 ? (
        <div className="md-note">
          Nothing needs you. Gemini and the yard rules are handling every routine car.
        </div>
      ) : (
        cases.map((item) => (
          <AssistCard
            key={item.case_id}
            item={item}
            onChoose={onChoose}
            onFocus={onFocus}
            focused={focusedCase === item.case_id}
          />
        ))
      )}
    </section>
  )
}

type AssistCardProps = {
  item: AssistCase
  onChoose: WaitingOnYouProps['onChoose']
  onFocus?: WaitingOnYouProps['onFocus']
  focused: boolean
}

function AssistCard({ item, onChoose, onFocus, focused }: AssistCardProps) {
  const [broken, setBroken] = useState(false)
  const frame = item.frames[0]
  const recommended = item.analysis?.recommendation
  const alt = `Clip frame from ${item.vin}: ${item.reason}`
  return (
    <article className="md-card" aria-labelledby={`md-card-${item.case_id}`}>
      <div className="md-card-head">
        <h3 id={`md-card-${item.case_id}`}>Assistance · {item.vin}</h3>
        <span className="md-kind">CAR STOPPED</span>
      </div>
      {onFocus ? (
        <button
          type="button"
          className={`md-sim-btn${focused ? ' on' : ''}`}
          aria-pressed={focused}
          aria-label={`Show ${item.vin} in 3D`}
          onClick={() => onFocus(item.case_id)}
        >
          Show in 3D
        </button>
      ) : null}
      {frame && !broken ? (
        <img
          className="md-card-img"
          src={`data:image/jpeg;base64,${frame}`}
          alt={alt}
          onError={() => setBroken(true)}
        />
      ) : (
        <div className="md-card-img md-card-noimg">{alt}</div>
      )}
      <p className="md-card-body">
        {item.reason}.{' '}
        {item.analysis
          ? `${item.analysis.description}${item.analysis.degraded ? ' (offline answer)' : ''}`
          : 'Gemini is looking at the clip.'}
      </p>
      <div className="md-opts">
        {item.options.map((option) => {
          const rec = option === recommended
          return (
            <button
              key={option}
              type="button"
              className={rec ? 'opt rec' : 'opt'}
              onClick={() => onChoose(item.case_id, option)}
            >
              <span className="md-opt-label">{OPTION_LABELS[option]}</span>
              <span className="md-opt-sub">{rec ? 'recommended' : 'option'}</span>
            </button>
          )
        })}
      </div>
    </article>
  )
}
