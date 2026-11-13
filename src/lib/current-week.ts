export interface SeasonWeek {
  season: number;
  week: number;
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const TUESDAY = 2;

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

function kickoffMillis(row: SeasonWeek): number | null {
  const value = (row as { kickoff_utc?: unknown }).kickoff_utc;
  if (typeof value !== 'string' || value.length === 0) {
    return null;
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** First Tuesday 10:00 UTC strictly after the week's last kickoff. */
function rolloverMillis(lastKickoff: number): number {
  const date = new Date(lastKickoff);
  const daysUntilTuesday = (TUESDAY - date.getUTCDay() + 7) % 7;
  const candidate = Date.UTC(
    date.getUTCFullYear(),
    date.getUTCMonth(),
    date.getUTCDate() + daysUntilTuesday,
    10,
    0,
    0,
    0,
  );
  return candidate > lastKickoff ? candidate : candidate + WEEK_MS;
}

/**
 * Build clock. An injected Date wins. Otherwise BUILD_NOW, then the actual time.
 * An invalid override is ignored so the build still finishes.
 */
export function buildTime(now?: Date): Date {
  if (now instanceof Date && Number.isFinite(now.getTime())) {
    return now;
  }
  const override = process.env['BUILD_NOW'];
  if (typeof override === 'string' && override.length > 0) {
    const parsed = new Date(override);
    if (Number.isFinite(parsed.getTime())) {
      return parsed;
    }
  }
  return new Date();
}

interface WeekGroup {
  season: number;
  week: number;
  lastKickoff: number | null;
}

function weekGroups<T extends SeasonWeek>(rows: readonly T[]): WeekGroup[] {
  const groups = new Map<string, WeekGroup>();
  for (const row of rows) {
    const key = `${row.season}:${row.week}`;
    let group = groups.get(key);
    if (!group) {
      group = { season: row.season, week: row.week, lastKickoff: null };
      groups.set(key, group);
    }
    const kickoff = kickoffMillis(row);
    if (kickoff !== null && (group.lastKickoff === null || kickoff > group.lastKickoff)) {
      group.lastKickoff = kickoff;
    }
  }
  return [...groups.values()].sort(compareSeasonWeek);
}

/**
 * Earliest (season, week) whose Tuesday 10:00 UTC rollover is still ahead of the build clock.
 * Rollover is the first Tuesday 10:00 UTC strictly after that week's last valid kickoff.
 * Missing or invalid kickoffs are skipped. When every week has rolled, the latest week is used.
 */
export function currentWeek<T extends SeasonWeek>(rows: readonly T[], now?: Date): SeasonWeek | null {
  const groups = weekGroups(rows);
  if (groups.length === 0) {
    return null;
  }
  const at = buildTime(now).getTime();
  for (const group of groups) {
    if (group.lastKickoff === null) {
      continue;
    }
    if (at < rolloverMillis(group.lastKickoff)) {
      return { season: group.season, week: group.week };
    }
  }
  const latest = groups[groups.length - 1];
  return latest ? { season: latest.season, week: latest.week } : null;
}

/**
 * Every row in the current (season, week). Input order is kept.
 * Pass `now` in tests. Otherwise the clock is BUILD_NOW or new Date() at build time.
 */
export function currentWeekRows<T extends SeasonWeek>(rows: readonly T[], now?: Date): T[] {
  const current = currentWeek(rows, now);
  if (current === null) {
    return [];
  }
  return rows.filter((row) => compareSeasonWeek(row, current) === 0);
}
