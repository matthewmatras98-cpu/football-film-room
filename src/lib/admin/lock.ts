/** Picks and voids close this long before kickoff. */
export const LOCK_LEAD_MS = 10 * 60 * 1000;

/** True when `nowMs` is strictly earlier than kickoff minus the lock cushion. */
export function isBeforeLock(nowMs: number, kickoffIso: string): boolean {
  const kickoffMs = Date.parse(kickoffIso);
  if (!Number.isFinite(kickoffMs)) {
    return false;
  }
  return nowMs < kickoffMs - LOCK_LEAD_MS;
}
