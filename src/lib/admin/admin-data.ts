import { currentSeasonWeek } from '../current-week';
import { voidsByWeek, type SlateVoid } from './voids';

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
  /** Counted voids from `picks_view.json`, grouped by week. Absent on older snapshots. */
  voids?: Record<string, SlateVoid[]>;
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
    voids: voidsByWeek(picks.filter(isViewRow)),
  };
}

function isViewRow(pick: LedgerPickInput): pick is LedgerPickInput & {
  week: number;
  line_at_pick: number;
  picked_at: string;
  voided_at: string | null;
} {
  const row = pick as LedgerPickInput & {
    week?: unknown;
    line_at_pick?: unknown;
    picked_at?: unknown;
    voided_at?: unknown;
  };
  return (
    typeof row.week === 'number' &&
    typeof row.line_at_pick === 'number' &&
    typeof row.picked_at === 'string' &&
    (typeof row.voided_at === 'string' || row.voided_at === null)
  );
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
  const voids = parseVoids(record.voids);
  if (!voids) {
    return null;
  }
  return { open_week: openWeek, games, picks, voids };
}

function parseVoids(value: unknown): Record<string, SlateVoid[]> | null {
  if (value === undefined) {
    return {};
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  const grouped: Record<string, SlateVoid[]> = {};
  for (const [week, rows] of Object.entries(value)) {
    if (!Array.isArray(rows)) {
      return null;
    }
    const parsed: SlateVoid[] = [];
    for (const row of rows) {
      const entry = asVoid(row);
      if (!entry) {
        return null;
      }
      parsed.push(entry);
    }
    grouped[week] = parsed;
  }
  return grouped;
}

function asVoid(value: unknown): SlateVoid | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const row = value as Record<string, unknown>;
  const pick = asPickCells(row.pick);
  if (typeof row.game_id !== 'string' || typeof row.week !== 'number' || !pick) {
    return null;
  }
  if (row.voided_at !== null && typeof row.voided_at !== 'string') {
    return null;
  }
  if (row.repick !== null && !asRepick(row.repick)) {
    return null;
  }
  return {
    game_id: row.game_id,
    week: row.week,
    pick,
    voided_at: row.voided_at,
    repick: row.repick === null ? null : asRepick(row.repick),
  };
}

function asPickCells(value: unknown): SlateVoid['pick'] | null {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const row = value as Record<string, unknown>;
  if (
    typeof row.side !== 'string' ||
    typeof row.market !== 'string' ||
    typeof row.line_at_pick !== 'number' ||
    typeof row.picked_at !== 'string'
  ) {
    return null;
  }
  return {
    side: row.side,
    market: row.market,
    line_at_pick: row.line_at_pick,
    picked_at: row.picked_at,
  };
}

function asRepick(value: unknown): SlateVoid['repick'] {
  if (!value || typeof value !== 'object') {
    return null;
  }
  const row = value as Record<string, unknown>;
  if (
    typeof row.pick_id !== 'string' ||
    typeof row.side !== 'string' ||
    typeof row.line_at_pick !== 'number' ||
    typeof row.picked_at !== 'string'
  ) {
    return null;
  }
  return {
    pick_id: row.pick_id,
    side: row.side,
    line_at_pick: row.line_at_pick,
    picked_at: row.picked_at,
  };
}
