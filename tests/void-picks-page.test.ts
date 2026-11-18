import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';
import type { PickView, Summary } from '../src/lib/picks-schema';

const voidAriWriteup = 'Arizona void write-up stays off the ledger.';
const voidGbWriteup = 'Green Bay void write-up stays off the ledger.';
const liveWriteup = 'Kansas City covers in the fixture.';

const shared = vi.hoisted(() => ({
  picks: [] as PickView[],
  summary: null as Summary | null,
}));

function loadFixtures(): { picks: PickView[]; summary: Summary } {
  const view = JSON.parse(readFileSync(new URL('./fixtures/picks_view.json', import.meta.url), 'utf8')) as PickView[];
  const summary = JSON.parse(readFileSync(new URL('./fixtures/summary.json', import.meta.url), 'utf8')) as Summary;
  const byId = (id: string) => {
    const row = view.find((item) => item.pick_id === id);
    if (!row) {
      throw new Error(`missing fixture pick ${id}`);
    }
    return row;
  };
  const greenBay = byId('2099_01_GB_CHI-spread');
  return {
    summary,
    picks: [
      byId('2099_01_ARI_LAR-spread'),
      { ...greenBay, status: 'void', voided_at: '2099-09-13T16:00:00Z' },
      byId('2099_01_KC_BUF-spread'),
      byId('2099_01_BAL_PIT-spread'),
    ],
  };
}

function fixtureGameEntries(): { id: string; data: { game_id: string } }[] {
  const dir = new URL('./fixtures/games/', import.meta.url);
  return readdirSync(dir)
    .filter((file) => file.endsWith('.json') && file !== 'malformed.json')
    .map((file) => {
      const data = JSON.parse(readFileSync(new URL(file, dir), 'utf8')) as { game_id: string };
      return { id: data.game_id, data };
    });
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
    if (name === 'games') {
      return fixtureGameEntries();
    }
    if (name === 'picks') {
      return [
        writeupEntry('2099_01_ARI_LAR-spread', '2099_01_ARI_LAR', voidAriWriteup),
        writeupEntry('2099_01_GB_CHI-spread', '2099_01_GB_CHI', voidGbWriteup),
        writeupEntry('2099_01_KC_BUF-spread', '2099_01_KC_BUF', liveWriteup),
      ];
    }
    return [];
  },
}));

vi.mock('../src/lib/picks', async () => {
  const loaded = loadFixtures();
  shared.picks = loaded.picks;
  shared.summary = loaded.summary;
  const actual = await vi.importActual<typeof import('../src/lib/picks')>('../src/lib/picks');
  return { ...actual, picks: shared.picks, summary: shared.summary };
});

const { experimental_AstroContainer: AstroContainer } = await import('astro/container');
const { default: PicksPage } = await import('../src/pages/picks.astro');
const { default: Index } = await import('../src/pages/index.astro');
const { default: GamesIndex } = await import('../src/pages/games/index.astro');
const { siteUrl } = await import('../src/data/site-url.mjs');
const { formatCardPick } = await import('../src/lib/card-pick');
const { formatUnits, formatWinPct } = await import('../src/lib/format-picks');

// Monday of fixture week 1, before the Tuesday 10:00Z rollover. Home and the
// slate read BUILD_NOW; the picks page does not.
const pinnedNow = new Date('2099-09-14T12:00:00Z');
const previousBuildNow = process.env.BUILD_NOW;
process.env.BUILD_NOW = pinnedNow.toISOString();

const container = await AstroContainer.create({
  astroConfig: { site: siteUrl() },
});
let picksPage: string;
let home: string;
let slate: string;
try {
  const request = new Request(`${siteUrl()}/`);
  picksPage = await container.renderToString(PicksPage, { request });
  home = await container.renderToString(Index, { request });
  slate = await container.renderToString(GamesIndex, { request });
} finally {
  if (previousBuildNow === undefined) {
    delete process.env.BUILD_NOW;
  } else {
    process.env.BUILD_NOW = previousBuildNow;
  }
}

const fixturePicks = shared.picks;
const fixtureSummary = shared.summary;
if (!fixtureSummary) {
  throw new Error('fixture summary was not loaded');
}
const voidAri = fixturePicks.find((row) => row.pick_id === '2099_01_ARI_LAR-spread');
const voidGb = fixturePicks.find((row) => row.pick_id === '2099_01_GB_CHI-spread');
const live = fixturePicks.find((row) => row.pick_id === '2099_01_KC_BUF-spread');
const rejected = fixturePicks.find((row) => row.pick_id === '2099_01_BAL_PIT-spread');

