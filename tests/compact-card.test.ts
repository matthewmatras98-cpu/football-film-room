import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import ProgramCard from '../src/components/ProgramCard.astro';
import { gameCardSchema, type GameCard } from '../src/lib/game-schema';

const css = readFileSync(new URL('../src/styles/global.css', import.meta.url), 'utf8');
const card = gameCardSchema.parse(
  JSON.parse(readFileSync(new URL('./fixtures/games/2099_01_KC_BUF.json', import.meta.url), 'utf8')),
) as GameCard;

const phone = css.slice(css.indexOf('@media (max-width: 30rem)'));

describe('compact phone card', () => {
  it('keeps the two-line card out of the desktop layout', () => {
    const base = css.slice(0, css.indexOf('@media (max-width: 30rem)'));
    expect(base).toMatch(/\.card-compact\s*\{[^}]*display:\s*none/);
    expect(phone).toMatch(/\.card-compact\s*\{[^}]*display:\s*block/);
    expect(phone).toMatch(/min-height:\s*44px/);
    expect(phone).toContain('.game-card .game-title');
    expect(phone).toContain('.slate-card .game-story');
  });

  it('renders matchup and kickoff on one line and spread and total on the next', async () => {
    const container = await AstroContainer.create();
    const html = await container.renderToString(ProgramCard, {
      props: { card, picks: [] },
    });
    const compact = html.match(/<p class="card-compact">[\s\S]*?<\/p>/)?.[0] ?? '';
    expect(compact).toContain('class="card-compact-match"');
    expect(compact).toContain('KC @ BUF');
    expect(compact).toContain('class="card-compact-kick"');
    expect(compact).toContain('·');
    expect(compact).toContain('MST');
    expect(compact).toContain('class="card-compact-spread"');
    expect(compact).toContain('BUF -6.0');
    expect(compact).toContain('class="card-compact-total"');
    expect(compact).toContain('47.0');
    expect(html).toContain('class="game-title"');
    expect(html).toContain('No official pick');
    expect(html).toContain('Read matchup notes');
  });
});
