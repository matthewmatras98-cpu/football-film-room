import { getCollection } from 'astro:content';
import { compareSeasonWeek, currentWeekRows } from './current-week';
import type { GameCard } from './game-schema';

export {
  SOURCE_UNAVAILABLE,
  formatHomeLine,
  formatPlayer,
  formatRest,
  formatStat,
  formatStatKind,
  formatText,
  formatTotalLine,
  statFootnote,
} from './format-stat';
export type { StatKind } from './format-stat';

export interface WeekGames<T> {
  season: number;
  week: number;
  games: T[];
}

export function compareGameCards(
  a: { kickoff_utc: string; game_id: string },
  b: { kickoff_utc: string; game_id: string },
): number {
  const ak = Date.parse(a.kickoff_utc);
  const bk = Date.parse(b.kickoff_utc);
  if (Number.isFinite(ak) && Number.isFinite(bk) && ak !== bk) {
    return ak - bk;
  }
  return a.game_id.localeCompare(b.game_id);
}

/** Kickoff ascending, then game id. Rows without both fields keep their order. */
export function sortGameCards<T>(rows: readonly T[]): T[] {
  const cards = rows as readonly { kickoff_utc?: unknown; game_id?: unknown }[];
  if (!cards.every((row) => typeof row.kickoff_utc === 'string' && typeof row.game_id === 'string')) {
    return [...rows];
  }
  return [...rows].sort((a, b) =>
    compareGameCards(
      a as { kickoff_utc: string; game_id: string },
      b as { kickoff_utc: string; game_id: string },
    ),
  );
}

/** Groups by season and week, latest season and week first. Cards in a week follow kickoff, then game id. */
export function gamesByWeek<T extends { season: number; week: number }>(
  rows: readonly T[],
): WeekGames<T>[] {
  const groups = new Map<string, WeekGames<T>>();
  for (const row of rows) {
    const key = `${row.season}-${row.week}`;
    const existing = groups.get(key);
    if (existing) {
      existing.games.push(row);
    } else {
      groups.set(key, { season: row.season, week: row.week, games: [row] });
    }
  }
  return [...groups.values()]
    .map((group) => ({ ...group, games: sortGameCards(group.games) }))
    .sort((a, b) => compareSeasonWeek(b, a));
}

/** Cards for one week number. An unknown week is empty. This does not substitute an older week. */
export function gamesForWeek<T extends { week: number }>(rows: readonly T[], week: number): T[] {
  return rows.filter((row) => row.week === week);
}

/**
 * "This week" is currentWeek: the earliest week still before its Tuesday rollover.
 * Pass an explicit week to read that week number only:
 * a week with zero cards returns an empty list and does not fall back.
 */
export function currentWeekGames<T extends { season: number; week: number }>(
  rows: readonly T[],
  week?: number,
  now?: Date,
): T[] {
  if (week === undefined) {
    return currentWeekRows(rows, now);
  }
  return gamesForWeek(rows, week);
}

export function picksForGame<T extends { game_id: string }>(gameId: string, rows: readonly T[]): T[] {
  return rows.filter((row) => row.game_id === gameId);
}

/** Content collection only. An empty collection is a valid empty list. */
export async function getAllGames(): Promise<GameCard[]> {
  const entries = await getCollection('games');
  for (const entry of entries) {
    if (entry.id !== entry.data.game_id) {
      throw new Error(
        `src/content/games/${entry.id}.json declares game_id "${entry.data.game_id}". The file name must match game_id.`,
      );
    }
  }
  return entries
    .map((entry) => entry.data)
    .sort((a, b) => a.season - b.season || a.week - b.week || a.game_id.localeCompare(b.game_id));
}

export async function getGame(gameId: string): Promise<GameCard | undefined> {
  const games = await getAllGames();
  return games.find((game) => game.game_id === gameId);
}