function gameItem(html: string, gameId: string): string {
  const marker = `data-game-id="${gameId}"`;
  const start = html.indexOf(marker);
  expect(start, marker).toBeGreaterThanOrEqual(0);
  const liStart = html.lastIndexOf('<li', start);
  const end = html.indexOf('</li>', start);
  return html.slice(liStart, end === -1 ? html.length : end);
}

function slateCard(html: string, gameId: string): string {
  const marker = `href="/games/${gameId}/"`;
  const start = html.indexOf(marker);
  expect(start, marker).toBeGreaterThanOrEqual(0);
  const articleStart = html.lastIndexOf('<article', start);
  const end = html.indexOf('</article>', start);
  return html.slice(articleStart, end === -1 ? html.length : end);
}

function pageFiles(dir: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...pageFiles(full));
    } else {
      found.push(full);
    }
  }
  return found;
}

describe('void picks on /picks/', () => {
  it('omits a void pick, its write-up, and any pick route', () => {
    expect(voidAri?.status).toBe('void');
    expect(voidGb?.status).toBe('void');
    for (const row of [voidAri, voidGb]) {
      const pickId = row?.pick_id ?? '';
      expect(picksPage).not.toContain(pickId);
      expect(picksPage).not.toContain(`id="pick-${pickId}"`);
      expect(picksPage).not.toContain(`#pick-${pickId}`);
      expect(picksPage).not.toContain(`/picks/${pickId}`);
    }
    expect(picksPage).not.toContain(voidAriWriteup);
    expect(picksPage).not.toContain(voidGbWriteup);
    expect(picksPage).not.toContain('>Void<');
    expect(picksPage).not.toContain('Status void');

    const pagesDir = fileURLToPath(new URL('../src/pages/', import.meta.url));
    const pickRoutes = pageFiles(pagesDir).filter(
      (file) => file.includes(`${path.sep}picks${path.sep}`) || path.basename(file) === 'picks.astro',
    );
    expect(pickRoutes.map((file) => path.basename(file))).toEqual(['picks.astro']);
    expect(pageFiles(pagesDir).some((file) => file.includes('[pick'))).toBe(false);
  });

  it('still renders a non-void pick', () => {
    expect(live?.status).not.toBe('void');
    expect(rejected?.status).toBe('rejected_late');
    expect(picksPage).toContain(`id="pick-${live?.pick_id}"`);
    expect(picksPage).toContain(liveWriteup);
    expect(picksPage).toContain(formatCardPick(live!));
    expect(picksPage).toContain('Rejected \u2013 late');
    expect(picksPage).toContain(`id="pick-${rejected?.pick_id}"`);
  });

  it('reads No official pick on the home and slate cards for a voided game', () => {
    for (const html of [gameItem(home, '2099_01_GB_CHI'), slateCard(slate, '2099_01_GB_CHI')]) {
      expect(html).toContain('No official pick');
      expect(html).not.toContain(voidGbWriteup);
      expect(html).not.toContain('<details');
      expect(html).not.toContain('>Void<');
      expect(html).not.toContain('pick-line');
    }
    const picked = gameItem(home, '2099_01_KC_BUF');
    expect(picked).toContain(formatCardPick(live!));
    expect(picked).toContain(liveWriteup);
  });

  it('keeps the summary record instead of recomputing it from the visible rows', () => {
    const record = `${fixtureSummary.w}-${fixtureSummary.l}-${fixtureSummary.p}`;
    expect(record).toBe('3-4-2');
    expect(picksPage).toContain(`<span class="stat-figure nums">${record}</span>`);
    expect(picksPage).toContain(formatUnits(fixtureSummary.units));
    expect(picksPage).toContain(formatWinPct(fixtureSummary));
    expect(picksPage).toContain(`data-w="${fixtureSummary.w}"`);
    expect(picksPage).toContain(`data-l="${fixtureSummary.l}"`);
    expect(picksPage).toContain(`data-p="${fixtureSummary.p}"`);
    expect(picksPage).toContain(`data-units="${fixtureSummary.units}"`);
    expect(home).toContain(`${record} \u00b7 ${formatUnits(fixtureSummary.units)}`);
  });
});
