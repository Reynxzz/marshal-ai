// AI mode switch. Offline is opt-in: set VITE_AI_OFFLINE=true in .env to run the deterministic
// handlers. Otherwise Gemini is forced — a missing key or any model failure surfaces as an
// error instead of silently degrading to offline answers.

export const AI_OFFLINE_ENV = 'VITE_AI_OFFLINE'

export function isOfflineValue(raw: unknown): boolean {
  return ['1', 'true', 'yes', 'on'].includes(
    String(raw ?? '')
      .trim()
      .toLowerCase(),
  )
}

/** Client-side read; Vite injects VITE_* vars into import.meta.env. */
export function offlineAllowed(): boolean {
  return isOfflineValue((import.meta.env as Record<string, unknown> | undefined)?.[AI_OFFLINE_ENV])
}
