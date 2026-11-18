import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import type { GameCard } from '../src/lib/game-schema';
import type { PickView } from '../src/lib/picks-schema';

const voidWriteup = 'Arizona void write-up stays off the matchup page.';
const extraVoidWriteup = 'Kansas City void write-up stays off the matchup page.';
const liveWriteup = 'Kansas City covers in the fixture.';

const { fixturePicks } = vi.hoisted(() => ({ fixturePicks: [] as PickView[] }));

function loadFixtures(): PickView[] {
  const view = JSON.parse(readFileSync(new URL('./fixtures/picks_view.json', import.meta.url), 'utf8')) as PickView[];
  const byId = (id: string) => {
    const row = view.find((item) => item.pick_id === id);
    if (!row) {
      throw new Error(`missing fixture pick ${id}`);
    }
    return row;
  };
  const voidOnly = byId('2099_01_ARI_LAR-spread');
  const rejected = byId('2099_01_BAL_PIT-spread');
  return [
    { ...voidOnly, game_id: '2099_01_GB_CHI' },
    {
      ...voidOnly,
      pick_id: '2099_01_KC_BUF-total',
      game_id: '2099_01_KC_BUF',
      market: 'total',
      side: 'under',
      line_at_pick: 47,
    },
    byId('2099_01_KC_BUF-spread'),
    { ...rejected, game_id: '2099_02_NE_MIA' },
  ];
}

function loadGame(name: string): GameCard {
  return JSON.parse(readFileSync(new URL(`./fixtures/games/${name}`, import.meta.url), 'utf8')) as GameCard;
}

function writeupEntry(pickId: string, gameId: string, body: string) {
  return {
    id: pickId,
    body,
    data: {
      pick_id: pickId,
      game_id: gameId,
      submitted_at: '2099-09-13T15:00:00Z',
    },
  };
}

vi.mock('astro:content', () => ({
  getCollection: async (name: string) => {
    if (name === 'picks') {
      return [
        writeupEntry('2099_01_ARI_LAR-spread', '2099_01_GB_CHI', voidWriteup),
        writeupEntry('2099_01_KC_BUF-total', '2099_01_KC_BUF', extraVoidWriteup),
        writeupEntry('2099_01_KC_BUF-spread', '2099_01_KC_BUF', liveWriteup),
      ];
    }
    return [];
  },
}));

vi.mock('../src/lib/picks', async () => {
  fixturePicks.splice(0, fixturePicks.length, ...loadFixtures());
  const actual = await vi.importActual<typeof import('../src/lib/picks')>('../src/lib/picks');
  return { ...actual, picks: fixturePicks };
});

const { experimental_AstroContainer: AstroContainer } = await import('astro/container');
const { default: GamePage } = await import('../src/pages/games/[game_id].astro');
const { siteUrl } = await import('../src/data/site-url.mjs');
const { gameCardSchema } = await import('../src/lib/game-schema');

const greenBay = gameCardSchema.parse(loadGame('2099_01_GB_CHI.json'));
const kansasCity = gameCardSchema.parse(loadGame('2099_01_KC_BUF.json'));
const miami = gameCardSchema.parse(loadGame('2099_02_NE_MIA.json'));

// Monday of fixture week 1, before the Tuesday 10:00Z rollover.
const pinnedNow = new Date('2099-09-14T12:00:00Z');
const previousBuildNow = process.env.BUILD_NOW;
process.env.BUILD_NOW = pinnedNow.toISOString();

const container = await AstroContainer.create({
  astroConfig: { site: siteUrl() },
});

async function renderGame(game: GameCard): Promise<string> {
  return container.renderToString(GamePage, {
    props: { game },
    request: new Request(`${siteUrl()}/games/${game.game_id}/`),
  });
}

let voidOnlyPage: string;
let mixedPage: string;
let rejectedPage: string;
try {
  voidOnlyPage = await renderGame(greenBay);
  mixedPage = await renderGame(kansasCity);
  rejectedPage = await renderGame(miami);
} finally {
  if (previousBuildNow === undefined) {
    delete process.env.BUILD_NOW;
  } else {
    process.env.BUILD_NOW = previousBuildNow;
  }
}

describe('void picks on a matchup page', () => {
  it('omits a void-only game, its note, and its id, and skips the pick table', () => {
    expect(voidOnlyPage).toContain('<h1>GB @ CHI</h1>');
    expect(voidOnlyPage).not.toContain('2099_01_ARI_LAR-spread');
    expect(voidOnlyPage).not.toContain('void marker');
    expect(voidOnlyPage).not.toContain(voidWriteup);
    expect(voidOnlyPage).not.toContain('id="pick-');
    expect(voidOnlyPage).not.toContain('<h2>Official pick</h2>');
    expect(voidOnlyPage).not.toMatch(/<table\b[^>]*\bclass="picks"/);
    expect(voidOnlyPage).not.toContain('<th scope="col">Week</th>');
  });

  it('keeps the live pick when the same game also has a void', () => {
    expect(mixedPage).toContain('id="pick-2099_01_KC_BUF-spread"');
    expect(mixedPage).toContain('KC lay');
    expect(mixedPage).toContain(liveWriteup);
    expect(mixedPage).toContain('<h2>Official pick</h2>');
    expect(mixedPage).not.toContain('2099_01_KC_BUF-total');
    expect(mixedPage).not.toContain('void marker');
    expect(mixedPage).not.toContain(extraVoidWriteup);
    expect(mixedPage).not.toContain('id="pick-2099_01_KC_BUF-total"');
  });

  it('still shows a rejected-late row', () => {
    expect(rejectedPage).toContain('id="pick-2099_01_BAL_PIT-spread"');
    expect(rejectedPage).toContain('exactly kickoff');
    expect(rejectedPage).toContain('Rejected \u2013 late');
    expect(rejectedPage).toContain('<h2>Official pick</h2>');
  });
});
