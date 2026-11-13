import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import PickCards from '../src/components/PickCards.astro';
import ProgramCard from '../src/components/ProgramCard.astro';
import Index from '../src/pages/index.astro';
import { siteUrl } from '../src/data/site-url.mjs';
import { formatPickGrade } from '../src/lib/card-pick';
import { gameCardSchema, type GameCard } from '../src/lib/game-schema';
import { picksForGame } from '../src/lib/games';
import { currentWeekPicks, picks } from '../src/lib/picks';
import type { Writeup } from '../src/lib/writeup';

const container = await AstroContainer.create({
  astroConfig: { site: siteUrl() },
});
const home = await container.renderToString(Index, {
  request: new Request(`${siteUrl()}/`),
});

function loadGame(id: string): GameCard {
  return gameCardSchema.parse(
    JSON.parse(readFileSync(new URL(`../src/content/games/${id}.json`, import.meta.url), 'utf8')),
  );
}

function duplicateIds(html: string): string[] {
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  const counts = new Map<string, number>();
  for (const id of ids) {
    counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  return [...counts].filter(([, count]) => count > 1).map(([id]) => id);
}

function homePicksSection(html: string): string {
  const start = html.indexOf('<div class="home-picks"');
  const markers = ['data-week-cards', '<aside'];
  const ends = markers
    .map((marker) => html.indexOf(marker, start + 1))
    .filter((at) => at > start);
  const end = ends.length === 0 ? html.length : Math.min(...ends);
  return html.slice(start, end);
}

const shown = currentWeekPicks(picks);
const pickedGame = loadGame('2026_10_NE_DET');
const quietGame = loadGame('2026_11_TB_DET');
const pickedRow = picks.find((row) => row.game_id === pickedGame.game_id);
const writeup: Writeup = {
  pick_id: pickedRow?.pick_id ?? '',
  game_id: pickedGame.game_id,
  submitted_at: pickedRow?.picked_at ?? '',
  body: 'Detroit has the better offense',
};

describe('home pick cards', () => {
  it('replaces the picks table with one card per shown pick', () => {
    expect(home).not.toMatch(/<table\b[^>]*\bclass="picks"/);
    const section = homePicksSection(home);
    expect(section.match(/<li\b[^>]*\bclass="game-card"/g)).toHaveLength(shown.length);
    for (const row of shown) {
      expect(home.match(new RegExp(`id="pick-${row.pick_id}"`, 'g'))).toHaveLength(1);
      const block = section.slice(section.indexOf(`id="pick-${row.pick_id}"`));
      expect(block.indexOf(formatPickGrade(row))).toBeGreaterThanOrEqual(0);
      expect(block.indexOf('class="pick-grade"')).toBeLessThan(block.indexOf('Submitted (server time)'));
    }
    expect(duplicateIds(home)).toEqual([]);
    expect(home).toContain('data-season-summary');
  });
});

describe('week cards', () => {
  it('uses the picks accordion for an official pick and stays quiet otherwise', async () => {
    expect(pickedRow).toBeTruthy();
    const row = pickedRow!;
    const ledger = await container.renderToString(PickCards, {
      props: { picks: [row], games: [pickedGame], writeups: [writeup] },
    });
    const picked = await container.renderToString(ProgramCard, {
      props: { card: pickedGame, picks: picksForGame(pickedGame.game_id, picks), writeups: [writeup] },
    });
    const quiet = await container.renderToString(ProgramCard, {
      props: { card: quietGame, picks: picksForGame(quietGame.game_id, picks), writeups: [] },
    });
    const weekSection = `<section data-week-cards><ul><li class="game-card" data-game-id="${pickedGame.game_id}">${picked}</li><li class="game-card" data-game-id="${quietGame.game_id}">${quiet}</li></ul></section>`;
    const page = `<div class="home-picks">${ledger}</div>${weekSection}`;

    expect(picked).not.toMatch(/id="pick-/);
    expect(picked).toContain('<details class="writeup-details">');
    expect(picked.indexOf(formatPickGrade(row))).toBeGreaterThanOrEqual(0);
    expect(picked.indexOf('class="pick-grade"')).toBeLessThan(picked.indexOf('Submitted (server time)'));
    expect(picked.indexOf('Submitted (server time)')).toBeLessThan(picked.indexOf('Detroit has the better offense'));
    expect(picked.match(/<details\b[\s\S]*?<\/details>/g)).toEqual(ledger.match(/<details\b[\s\S]*?<\/details>/g));

    expect(quiet).toContain('No official pick');
    expect(quiet).not.toContain('<details');
    expect(weekSection).not.toMatch(/id="pick-/);
    expect(page.match(new RegExp(`id="pick-${row.pick_id}"`, 'g'))).toHaveLength(1);
    expect(duplicateIds(page)).toEqual([]);
  });
});
