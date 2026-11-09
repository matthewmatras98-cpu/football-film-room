import { getCollection } from 'astro:content';
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

/** Groups by season and week, newest season and week first. Order inside a week is kept. */
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
  return [...groups.values()].sort((a, b) => b.season - a.season || b.week - a.week);
}

/** Cards for one week number. An unknown week is empty. This does not substitute an older week. */
export function gamesForWeek<T extends { week: number }>(rows: readonly T[], week: number): T[] {
  return rows.filter((row) => row.week === week);
}

/**
 * "This week" is the highest week number present, matching the picks page.
 * It is not the calendar week. Pass an explicit week to read that week only:
 * a week with zero cards returns an empty list and does not fall back.
 */
export function currentWeekGames<T extends { week: number }>(rows: readonly T[], week?: number): T[] {
  if (week === undefined) {
    if (rows.length === 0) {
      return [];
    }
    week = Math.max(...rows.map((row) => row.week));
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
