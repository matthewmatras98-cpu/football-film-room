import { currentSeasonWeek } from '../current-week';

export interface AdminGame {
  game_id: string;
  season: number;
  week: number;
  away: string;
  home: string;
  kickoff_utc: string;
  home_spread: number | null;
  away_spread: number | null;
  total: number | null;
  /** True when this game has a final score. Cards do not carry scores, so the bake leaves this false. */
  final: boolean;
}

export interface AdminPick {
  pick_id: string;
  game_id: string;
  market: string;
  side: string;
  status: string;
  kickoff_utc: string | null;
}

export interface AdminData {
  open_week: { season: number; week: number } | null;
  games: AdminGame[];
  picks: AdminPick[];
}

export interface ScheduleGameInput {
  game_id: string;
  season: number;
  week: number;
  away: string;
  home: string;
  kickoff_utc: string;
  line_latest: {
    home_spread: number | null;
    away_spread?: number | null;
    total: number | null;
  } | null;
}

export interface LedgerPickInput {
  pick_id: string;
  game_id: string;
  market: string;
  side: string;
  status: string;
}

function nullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** Schedule and ledger snapshot. Request handlers choose open weeks from `games`. */
export function buildAdminData(games: readonly ScheduleGameInput[], picks: readonly LedgerPickInput[]): AdminData {
  const openWeek = currentSeasonWeek(games);
  const kickoffs = new Map(games.map((game) => [game.game_id, game.kickoff_utc]));
  return {
    open_week: openWeek,
    games: games.map((game) => ({
      game_id: game.game_id,
      season: game.season,
      week: game.week,
      away: game.away,
      home: game.home,
      kickoff_utc: game.kickoff_utc,
      home_spread: game.line_latest?.home_spread ?? null,
      away_spread: game.line_latest?.away_spread ?? null,
      total: game.line_latest?.total ?? null,
      final: false,
    })),
    picks: picks.map((pick) => ({
      pick_id: pick.pick_id,
      game_id: pick.game_id,
      market: pick.market,
      side: pick.side,
      status: pick.status,
      kickoff_utc: kickoffs.get(pick.game_id) ?? null,
    })),
  };
}

function asGame(value: unknown): AdminGame | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const row = value as Record<string, unknown>;
  if (
    typeof row.game_id !== 'string' ||
    typeof row.season !== 'number' ||
    typeof row.week !== 'number' ||
    typeof row.away !== 'string' ||
    typeof row.home !== 'string' ||
    typeof row.kickoff_utc !== 'string'
  ) {
    return null;
  }
  return {
    game_id: row.game_id,
    season: row.season,
    week: row.week,
    away: row.away,
    home: row.home,
    kickoff_utc: row.kickoff_utc,
    home_spread: nullableNumber(row.home_spread),
    away_spread: nullableNumber(row.away_spread),
    total: nullableNumber(row.total),
    final: row.final === true,
  };
}

function asPick(value: unknown): AdminPick | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const row = value as Record<string, unknown>;
  if (
    typeof row.pick_id !== 'string' ||
    typeof row.game_id !== 'string' ||
    typeof row.market !== 'string' ||
    typeof row.side !== 'string' ||
    typeof row.status !== 'string'
  ) {
    return null;
  }
  return {
    pick_id: row.pick_id,
    game_id: row.game_id,
    market: row.market,
    side: row.side,
    status: row.status,
    kickoff_utc: typeof row.kickoff_utc === 'string' ? row.kickoff_utc : null,
  };
}

/** Built JSON from dist. A bad file is null so the caller can answer without throwing. */
export function parseAdminData(value: unknown): AdminData | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (!Array.isArray(record.games) || !Array.isArray(record.picks)) {
    return null;
  }
  const games: AdminGame[] = [];
  for (const game of record.games) {
    const parsed = asGame(game);
    if (!parsed) {
      return null;
    }
    games.push(parsed);
  }
  const picks: AdminPick[] = [];
  for (const pick of record.picks) {
    const parsed = asPick(pick);
    if (!parsed) {
      return null;
    }
    picks.push(parsed);
  }
  let openWeek: AdminData['open_week'] = null;
  if (record.open_week && typeof record.open_week === 'object') {
    const week = record.open_week as Record<string, unknown>;
    if (typeof week.season === 'number' && typeof week.week === 'number') {
      openWeek = { season: week.season, week: week.week };
    }
  }
  return { open_week: openWeek, games, picks };
}
