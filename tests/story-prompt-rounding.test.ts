import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import ProgramCard from '../src/components/ProgramCard.astro';
import { siteUrl } from '../src/data/site-url.mjs';
import { gameCardSchema, type GameCard } from '../src/lib/game-schema';

const { default: GamePage } = await import('../src/pages/games/[game_id].astro');

const cards = readdirSync(new URL('./fixtures/generated-cards/', import.meta.url))
  .filter((name) => name.endsWith('.json'))
  .map((name) =>
    gameCardSchema.parse(JSON.parse(readFileSync(new URL(`./fixtures/generated-cards/${name}`, import.meta.url), 'utf8'))),
  ) as GameCard[];

const container = await AstroContainer.create({ astroConfig: { site: siteUrl() } });

function longDecimals(html: string): string[] {
  const sheet = html.indexOf('card-sheet');
  const body = sheet === -1 ? html : html.slice(sheet);
  const text = body.replace(/<[^>]+>/g, ' ');
  return text.match(/\d+\.\d{3,}/g) ?? [];
}

describe('story prompt display rounding', () => {
  it('renders generated cards with no number past two decimals', async () => {
    expect(cards.length).toBeGreaterThan(0);
    for (const card of cards) {
      const home = await container.renderToString(ProgramCard, {
        props: { card, picks: [], writeups: [] },
      });
      const page = await container.renderToString(GamePage, {
        props: { game: card },
        request: new Request(`${siteUrl()}/games/${card.game_id}/`),
      });
      expect(longDecimals(home), card.game_id).toEqual([]);
      expect(longDecimals(page), card.game_id).toEqual([]);
      expect(page).toContain('Story prompts');
    }
  });
});
