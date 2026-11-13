import { closeMarker, formatClv } from './format-picks';
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
 * One official pick per game and market. A later version replaces an earlier one.
 * Void and rejected-late rows are left out, so the card renders as unpicked.
 */
export function officialCardPicks<T extends CardPickRef>(rows: readonly T[]): T[] {
  const chosen = new Map<string, T>();
  for (const row of rows) {
    if (row.status === 'void' || row.status === 'rejected_late') {
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

function cardLine(market: string, side: string, line: number): string {
  return market === 'total' ? totalSide(side, line) : formatSpreadTeam(side, line);
}

/** The line the pick was made at (`line_at_pick`), plus W, L, or Push when a grade exists. */
export function formatCardPick(row: CardPickRef): string {
  const line = cardLine(row.market, row.side, row.line_at_pick);
  const grade = row.result === 'W' ? 'W' : row.result === 'L' ? 'L' : row.result === 'P' ? 'Push' : null;
  return grade ? `${line} \u00b7 ${grade}` : line;
}

export interface PickGradeRow extends CardPickRef {
  closing_line: number | null;
  clv_pts: number | null;
  close_source?: string | null;
}

/**
 * One muted line for a ledger card. Null grades are left out.
 * Void and rejected rows keep the original pick, because the badge no longer shows it.
 */
export function formatPickGrade(row: PickGradeRow): string {
  const parts: string[] = [];
  if (row.status === 'void' || row.status === 'rejected_late') {
    parts.push(`Pick ${cardLine(row.market, row.side, row.line_at_pick)}`);
  }
  if (row.closing_line !== null) {
    parts.push(`Closed ${cardLine(row.market, row.side, row.closing_line)}`);
    const marker = closeMarker(row.close_source ?? null);
    if (marker) {
      parts.push(marker.text);
    }
  }
  if (row.clv_pts !== null) {
    parts.push(`CLV ${formatClv(row.clv_pts)}`);
  }
  if (row.result === 'W' || row.result === 'L') {
    parts.push(`Result ${row.result}`);
  } else if (row.result === 'P') {
    parts.push('Result Push');
  }
  parts.push(`Status ${row.status}`);
  return parts.join(' \u00b7 ');
}
