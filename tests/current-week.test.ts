import { afterEach, describe, expect, it } from 'vitest';
import { compareSeasonWeek, currentSeasonWeek, currentWeek, currentWeekRows } from '../src/lib/current-week';
import { currentWeekGames } from '../src/lib/games';
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

const slate = [
  { season: 2026, week: 10, id: 'ne-det', kickoff_utc: '2026-11-15T14:30:00Z' },
  { season: 2026, week: 10, id: 'mnf', kickoff_utc: '2026-11-17T01:15:00Z' },
  { season: 2026, week: 11, id: 'next-a', kickoff_utc: '2026-11-22T18:00:00Z' },
  { season: 2026, week: 11, id: 'next-b', kickoff_utc: '2026-11-23T01:20:00Z' },
];

describe('tuesday rollover', () => {
  const previous = process.env.BUILD_NOW;

  afterEach(() => {
    if (previous === undefined) {
      delete process.env.BUILD_NOW;
    } else {
      process.env.BUILD_NOW = previous;
    }
  });

  it('stays on week 10 while week 11 files are already present', () => {
    const now = new Date('2026-11-12T21:00:00Z');
    expect(currentWeek(slate, now)).toEqual({ season: 2026, week: 10 });
    expect(currentWeekRows(slate, now).map((row) => row.id)).toEqual(['ne-det', 'mnf']);
    expect(currentWeekPicks(slate, now).map((row) => row.id)).toEqual(['ne-det', 'mnf']);
  });

  it('keeps Monday night, including 23:00Z, on week 10', () => {
    expect(currentWeek(slate, new Date('2026-11-16T23:00:00Z'))).toEqual({ season: 2026, week: 10 });
  });

  it('flips at Tuesday 10:00Z and not one minute earlier', () => {
    expect(currentWeek(slate, new Date('2026-11-17T09:59:00Z'))).toEqual({ season: 2026, week: 10 });
    expect(currentWeek(slate, new Date('2026-11-17T10:00:00Z'))).toEqual({ season: 2026, week: 11 });
    expect(currentWeekRows(slate, new Date('2026-11-17T10:00:00Z')).map((row) => row.id)).toEqual([
      'next-a',
      'next-b',
    ]);
  });

  it('skips a missing or invalid kickoff and does not throw', () => {
    const rows = [
      { season: 2026, week: 10, id: 'ok', kickoff_utc: '2026-11-15T14:30:00Z' },
      { season: 2026, week: 10, id: 'missing' },
      { season: 2026, week: 10, id: 'blank', kickoff_utc: '' },
      { season: 2026, week: 10, id: 'bad', kickoff_utc: 'not-a-kickoff' },
      { season: 2026, week: 10, id: 'null-kick', kickoff_utc: null },
      { season: 2026, week: 9, id: 'undated' },
      { season: 2026, week: 11, id: 'next', kickoff_utc: '2026-11-22T18:00:00Z' },
    ];
    const now = new Date('2026-11-12T21:00:00Z');
    expect(() => currentWeek(rows, now)).not.toThrow();
    expect(currentWeek(rows, now)).toEqual({ season: 2026, week: 10 });
    expect(currentWeekRows(rows, now).map((row) => row.id)).toEqual(['ok', 'missing', 'blank', 'bad', 'null-kick']);
  });

  it('falls back to the latest week after every week has rolled', () => {
    const rows = [
      { season: 2026, week: 9, id: 'w9', kickoff_utc: '2026-11-08T18:00:00Z' },
      ...slate,
    ];
    const now = new Date('2026-11-24T10:00:00Z');
    expect(currentWeek(rows, now)).toEqual({ season: 2026, week: 11 });
    expect(currentWeekRows(rows, now).map((row) => row.id)).toEqual(['next-a', 'next-b']);
  });

  it('keeps cards and picks on week 10 while week 11 files exist', () => {
    const now = new Date('2026-11-12T21:00:00Z');
    const games = [
      { season: 2026, week: 10, game_id: '2026_10_NE_DET', kickoff_utc: '2026-11-15T14:30:00Z' },
      { season: 2026, week: 11, game_id: '2026_11_TB_DET', kickoff_utc: '2026-11-22T18:00:00Z' },
    ];
    const picks = [
      { season: 2026, week: 10, id: 'w10' },
      { season: 2026, week: 11, id: 'w11' },
    ];
    const cards = currentWeekGames(games, undefined, now);
    const chosen = currentWeekPicks(picks, now, games);
    const cardWeek = { season: cards[0]?.season, week: cards[0]?.week };
    const pickWeek = { season: chosen[0]?.season, week: chosen[0]?.week };
    expect(cardWeek).toEqual({ season: 2026, week: 10 });
    expect(pickWeek).toEqual(cardWeek);
    expect(cards.every((row) => row.season === cardWeek.season && row.week === cardWeek.week)).toBe(true);
    expect(chosen.every((row) => row.season === pickWeek.season && row.week === pickWeek.week)).toBe(true);
    expect(currentWeekPicks(games, now).map((row) => row.game_id)).toEqual(['2026_10_NE_DET']);
  });

  it('reads BUILD_NOW when the clock is not injected', () => {
    process.env.BUILD_NOW = '2026-11-12T21:00:00Z';
    expect(currentWeek(slate)).toEqual({ season: 2026, week: 10 });
    process.env.BUILD_NOW = '2026-11-17T10:00:00Z';
    expect(currentWeek(slate)).toEqual({ season: 2026, week: 11 });
    process.env.BUILD_NOW = 'not-a-date';
    expect(() => currentWeek(slate, new Date('2026-11-16T23:00:00Z'))).not.toThrow();
    expect(currentWeek(slate, new Date('2026-11-12T21:00:00Z'))).toEqual({ season: 2026, week: 10 });
  });
});
