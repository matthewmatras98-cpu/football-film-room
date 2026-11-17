import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import type { GameCard } from '../src/lib/game-schema';

const { fixturePicks } = vi.hoisted(() => ({
  fixturePicks: [
    {
      pick_id: '2099_01_KC_BUF-spread',
      game_id: '2099_01_KC_BUF',
      season: 2099,
      week: 1,
      market: 'spread' as const,
      side: 'BUF',
      line_at_pick: -6,
      picked_at: '2099-09-13T15:00:00Z',
      units: 1,
      confidence: 1,
      tags: '',
      note: '',
      closing_line: -6.5,
      close_source: 'odds_api' as const,
      close_ts: '2099-09-14T18:00:00Z',
      close_books: 9,
      close_age_min: 30,
      final_home: null,
      final_away: null,
      result: null,
      units_pl: null,
      clv_pts: null,
      status: 'pending' as const,
      first_pushed_at: '2099-09-13T15:00:17Z',
      voided_at: null,
    },
    {
      pick_id: '2099_02_NE_MIA-spread',
      game_id: '2099_02_NE_MIA',
      season: 2099,
      week: 2,
      market: 'spread' as const,
      side: 'MIA',
      line_at_pick: -3.5,
      picked_at: '2099-09-20T15:00:00Z',
      units: 1,
      confidence: 1,
      tags: '',
      note: '',
      closing_line: null,
      close_source: null,
      close_ts: null,
      close_books: null,
      close_age_min: null,
      final_home: null,
      final_away: null,
      result: null,
      units_pl: null,
      clv_pts: null,
      status: 'pending' as const,
      first_pushed_at: '2099-09-20T15:00:17Z',
      voided_at: null,
    },
  ],
}));

function fixtureGameEntries(): { id: string; data: { game_id: string } }[] {
  const dir = new URL('./fixtures/games/', import.meta.url);
  return readdirSync(dir)
    .filter((file) => file.endsWith('.json') && file !== 'malformed.json')
    .map((file) => {
      const data = JSON.parse(readFileSync(new URL(file, dir), 'utf8')) as { game_id: string };
      return { id: data.game_id, data };
    });
}

vi.mock('astro:content', () => ({
  getCollection: async (name: string) => {
    if (name === 'games') {
      return fixtureGameEntries();
    }
    if (name === 'picks') {
      return [
        {
          id: '2099_01_KC_BUF-spread',
          body: 'Bills cover.',
          data: {
            pick_id: '2099_01_KC_BUF-spread',
            game_id: '2099_01_KC_BUF',
            submitted_at: '2099-09-13T15:00:00Z',
          },
        },
      ];
    }
    return [];
  },
}));

vi.mock('../src/lib/picks', async () => {
  const actual = await vi.importActual<typeof import('../src/lib/picks')>('../src/lib/picks');
  return { ...actual, picks: fixturePicks };
});

const { experimental_AstroContainer: AstroContainer } = await import('astro/container');
const { default: Index } = await import('../src/pages/index.astro');
const { siteUrl } = await import('../src/data/site-url.mjs');
const { gameCardSchema } = await import('../src/lib/game-schema');
const { currentWeekGames } = await import('../src/lib/games');
const { officialCardPicks } = await import('../src/lib/card-pick');
const { currentWeekPicks, picks } = await import('../src/lib/picks');

// Monday of fixture week 1, before the Tuesday 10:00Z rollover. The page reads
// BUILD_NOW; the assertions pass this same instant into the week helpers.
const pinnedNow = new Date('2099-09-14T12:00:00Z');
const previousBuildNow = process.env.BUILD_NOW;
process.env.BUILD_NOW = pinnedNow.toISOString();

const container = await AstroContainer.create({
  astroConfig: { site: siteUrl() },
});
let home: string;
try {
  home = await container.renderToString(Index, {
    request: new Request(`${siteUrl()}/`),
  });
} finally {
  if (previousBuildNow === undefined) {
    delete process.env.BUILD_NOW;
  } else {
    process.env.BUILD_NOW = previousBuildNow;
  }
}

const indexSource = readFileSync(new URL('../src/pages/index.astro', import.meta.url), 'utf8');

function loadCards(): GameCard[] {
  return fixtureGameEntries()
    .map((entry) => gameCardSchema.parse(entry.data))
    .sort((a, b) => a.season - b.season || a.week - b.week || a.game_id.localeCompare(b.game_id));
}

function gameItem(html: string, gameId: string): string {
  const marker = `data-game-id="${gameId}"`;
  const start = html.indexOf(marker);
  expect(start, marker).toBeGreaterThanOrEqual(0);
  const liStart = html.lastIndexOf('<li', start);
  const end = html.indexOf('</li>', start);
  return html.slice(liStart, end === -1 ? html.length : end);
}

const cards = loadCards();
const thisWeek = currentWeekGames(cards, undefined, pinnedNow);
const weekPicks = currentWeekPicks(picks, pinnedNow, cards);
const pickedGameId = '2099_01_KC_BUF';

describe('home single list', () => {
  it('drops the picks block and lists each current-week game once', () => {
    expect(indexSource).not.toContain('PickCards');
    expect(indexSource).not.toMatch(/<h2>This week<\/h2>/);
    expect(home).not.toMatch(/<div\b[^>]*\bclass="[^"]*\bhome-picks\b/);
    expect(home).not.toMatch(/<h2>This week<\/h2>/);
    expect(home).not.toMatch(/id="pick-/);
    expect(home).not.toMatch(/#pick-/);
    expect(home).not.toMatch(/<table\b[^>]*\bclass="picks"/);
    expect(home).toContain("This week's cards");
    expect(home).toContain('Season 2099, week 1');
    expect(home).not.toContain('2026_');
    expect(home).toContain('data-season-summary');
    expect(home).toContain('class="home-record-line');
    expect(thisWeek.length).toBeGreaterThan(0);
    expect(thisWeek.every((card) => card.season === 2099 && card.week === 1)).toBe(true);
    expect(cards.some((card) => card.week === 2)).toBe(true);

    for (const card of cards) {
      const marker = `data-game-id="${card.game_id}"`;
      const count = home.split(marker).length - 1;
      const onThisWeek = thisWeek.some((weekCard) => weekCard.game_id === card.game_id);
      expect(count, marker).toBe(onThisWeek ? 1 : 0);
    }
  });

  it('keeps the badge and collapsed write-up on a picked week card', () => {
    expect(thisWeek.some((card) => card.game_id === pickedGameId)).toBe(true);
    expect(weekPicks.map((row) => row.game_id)).toEqual([pickedGameId]);
    const official = officialCardPicks(weekPicks.filter((row) => row.game_id === pickedGameId));
    expect(official).toHaveLength(1);
    const item = gameItem(home, pickedGameId);
    expect(item).toContain('<span class="badge badge--none pick-line">BUF -6.0</span>');
    expect(item).toContain('Read matchup notes');
    expect(item).toContain('<details class="writeup-details">');
    expect(item).not.toMatch(/<details[^>]*\bopen\b/);
    expect(item).toContain('Read the write-up');
    expect(item).toContain('Bills cover.');
    expect(item).not.toMatch(/id="pick-/);

    const quiet = thisWeek.find(
      (card) => officialCardPicks(picks.filter((row) => row.game_id === card.game_id)).length === 0,
    );
    expect(quiet).toBeTruthy();
    const unpicked = gameItem(home, quiet!.game_id);
    expect(unpicked).toContain('No official pick');
    expect(unpicked).not.toContain('pick-line');
    expect(unpicked).not.toContain('<details');
  });
});
