import { z } from 'astro/zod';
import { GAME_ID_PATTERN, isIsoWithOffset } from './schemas';

/** Statuses written by `grade.py`. */
export const PICK_STATUSES = ['graded', 'pending', 'ungraded', 'rejected_late', 'void'] as const;

/** Close sources written by `grade.py`. Null means neither books nor nflverse had a line. */
export const CLOSE_SOURCES = ['odds_api', 'odds_api_thin', 'odds_api_stale', 'nflverse'] as const;

export const MARKETS = ['spread', 'total'] as const;

export const RESULTS = ['W', 'L', 'P'] as const;

/** `{game_id}-{market}`, or the same id with `-r2` through `-r9`. */
export function isPickId(pickId: string, gameId: string, market: string): boolean {
  const base = `${gameId}-${market}`;
  if (pickId === base) {
    return true;
  }
  return pickId.startsWith(base) && /^-r[2-9]$/.test(pickId.slice(base.length));
}

const utcTimestamp = z
  .string()
  .refine((value) => value.endsWith('Z') && isIsoWithOffset(value), 'must be a UTC timestamp ending in Z');

const pickedAt = z
  .string()
  .refine(isIsoWithOffset, 'picked_at must be an ISO-8601 timestamp with an offset');

const nullableNumber = z.number().nullable();
const nullableInt = z.number().int().nullable();

export const summarySchema = z
  .object({
    season: z.number().int().nullable(),
    w: z.number().int(),
    l: z.number().int(),
    p: z.number().int(),
    win_pct: nullableNumber,
    units: z.number(),
    avg_clv: nullableNumber,
    pct_clv_pos: nullableNumber,
    rejected_count: z.number().int(),
    ungraded_count: z.number().int(),
    void_count: z.number().int(),
    generated_at: utcTimestamp,
  })
  .strict();

export const pickViewSchema = z
  .object({
    pick_id: z.string().min(1),
    game_id: z.string().regex(GAME_ID_PATTERN),
    season: z.number().int(),
    week: z.number().int(),
    market: z.enum(MARKETS),
    side: z.string().min(1),
    line_at_pick: z.number(),
    picked_at: pickedAt,
    units: z.number(),
    confidence: z.number().int().min(1).max(3).nullable(),
    tags: z.string(),
    note: z.string(),
    closing_line: nullableNumber,
    close_source: z.enum(CLOSE_SOURCES).nullable(),
    close_ts: utcTimestamp.nullable(),
    close_books: nullableInt,
    close_age_min: nullableNumber,
    final_home: nullableInt,
    final_away: nullableInt,
    result: z.enum(RESULTS).nullable(),
    units_pl: nullableNumber,
    clv_pts: nullableNumber,
    status: z.enum(PICK_STATUSES),
    first_pushed_at: utcTimestamp.nullable(),
    voided_at: utcTimestamp.nullable(),
  })
  .strict()
  .refine(
    (row) => isPickId(row.pick_id, row.game_id, row.market),
    'pick_id must be {game_id}-{market} or {game_id}-{market}-r2 through -r9',
  )
  .refine(
    (row) => row.market !== 'total' || row.side === 'over' || row.side === 'under',
    'total side must be over or under',
  );

export const picksViewSchema = z.array(pickViewSchema);

export type Summary = z.infer<typeof summarySchema>;
export type PickView = z.infer<typeof pickViewSchema>;
export type PickStatus = PickView['status'];
export type CloseSource = PickView['close_source'];
