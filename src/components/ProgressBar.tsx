/** Props for {@link ProgressBar}. */
export type ProgressBarProps = {
  /** Share of cars that finished the shift, 0-1. */
  progress: number
}

/**
 * Shift progress bar (M8.2): amber, 6px, animated width. The visual bar is decorative; a native
 * progress element carries the value for assistive tech.
 * @param props - Progress 0-1 (clamped).
 * @returns The progress bar.
 */
export function ProgressBar({ progress }: ProgressBarProps) {
  const pct = Math.round(Math.max(0, Math.min(1, Number.isFinite(progress) ? progress : 0)) * 100)
  return (
    <div className="md-progress">
      <div className="md-progress-track" aria-hidden="true">
        <div className="md-progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <progress
        className="md-sr-only"
        aria-label="Shift progress"
        max={100}
        value={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        {pct}%
      </progress>
    </div>
  )
}
