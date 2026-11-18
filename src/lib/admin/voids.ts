/** Fields `voidsByWeek` reads from one `picks_view.json` row. */
export interface VoidViewRow {
  pick_id: string;
  game_id: string;
  market: string;
  side: string;
  status: string;
  week: number;
  line_at_pick: number;
  picked_at: string;
  voided_at: string | null;
}

/** One re-pick attached to a void. Null on the entry when there is no later version. */
export interface SlateRepick {
  pick_id: string;
  side: string;
  line_at_pick: number;
  picked_at: string;
}

/**
 * One counted void, in the shape documented under "Voided picks (picks_view.json)".
 * `pick` is the voided row, not the void CSV row.
 */
export interface SlateVoid {
  game_id: string;
  week: number;
  pick: {
    side: string;
    market: string;
    line_at_pick: number;
    picked_at: string;
  };
  voided_at: string | null;
  repick: SlateRepick | null;
}

/**
 * Version of `{game_id}-{market}`. 1 is the unsuffixed id. 2–9 are `-r2` through `-r9`.
 * There is no `-r1`. This is `pick_version` from the pipeline schema.
 */
export function pickVersion(pickId: string, gameId: string, market: string): number | null {
  const base = `${gameId}-${market}`;
  if (pickId === base) {
    return 1;
  }
  const prefix = `${base}-r`;
  if (pickId.length !== prefix.length + 1 || !pickId.startsWith(prefix)) {
    return null;
  }
  const digit = pickId[pickId.length - 1] ?? '';
  if (!'23456789'.includes(digit)) {
    return null;
  }
  return Number(digit);
}

/**
 * The id a replacement supersedes. `previous_pick_id` from the pipeline schema.
 * `-r2` steps back to the unsuffixed id. `-rN` steps back to `-r{N-1}`.
 */
export function previousPickId(pickId: string, gameId: string, market: string): string | null {
  const version = pickVersion(pickId, gameId, market);
  if (version === null || version < 2) {
    return null;
  }
  const base = `${gameId}-${market}`;
  if (version === 2) {
    return base;
  }
  return `${base}-r${version - 1}`;
}

/**
 * Slate `voids`, grouped by week, built from the view alone.
 * A re-pick is the row whose `previous_pick_id` is this void. A `rejected_late` row is not attached.
 */
export function voidsByWeek(rows: readonly VoidViewRow[]): Record<string, SlateVoid[]> {
  const successor = new Map<string, VoidViewRow>();
  for (const row of rows) {
    const previous = previousPickId(row.pick_id, row.game_id, row.market);
    if (previous) {
      successor.set(previous, row);
    }
  }

  const grouped: Record<string, SlateVoid[]> = {};
  for (const row of rows) {
    if (row.status !== 'void') {
      continue;
    }
    const next = successor.get(row.pick_id);
    const repick =
      !next || next.status === 'rejected_late'
        ? null
        : {
            pick_id: next.pick_id,
            side: next.side,
            line_at_pick: next.line_at_pick,
            picked_at: next.picked_at,
          };
    const key = String(row.week);
    const list = grouped[key] ?? [];
    list.push({
      game_id: row.game_id,
      week: row.week,
      pick: {
        side: row.side,
        market: row.market,
        line_at_pick: row.line_at_pick,
        picked_at: row.picked_at,
      },
      voided_at: row.voided_at,
      repick,
    });
    grouped[key] = list;
  }
  return grouped;
}
