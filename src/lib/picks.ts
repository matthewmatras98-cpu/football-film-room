import { matchupLabel } from './format-picks';
import { picksViewSchema, summarySchema, type PickView, type Summary } from './picks-schema';

export {
  CLOSE_MARKER,
  EM_DASH,
  STATUS_LABEL,
  closeMarker,
  formatClv,
  formatFinal,
  formatMst,
  formatPct,
  formatPickLine,
  formatSideLine,
  formatSignedLine,
  formatUnits,
  formatWinPct,
  matchupLabel,
} from './format-picks';

const summaryModules = import.meta.glob('../data/summary.json', {
  eager: true,
  import: 'default',
}) as Record<string, unknown>;

const viewModules = import.meta.glob('../data/picks_view.json', {
  eager: true,
  import: 'default',
}) as Record<string, unknown>;

function sole(modules: Record<string, unknown>, label: string): unknown | null {
  const values = Object.values(modules);
  if (values.length === 0) {
    return null;
  }
  if (values.length > 1) {
    throw new Error(`${label}: expected one file, found ${values.length}`);
  }
  return values[0];
}

function issuesOf(error: { message?: string }): string {
  return error.message && error.message.length > 0 ? error.message : 'invalid';
}

/** Missing file → null. Present file → validated summary, or the build throws. */
export function loadSummary(modules: Record<string, unknown> = summaryModules): Summary | null {
  const data = sole(modules, 'summary.json');
  if (data === null) {
    return null;
  }
  const parsed = summarySchema.safeParse(data);
  if (!parsed.success) {
    throw new Error(`summary.json does not match the grader contract:\n${issuesOf(parsed.error)}`);
  }
  return parsed.data;
}

/** Missing file → []. Present file → validated rows, or the build throws. */
export function loadPicks(modules: Record<string, unknown> = viewModules): PickView[] {
  const data = sole(modules, 'picks_view.json');
  if (data === null) {
    return [];
  }
  const parsed = picksViewSchema.safeParse(data);
  if (!parsed.success) {
    throw new Error(`picks_view.json does not match the grader contract:\n${issuesOf(parsed.error)}`);
  }
  return parsed.data;
}

export const summary = loadSummary();
export const picks = loadPicks();

export interface WeekGroup<T> {
  season: number;
  week: number;
  picks: T[];
}

/** Groups by season and week, newest season and week first. Order inside a week is kept. */
export function byWeek<T extends { season: number; week: number }>(rows: readonly T[]): WeekGroup<T>[] {
  const groups = new Map<string, WeekGroup<T>>();
  for (const row of rows) {
    const key = `${row.season}-${row.week}`;
    const existing = groups.get(key);
    if (existing) {
      existing.picks.push(row);
    } else {
      groups.set(key, { season: row.season, week: row.week, picks: [row] });
    }
  }
  return [...groups.values()].sort((a, b) => b.season - a.season || b.week - a.week);
}

/** Rows for one week. An unknown week is empty. This does not substitute an older week. */
export function picksForWeek<T extends { week: number }>(rows: readonly T[], week: number): T[] {
  return rows.filter((row) => row.week === week);
}

/**
 * "This week" is the highest week number present. It is not the calendar week.
 * If that week has no rows, the result is empty. Older weeks are not used.
 */
export function currentWeekPicks<T extends { week: number }>(rows: readonly T[]): T[] {
  if (rows.length === 0) {
    return [];
  }
  const week = Math.max(...rows.map((row) => row.week));
  return picksForWeek(rows, week);
}

export interface WeekPickEntry {
  href: string | null;
  label: string;
}

/**
 * One entry per pick that shares a post's game id.
 * An id with no row stays plain text (`href` null) so the build still succeeds.
 */
export function weekPickEntries<T extends { game_id: string; pick_id: string; market: string }>(
  gameIds: readonly string[],
  rows: readonly T[],
): WeekPickEntry[] {
  const entries: WeekPickEntry[] = [];
  for (const gameId of gameIds) {
    const matches = rows.filter((row) => row.game_id === gameId);
    if (matches.length === 0) {
      entries.push({ href: null, label: gameId });
      continue;
    }
    for (const row of matches) {
      entries.push({
        href: `/picks/#pick-${row.pick_id}`,
        label: `${matchupLabel(row.game_id)} ${row.market}`,
      });
    }
  }
  return entries;
}
