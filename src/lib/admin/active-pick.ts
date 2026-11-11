/** A pick id is active when that id has a pick and no void. Versions: base, then -r2..-r9. */

export interface ActivePickRef {
  pick_id: string;
  game_id: string;
  market: string;
  status: string;
}

function version(pickId: string, gameId: string, market: string): number {
  const base = `${gameId}-${market}`;
  if (pickId === base) {
    return 1;
  }
  const prefix = `${base}-r`;
  if (!pickId.startsWith(prefix)) {
    return 0;
  }
  const suffix = pickId.slice(prefix.length);
  if (!/^\d+$/.test(suffix)) {
    return 0;
  }
  return Number(suffix);
}

/** The highest active id for this game and market, or null when every version is void or missing. */
export function activePickId(picks: readonly ActivePickRef[], gameId: string, market: string): string | null {
  const active: string[] = [];
  for (const pick of picks) {
    if (pick.game_id !== gameId || pick.market !== market || pick.status === 'void') {
      continue;
    }
    if (!active.includes(pick.pick_id)) {
      active.push(pick.pick_id);
    }
  }
  if (active.length === 0) {
    return null;
  }
  return active.reduce((best, pickId) => (version(pickId, gameId, market) > version(best, gameId, market) ? pickId : best));
}

/** One active row per game and market, in first-seen order. Voided history is left out. */
export function activePicks<T extends ActivePickRef>(picks: readonly T[]): T[] {
  const chosen: T[] = [];
  const seen = new Set<string>();
  for (const pick of picks) {
    const key = `${pick.game_id}\0${pick.market}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    const pickId = activePickId(picks, pick.game_id, pick.market);
    if (!pickId) {
      continue;
    }
    const row = picks.find(
      (item) =>
        item.game_id === pick.game_id && item.market === pick.market && item.pick_id === pickId && item.status !== 'void',
    );
    if (row) {
      chosen.push(row);
    }
  }
  return chosen;
}
