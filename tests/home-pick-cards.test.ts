import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import type { GameCard } from '../src/lib/game-schema';

vi.mock('astro:content', async () => {
  const { readdirSync, readFileSync } = await import('node:fs');
  const { basename } = await import('node:path');
  return {
    getCollection: async (name: string) => {
      if (name === 'games') {
        const dir = new URL('../src/content/games/', import.meta.url);
        return readdirSync(dir)
          .filter((file) => file.endsWith('.json'))
          .map((file) => {
            const data = JSON.parse(readFileSync(new URL(file, dir), 'utf8')) as { game_id: string };
            return { id: data.game_id, data };
          });
      }
      if (name === 'picks') {
        const dir = new URL('../src/content/picks/', import.meta.url);
        return readdirSync(dir)
          .filter((file) => file.endsWith('.md'))
          .map((file) => {
            const raw = readFileSync(new URL(file, dir), 'utf8');
            const body = raw.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '');
            const field = (key: string) => new RegExp(`^${key}:\\s*"([^"]+)"`, 'm').exec(raw)?.[1] ?? '';
            const id = basename(file, '.md');
            return {
              id,
              body,
              data: {
                pick_id: field('pick_id') || id,
                game_id: field('game_id'),
                submitted_at: field('submitted_at'),
              },
            };
          });
      }
      return [];
    },
  };
});

const { experimental_AstroContainer: AstroContainer } = await import('astro/container');
const { default: Index } = await import('../src/pages/index.astro');
const { siteUrl } = await import('../src/data/site-url.mjs');
const { currentWeek } = await import('../src/lib/current-week');
const { gameCardSchema } = await import('../src/lib/game-schema');
const { officialCardPicks } = await import('../src/lib/card-pick');
const { picks } = await import('../src/lib/picks');

const container = await AstroContainer.create({
  astroConfig: { site: siteUrl() },
});
const home = await container.renderToString(Index, {
  request: new Request(`${siteUrl()}/`),
});
const indexSource = readFileSync(new URL('../src/pages/index.astro', import.meta.url), 'utf8');

function loadCards(): GameCard[] {
  const dir = new URL('../src/content/games/', import.meta.url);
  return readdirSync(dir)
    .filter((name) => name.endsWith('.json'))
    .map((name) =>
      gameCardSchema.parse(JSON.parse(readFileSync(new URL(name, dir), 'utf8'))),
    );
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
const current = currentWeek(cards);
const thisWeek = cards.filter(
  (card) => current !== null && card.season === current.season && card.week === current.week,
);
const pickedGameId = '2026_10_NE_DET';

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
    expect(home).toContain('data-season-summary');
    expect(home).toContain('class="home-record-line');
    expect(thisWeek.length).toBeGreaterThan(0);

    for (const card of cards) {
      const marker = `data-game-id="${card.game_id}"`;
      const count = home.split(marker).length - 1;
      const onThisWeek = thisWeek.some((weekCard) => weekCard.game_id === card.game_id);
      expect(count, marker).toBe(onThisWeek ? 1 : 0);
    }
  });

  it('keeps the badge and collapsed write-up on a picked week card', () => {
    expect(thisWeek.some((card) => card.game_id === pickedGameId)).toBe(true);
    const official = officialCardPicks(picks.filter((row) => row.game_id === pickedGameId));
    expect(official).toHaveLength(1);
    const item = gameItem(home, pickedGameId);
    expect(item).toContain('<span class="badge badge--none pick-line">DET -3.0</span>');
    expect(item).toContain('Read matchup notes');
    expect(item).toContain('<details class="writeup-details">');
    expect(item).not.toMatch(/<details[^>]*\bopen\b/);
    expect(item).toContain('Read the write-up');
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
