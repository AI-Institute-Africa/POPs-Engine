// Time-left helpers for the provisional-access banner. Pure, so they are easy to test.

export type TimeLeft = { ms: number; label: string; urgent: boolean };

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/** Rounds down: never promises more time than is left. Returns null once expired. */
export function timeLeft(expiresAtIso: string, nowMs: number): TimeLeft | null {
  const end = Date.parse(expiresAtIso);
  if (Number.isNaN(end)) return null;
  const ms = end - nowMs;
  if (ms <= 0) return null;
  const days = Math.floor(ms / DAY);
  const hours = Math.floor((ms % DAY) / HOUR);
  const mins = Math.floor((ms % HOUR) / 60_000);
  const label = days > 0 ? `${days}d ${hours}h` : hours > 0 ? `${hours}h ${mins}m` : `${Math.max(mins, 1)}m`;
  return { ms, label, urgent: ms < DAY };
}
