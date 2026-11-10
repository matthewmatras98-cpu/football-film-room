import { describe, expect, it } from 'vitest';
import { compareSeasonWeek, currentSeasonWeek, currentWeekRows } from '../src/lib/current-week';
import { byWeek, currentWeekPicks } from '../src/lib/picks';

const crossSeason = [
  { season: 2026, week: 18, id: '2026-w18' },
  { season: 2027, week: 1, id: '2027-w1-a' },
  { season: 2026, week: 17, id: '2026-w17' },
  { season: 2027, week: 1, id: '2027-w1-b' },
];

describe('current week', () => {
  it('lets week 1 of 2027 beat week 18 of 2026', () => {
    expect(compareSeasonWeek({ season: 2027, week: 1 }, { season: 2026, week: 18 })).toBeGreaterThan(0);
    expect(currentSeasonWeek(crossSeason)).toEqual({ season: 2027, week: 1 });
    expect(currentWeekRows(crossSeason).map((row) => row.id)).toEqual(['2027-w1-a', '2027-w1-b']);
    expect(currentWeekPicks(crossSeason).map((row) => row.id)).toEqual(['2027-w1-a', '2027-w1-b']);
    expect(byWeek(crossSeason).map((group) => `${group.season}-${group.week}`)).toEqual([
      '2027-1',
      '2026-18',
      '2026-17',
    ]);
  });

  it('treats an equal season and week as a tie and keeps every row', () => {
    const tied = { season: 2027, week: 1 };
    expect(compareSeasonWeek(tied, { season: 2027, week: 1 })).toBe(0);
    expect(currentWeekRows(crossSeason).map((row) => row.id)).toEqual(['2027-w1-a', '2027-w1-b']);
    expect(currentWeekRows([])).toEqual([]);
    expect(currentSeasonWeek([])).toBeNull();
    expect(currentWeekPicks([])).toEqual([]);
  });

  it('uses the week number only after the season matches', () => {
    const rows = [
      { season: 2026, week: 1, id: 'early' },
      { season: 2026, week: 18, id: 'late' },
    ];
    expect(compareSeasonWeek(rows[1]!, rows[0]!)).toBeGreaterThan(0);
    expect(currentWeekRows(rows).map((row) => row.id)).toEqual(['late']);
    expect(byWeek(rows).map((group) => group.week)).toEqual([18, 1]);
  });
});
