export interface SeasonWeek {
  season: number;
  week: number;
}

/**
 * Season decides first, then week.
 * Equal pairs tie at 0. A later season wins even when its week number is lower.
 */
export function compareSeasonWeek(a: SeasonWeek, b: SeasonWeek): number {
  return a.season - b.season || a.week - b.week;
}

/** The latest (season, week), or null when there are no rows. */
export function currentSeasonWeek<T extends SeasonWeek>(rows: readonly T[]): SeasonWeek | null {
  let best: SeasonWeek | null = null;
  for (const row of rows) {
    if (best === null || compareSeasonWeek(row, best) > 0) {
      best = { season: row.season, week: row.week };
    }
  }
  return best;
}

/**
 * Every row in the latest (season, week). Input order is kept.
 * A tie on that pair keeps every matching row. Older pairs are left out.
 */
export function currentWeekRows<T extends SeasonWeek>(rows: readonly T[]): T[] {
  const current = currentSeasonWeek(rows);
  if (current === null) {
    return [];
  }
  return rows.filter((row) => compareSeasonWeek(row, current) === 0);
}
