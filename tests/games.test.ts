import { beforeEach, describe, expect, it, vi } from 'vitest';

const getCollection = vi.fn();

vi.mock('astro:content', () => ({
  getCollection: (...args: unknown[]) => getCollection(...args),
}));

const { currentWeekGames, gamesByWeek, gamesForWeek, getAllGames, getGame, picksForGame } = await import(
  '../src/lib/games'
);

describe('currentWeekGames', () => {
  const rows = [
    { season: 2099, week: 1, game_id: 'early' },
    { season: 2099, week: 2, game_id: 'current-a' },
    { season: 2099, week: 2, game_id: 'current-b' },
    { season: 2098, week: 18, game_id: 'older-season' },
  ];

  it('uses the latest season, then week, and does not fall back', () => {
    expect(currentWeekGames(rows).map((row) => row.game_id)).toEqual(['current-a', 'current-b']);
    expect(currentWeekGames(rows, 2).map((row) => row.game_id)).toEqual(['current-a', 'current-b']);
    expect(currentWeekGames(rows, 19)).toEqual([]);
    expect(currentWeekGames([])).toEqual([]);
    expect(gamesForWeek(rows, 3)).toEqual([]);
    expect(gamesByWeek(rows).map((group) => `${group.season}-${group.week}`)).toEqual([
      '2099-2',
      '2099-1',
      '2098-18',
    ]);
  });

  it('keeps every card in that week, with or without a pick', () => {
    const picks = [{ game_id: 'current-a', pick_id: 'current-a-spread' }];
    const week = currentWeekGames([
      { season: 2099, week: 2, game_id: 'current-a' },
      { season: 2099, week: 2, game_id: 'current-b' },
      { season: 2099, week: 1, game_id: 'early' },
    ]);
    expect(week.map((row) => row.game_id)).toEqual(['current-a', 'current-b']);
    expect(picksForGame('current-a', picks).map((row) => row.pick_id)).toEqual(['current-a-spread']);
    expect(picksForGame('current-b', picks)).toEqual([]);
    expect(picksForGame('early', picks)).toEqual([]);
  });

  it('lets week 1 of 2027 beat week 18 of 2026', () => {
    const rows = [
      { season: 2026, week: 18, game_id: '2026-w18-a' },
      { season: 2027, week: 1, game_id: '2027-w1-a' },
      { season: 2027, week: 1, game_id: '2027-w1-b' },
    ];
    expect(currentWeekGames(rows).map((row) => row.game_id)).toEqual(['2027-w1-a', '2027-w1-b']);
  });
});

describe('getAllGames', () => {
  beforeEach(() => {
    getCollection.mockReset();
  });

  it('returns an empty list when the collection has no cards', async () => {
    getCollection.mockResolvedValue([]);
    await expect(getAllGames()).resolves.toEqual([]);
    await expect(getGame('2099_02_NE_MIA')).resolves.toBeUndefined();
    expect(getCollection).toHaveBeenCalledWith('games');
  });

  it('sorts by season, week, and game id', async () => {
    getCollection.mockResolvedValue([
      { id: '2099_02_NE_MIA', data: { game_id: '2099_02_NE_MIA', season: 2099, week: 2 } },
      { id: '2099_01_KC_BUF', data: { game_id: '2099_01_KC_BUF', season: 2099, week: 1 } },
      { id: '2099_01_GB_CHI', data: { game_id: '2099_01_GB_CHI', season: 2099, week: 1 } },
    ]);
    const games = await getAllGames();
    expect(games.map((game) => game.game_id)).toEqual(['2099_01_GB_CHI', '2099_01_KC_BUF', '2099_02_NE_MIA']);
    await expect(getGame('2099_01_KC_BUF')).resolves.toMatchObject({ game_id: '2099_01_KC_BUF' });
  });

  it('fails when the file name does not match game_id', async () => {
    getCollection.mockResolvedValue([
      { id: 'malformed', data: { game_id: '2099_02_NE_MIA', season: 2099, week: 2 } },
    ]);
    await expect(getAllGames()).rejects.toThrow(/file name must match game_id/);
  });
});
