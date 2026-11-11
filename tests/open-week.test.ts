import { describe, expect, it } from 'vitest';
import { selectableGames, selectOpenWeeks, type OpenWeekGame } from '../src/lib/admin/open-week';

const now = new Date('2026-11-11T18:00:00.000Z');
const day = 24 * 60 * 60 * 1000;

function game(id: string, week: number, kickoffMs: number, final: boolean): OpenWeekGame & { game_id: string } {
  return {
    game_id: id,
    season: 2026,
    week,
    kickoff_utc: new Date(kickoffMs).toISOString(),
    final,
  };
}

describe('open weeks', () => {
  it('mirrors intake: final and far weeks out, seven-day weeks in, lock hides kickoff-10min', () => {
    const past = game('2026_09_OLD_NEW', 9, now.getTime() - 3 * day, true);
    const next = game('2026_10_NXT_WK1', 10, now.getTime() + 2 * day, false);
    const also = game('2026_11_NXT_WK2', 11, now.getTime() + 6 * day, false);
    const far = game('2026_12_FAR_OUT', 12, now.getTime() + 8 * day, false);
    const edge = game('2026_13_EDG_DAY', 13, now.getTime() + 7 * day, false);
    expect(selectOpenWeeks([past, next, also, far, edge], now)).toEqual([
      { season: 2026, week: 10 },
      { season: 2026, week: 11 },
      { season: 2026, week: 13 },
    ]);

    const locked = game('2026_10_LOC_KED', 10, now.getTime() + 10 * 60 * 1000, false);
    const shown = game('2026_10_OPN_GAM', 10, now.getTime() + 11 * 60 * 1000, false);
    expect(selectableGames([past, far, locked, shown], now).map((row) => row.game_id)).toEqual(['2026_10_OPN_GAM']);
  });
});
