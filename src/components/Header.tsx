import { formatClock } from './format'

/** Playback speeds offered in the header. */
export const SPEEDS = [1, 2, 4, 8, 16] as const

/** Props for {@link Header}. */
export type HeaderProps = {
  /** Sim seconds since the shift started. */
  simTime: number
  playing: boolean
  /** True once every car finished the shift. */
  done: boolean
  pauseWhenNeeded: boolean
  /** Sim seconds per real second. */
  speed: number
  /** Shift description shown next to the brand. */
  shiftInfo?: string
  onPlayPause: () => void
  onNextException: () => void
  onRestart: () => void
  onPauseWhenNeeded: (on: boolean) => void
  onSpeed?: (speed: number) => void
}

/**
 * Shift header (M8.1): brand, shift info, clock and playback controls.
 * @param props - Clock, playback state and callbacks.
 * @returns The header element.
 */
export function Header(props: HeaderProps) {
  const playLabel = props.playing ? 'Pause' : props.done ? 'Play again' : 'Play shift'
  return (
    <header className="md-header">
      <div className="md-brand">
        <div className="md-brand-name">Marshal</div>
        <div className="md-shift-info">
          {props.shiftInfo ?? 'Plant supervisor · Shift A · from 06:00'}
        </div>
        <span className="md-badge">SIMULATED SHIFT</span>
      </div>
      <div className="md-controls">
        <div className="md-clock">
          <span className="md-sr-only">Shift clock </span>
          {formatClock(props.simTime)}
        </div>
        <button type="button" className="ctl main" onClick={props.onPlayPause}>
          {playLabel}
        </button>
        <button type="button" className="ctl" onClick={props.onNextException}>
          Next exception
        </button>
        <button type="button" className="ctl" onClick={props.onRestart}>
          Restart
        </button>
        {props.onSpeed ? (
          <label className="md-speed">
            Speed
            <select
              value={props.speed}
              onChange={(event) => props.onSpeed?.(Number(event.target.value))}
            >
              {SPEEDS.map((speed) => (
                <option key={speed} value={speed}>
                  {speed}x
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label className="md-toggle">
          <input
            type="checkbox"
            checked={props.pauseWhenNeeded}
            onChange={(event) => props.onPauseWhenNeeded(event.target.checked)}
          />
          Pause when I'm needed
        </label>
      </div>
    </header>
  )
}
