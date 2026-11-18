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
    const title = phoneRule('.game-card .game-title');
    expect(title).toMatch(/position:\s*absolute/);
    expect(title).toMatch(/clip:\s*rect\(0,\s*0,\s*0,\s*0\)/);
    expect(title).not.toMatch(/display:\s*none/);
    expect(phoneRule('.slate-card .game-title')).toBe(title);
  });

  it('does not size every anchor in the phone block', () => {
    for (const rule of phoneRules(phone)) {
      const setsBox = /min-height\s*:|display\s*:/.test(rule.body);
      if (!setsBox) {
        continue;
      }
      for (const selector of rule.selectors) {
        expect(selector, rule.body).not.toMatch(/^(?:a|a[:[][^\s,+>~]*)(?:\s*,|$)/);
        expect(isElementOnlyAnchor(selector), selector).toBe(false);
      }
    }
  });

  it('keeps the phone nav and the pick footer on one row', () => {
    const base = css.slice(0, css.indexOf('@media (max-width: 30rem)'));
    expect(base).toMatch(/\.site-nav\s*\{[^}]*flex-wrap:\s*wrap/);
    expect(phoneRule('.site-nav')).toMatch(/flex-wrap:\s*nowrap/);
    expect(phoneRule('.game-card-foot')).toMatch(/flex-wrap:\s*nowrap/);
    const pick = phoneRule('.game-card-foot .program-pick');
    expect(pick).toMatch(/flex-wrap:\s*nowrap/);
    expect(pick).toMatch(/white-space:\s*nowrap/);
    expect(pick).not.toMatch(/display:\s*none/);
    expect(phoneRule('.brand')).toMatch(/padding:\s*var\(--space-8\)/);
    expect(phoneRule('.brand-dek')).toMatch(/font-size:\s*var\(--text-caption\)/);
    expect(phoneRule('.game-card .game-title')).toMatch(/clip:\s*rect\(0,\s*0,\s*0,\s*0\)/);
    for (const selector of [
      '.game-card-link',
      '.game-card-foot a',
      '.section-link',
      '.chip',
      '.btn',
      '.wordmark',
      '.ledger-chip',
      '.writeup-details > summary',
    ]) {
      expect(phoneRule(selector), selector).toMatch(/min-height:\s*44px/);
    }
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
    expect(html).toMatch(/<h3 class="game-title">/);
    expect(html).toContain('No official pick');
    expect(html).toContain('Read matchup notes');
  });
});

function phoneRule(selector: string): string {
  return phoneRules(phone).find((rule) => rule.selectors.includes(selector))?.body ?? '';
}

function phoneRules(block: string): { selectors: string[]; body: string }[] {
  const open = block.indexOf('{');
  const inner = open === -1 ? block : block.slice(open + 1);
  const rules: { selectors: string[]; body: string }[] = [];
  let cursor = 0;
  while (cursor < inner.length) {
    const brace = inner.indexOf('{', cursor);
    if (brace === -1) {
      break;
    }
    const selectors = inner
      .slice(cursor, brace)
      .split(',')
      .map((selector) => selector.trim())
      .filter((selector) => selector.length > 0 && !selector.startsWith('@'));
    const end = inner.indexOf('}', brace);
    if (end === -1) {
      break;
    }
    rules.push({ selectors, body: inner.slice(brace + 1, end) });
    cursor = end + 1;
  }
  return rules;
}

/** A selector whose subject is the `a` element, with no class, id, or attribute. */
function isElementOnlyAnchor(selector: string): boolean {
  if (/[.#\[]/.test(selector)) {
    return false;
  }
  return /(?:^|[\s>+~])a(?:::[\w-]+|:[\w-]+(?:\([^)]*\))?)*$/.test(selector);
}
