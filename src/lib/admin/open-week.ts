import { isBeforeLock } from './lock';

/** Intake's window: first kickoff at or before now plus seven days. */
export const OPEN_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;

export interface OpenWeekGame {
  season: number;
  week: number;
  kickoff_utc: string;
  /** True when the game has a final score. Mirrors `Game.has_final_score`. */
  final: boolean;
}

export interface SeasonWeek {
  season: number;
  week: number;
}

function weekKey(week: SeasonWeek): string {
  return `${week.season}:${week.week}`;
}

/**
 * Weeks intake will accept. A week is open when it is not entirely final and
 * its earliest kickoff is within seven days of `now` (including earlier).
 * More than one week can be open. The latest fully final week is not added.
 */
export function selectOpenWeeks(games: readonly OpenWeekGame[], now: Date): SeasonWeek[] {
  const groups = new Map<string, OpenWeekGame[]>();
  for (const game of games) {
    const key = weekKey(game);
    const group = groups.get(key);
    if (group) {
      group.push(game);
    } else {
      groups.set(key, [game]);
    }
  }
  const horizon = now.getTime() + OPEN_WINDOW_MS;
  const open: SeasonWeek[] = [];
  for (const group of groups.values()) {
    if (group.length === 0 || group.every((game) => game.final)) {
      continue;
    }
    const kickoffs: number[] = [];
    for (const game of group) {
      const kickoff = Date.parse(game.kickoff_utc);
      if (Number.isFinite(kickoff)) {
        kickoffs.push(kickoff);
      }
    }
    if (kickoffs.length === 0) {
      continue;
    }
    const first = kickoffs.reduce((earliest, kickoff) => (kickoff < earliest ? kickoff : earliest));
    const sample = group[0];
    if (sample && first <= horizon) {
      open.push({ season: sample.season, week: sample.week });
    }
  }
  return open;
}

/** Open-week games that are still strictly before the 10-minute lock. */
export function selectableGames<T extends OpenWeekGame>(games: readonly T[], now: Date): T[] {
  const open = new Set(selectOpenWeeks(games, now).map(weekKey));
  return games.filter((game) => open.has(weekKey(game)) && isBeforeLock(now.getTime(), game.kickoff_utc));
}
