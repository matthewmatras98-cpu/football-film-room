import { formatSpreadTeam, formatTotalLine } from './format-stat';

export interface CardPickRef {
  pick_id: string;
  game_id: string;
  market: string;
  side: string;
  line_at_pick: number;
  status: string;
  result?: 'W' | 'L' | 'P' | null;
}

/** `{game}-{market}` is version 1. `-r2` through `-r9` are later versions. */
function pickVersion(row: Pick<CardPickRef, 'pick_id' | 'game_id' | 'market'>): number {
  const base = `${row.game_id}-${row.market}`;
  if (row.pick_id === base) {
    return 1;
  }
  const match = /^-r([2-9])$/.exec(row.pick_id.slice(base.length));
  return match ? Number(match[1]) : 0;
}

/**
 * One non-void pick per game and market. A later version replaces an earlier one.
 * A voided id is left out, so a card with only voids renders as unpicked.
 */
export function officialCardPicks<T extends CardPickRef>(rows: readonly T[]): T[] {
  const chosen = new Map<string, T>();
  for (const row of rows) {
    if (row.status === 'void') {
      continue;
    }
    const key = `${row.game_id}\0${row.market}`;
    const current = chosen.get(key);
    if (!current || pickVersion(row) >= pickVersion(current)) {
      chosen.set(key, row);
    }
  }
  return [...chosen.values()];
}

function totalSide(side: string, line: number): string {
  const label = side === 'over' ? 'Over' : side === 'under' ? 'Under' : side;
  return `${label} ${formatTotalLine(line)}`;
}

/** The line the pick was made at (`line_at_pick`), plus W, L, or Push when a grade exists. */
export function formatCardPick(row: CardPickRef): string {
  const line =
    row.market === 'total' ? totalSide(row.side, row.line_at_pick) : formatSpreadTeam(row.side, row.line_at_pick);
  const grade = row.result === 'W' ? 'W' : row.result === 'L' ? 'L' : row.result === 'P' ? 'Push' : null;
  return grade ? `Pick: ${line} \u00b7 ${grade}` : `Pick: ${line}`;
}
