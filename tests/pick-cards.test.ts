import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import PickCards from '../src/components/PickCards.astro';
import ProgramCard from '../src/components/ProgramCard.astro';
import PicksTable from '../src/components/PicksTable.astro';
import { formatCardPick, formatPickGrade, officialCardPicks } from '../src/lib/card-pick';
import { gameCardSchema, type GameCard } from '../src/lib/game-schema';
import type { PickView } from '../src/lib/picks-schema';
import type { Writeup } from '../src/lib/writeup';

const css = readFileSync(new URL('../src/styles/global.css', import.meta.url), 'utf8');
const card = gameCardSchema.parse(
  JSON.parse(readFileSync(new URL('../src/content/games/2026_10_NE_DET.json', import.meta.url), 'utf8')),
) as GameCard;

const writeup: Writeup = {
  pick_id: '2026_10_NE_DET-spread',
  game_id: '2026_10_NE_DET',
  submitted_at: '2026-11-13T02:17:00.000Z',
  body: 'Cover the number',
};

function pick(overrides: Partial<PickView> = {}): PickView {
  return {
    pick_id: '2026_10_NE_DET-spread',
    game_id: '2026_10_NE_DET',
    season: 2026,
    week: 10,
    market: 'spread',
    side: 'DET',
    line_at_pick: -3,
    picked_at: '2026-11-13T02:17:00Z',
    units: 1,
    confidence: 1,
    tags: '',
    note: '',
    closing_line: -6.5,
    close_source: 'odds_api',
    close_ts: '2026-11-13T01:08:29Z',
    close_books: 9,
    close_age_min: 30,
    final_home: null,
    final_away: null,
    result: null,
    units_pl: null,
    clv_pts: null,
    status: 'pending',
    first_pushed_at: '2026-11-13T02:17:17Z',
    voided_at: null,
    ...overrides,
  };
}

function compact(html: string): string {
  return html.replace(/\s+/g, ' ').trim();
}

function withoutDetails(html: string): string {
  return html.replace(/<details\b[\s\S]*?<\/details>/g, '');
}

function asUnpicked(html: string): string {
  return compact(withoutDetails(html)).replace(
    /<span class="badge badge--none pick-line">DET -3\.0(?: · [WL]| · Push)?<\/span>/g,
    '<span class="badge badge--none">No official pick</span>',
  );
}

function block(source: string, startMarker: string, endMarker: string): string {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start + startMarker.length);
  expect(start, startMarker).toBeGreaterThanOrEqual(0);
  expect(end, endMarker).toBeGreaterThan(start);
  return source.slice(start, end);
}

const container = await AstroContainer.create();

async function cardHtml(picks: PickView[], writeups: readonly Writeup[] = [writeup]): Promise<string> {
  return container.renderToString(ProgramCard, {
    props: { card, picks, writeups },
  });
}

async function ledgerHtml(
  rows: PickView[],
  writeups: readonly Writeup[] = [writeup],
  games: GameCard[] = [card],
): Promise<string> {
  return container.renderToString(PickCards, {
    props: { picks: rows, games, writeups },
  });
}

function anchors(html: string): string[] {
  return html.match(/<a\b[^>]*>[\s\S]*?<\/a>/g) ?? [];
}

function cardBlock(html: string, pickId: string): string {
  const marker = `id="pick-${pickId}"`;
  const start = html.indexOf(marker);
  expect(start, marker).toBeGreaterThanOrEqual(0);
  const next = html.indexOf('id="pick-', start + marker.length);
  return html.slice(start, next === -1 ? html.length : next);
}

function cardInner(html: string, pickId: string): string {
  const block = cardBlock(html, pickId);
  const open = block.indexOf('>');
  const close = block.lastIndexOf('</li>');
  return block.slice(open + 1, close === -1 ? block.length : close);
}

describe('official card picks', () => {
  it('uses the line the pick was made at', () => {
    expect(formatCardPick(pick())).toBe('DET -3.0');
    expect(formatCardPick(pick())).not.toContain('Pick:');
    expect(formatCardPick(pick())).not.toContain('-6.5');
    expect(
      formatCardPick(
        pick({
          pick_id: '2026_10_NE_DET-total',
          market: 'total',
          side: 'over',
          line_at_pick: 45.5,
          closing_line: 47,
        }),
      ),
    ).toBe('Over 45.5');
    expect(
      formatCardPick(
        pick({
          pick_id: '2026_10_NE_DET-total',
          market: 'total',
          side: 'under',
          line_at_pick: 45.5,
        }),
      ),
    ).toBe('Under 45.5');
  });

  it('keeps a short grade line and drops fields that are missing', () => {
    expect(formatPickGrade(pick())).toBe('Closed DET -6.5 · Status pending');
    expect(
      formatPickGrade(
        pick({
          closing_line: null,
          clv_pts: null,
          result: null,
          close_source: null,
        }),
      ),
    ).toBe('Status pending');
    expect(
      formatPickGrade(
        pick({
          closing_line: -4.5,
          clv_pts: 1.5,
          result: 'W',
          status: 'graded',
          units_pl: 0.909,
          close_source: 'odds_api',
        }),
      ),
    ).toBe('Closed DET -4.5 · CLV +1.500 · Result W · Status graded');
    expect(
      formatPickGrade(
        pick({
          status: 'void',
          voided_at: '2026-11-13T03:00:00Z',
          closing_line: -4.5,
          close_source: 'odds_api_stale',
          clv_pts: null,
          result: null,
        }),
      ),
    ).toBe('Pick DET -3.0 · Closed DET -4.5 · stale close · Status void');
    expect(
      formatPickGrade(
        pick({
          status: 'rejected_late',
          closing_line: null,
          clv_pts: null,
          result: null,
          close_source: null,
        }),
      ),
    ).toBe('Pick DET -3.0 · Status rejected_late');
  });

  it('appends a grade only when one is already on the row', () => {
    expect(formatCardPick(pick({ result: 'W' }))).toBe('DET -3.0 · W');
    expect(formatCardPick(pick({ result: 'L' }))).toBe('DET -3.0 · L');
    expect(formatCardPick(pick({ result: 'P' }))).toBe('DET -3.0 · Push');
    expect(formatCardPick(pick({ result: null, status: 'pending' }))).toBe('DET -3.0');
    expect(formatCardPick(pick({ result: null, status: 'ungraded' }))).not.toContain('Ungraded');
  });

  it('drops voids and rejected-late rows and keeps the latest remaining version', () => {
    const voided = pick({ status: 'void', voided_at: '2026-11-13T03:00:00Z' });
    const rejected = pick({ status: 'rejected_late' });
    const repick = pick({
      pick_id: '2026_10_NE_DET-spread-r2',
      side: 'NE',
      line_at_pick: -1,
      status: 'pending',
    });
    expect(officialCardPicks([voided])).toEqual([]);
    expect(officialCardPicks([rejected])).toEqual([]);
    expect(officialCardPicks([voided, rejected, repick])).toEqual([repick]);
  });
});

describe('program card markup', () => {
  it('renders an unpicked card with the existing footer and no accordion', async () => {
    const html = await cardHtml([]);
    expect(html).toContain('<span class="badge badge--none">No official pick</span>');
    expect(html).toContain('<a class="game-card-link" href="/games/2026_10_NE_DET/">NE @ DET</a>');
    expect(html).toContain('>Read matchup notes</a>');
    expect(html).not.toContain('<details');
    expect(html).not.toContain('pick-line');
    expect(html).not.toContain('Pick:');
    expect(html).not.toContain('Cover the number');
  });

  it('swaps only the footer text and adds a collapsed write-up', async () => {
    const unpicked = await cardHtml([]);
    const picked = await cardHtml([pick()]);
    const openTags = picked.match(/<details\b[^>]*>/g) ?? [];
    expect(openTags).toEqual(['<details class="writeup-details">']);
    expect(picked).toContain('<span class="badge badge--none pick-line">DET -3.0</span>');
    expect(picked).not.toContain('Pick:');
    expect(picked).not.toContain('DET -6.5');
    expect(picked).toContain('<span class="visually-hidden"> for NE at DET</span>');
    expect(picked).toMatch(/<summary>[\s\S]*<span class="visually-hidden"> for NE at DET<\/span>[\s\S]*<\/summary>/);
    const summary = picked.match(/<summary>[\s\S]*?<\/summary>/)?.[0] ?? '';
    expect(summary).toContain('Read the write-up');
    expect(summary).toContain('Hide the write-up');
    expect(summary).not.toMatch(/<a\b|<button\b|<input\b/);
    expect(summary).not.toMatch(/aria-label/);
    expect(asUnpicked(picked)).toBe(compact(unpicked));
  });

  it('renders a voided pick as an unpicked card', async () => {
    const unpicked = await cardHtml([]);
    const voided = await cardHtml([pick({ status: 'void', voided_at: '2026-11-13T03:00:00Z' })]);
    expect(compact(voided)).toBe(compact(unpicked));
    expect(voided).not.toContain('<details');
    expect(voided).toContain('No official pick');
  });

  it('renders a rejected-late pick as an unpicked card', async () => {
    const unpicked = await cardHtml([]);
    const rejected = await cardHtml([pick({ status: 'rejected_late' })]);
    expect(compact(rejected)).toBe(compact(unpicked));
    expect(rejected).not.toContain('<details');
    expect(rejected).toContain('No official pick');
  });

  it('leaves the accordion off when the official pick has no write-up', async () => {
    const html = await cardHtml([pick()], []);
    expect(html).toContain('<span class="badge badge--none pick-line">DET -3.0</span>');
    expect(html).not.toContain('<details');
  });
});

describe('picks ledger', () => {
  it('keeps the write-up inside a collapsed details element', async () => {
    const row = pick();
    const html = await container.renderToString(PicksTable, {
      props: {
        picks: [row],
        gameIds: new Set([row.game_id]),
        writeups: [writeup],
      },
    });
    const outside = withoutDetails(html);
    expect(html).toContain('Cover the number');
    expect(html).toContain('class="pick-writeup"');
    expect(html).toContain('<span class="visually-hidden"> for NE at DET</span>');
    expect(outside).not.toContain('Cover the number');
    expect(outside).not.toContain('class="writeup');
    expect(outside).not.toContain('<details');
    const openTags = html.match(/<details\b[^>]*>/g) ?? [];
    expect(openTags).toEqual(['<details class="writeup-details">']);
  });

  it('still labels a rejected-late ledger row as rejected', async () => {
    const html = await container.renderToString(PicksTable, {
      props: {
        picks: [pick({ status: 'rejected_late' })],
        gameIds: new Set<string>(),
        writeups: [],
      },
    });
    expect(html).toContain('Rejected \u2013 late');
    expect(html).not.toContain('<details');
  });

  it('still labels a voided ledger row as void', async () => {
    const html = await container.renderToString(PicksTable, {
      props: {
        picks: [pick({ status: 'void', voided_at: '2026-11-13T03:00:00Z' })],
        gameIds: new Set<string>(),
        writeups: [writeup],
      },
    });
    const outside = withoutDetails(html);
    expect(outside).toContain('Void');
    expect(outside).not.toContain('Cover the number');
    expect(html).toContain('<details class="writeup-details">');
  });
});

describe('picks page cards', () => {
  it('renders exactly one card per pick, including re-picks', async () => {
    const rows = [
      pick(),
      pick({
        pick_id: '2026_10_NE_DET-spread-r2',
        side: 'NE',
        line_at_pick: -1,
        status: 'pending',
        closing_line: null,
        clv_pts: null,
      }),
      pick({
        pick_id: '2026_10_NE_DET-total',
        market: 'total',
        side: 'over',
        line_at_pick: 51,
        status: 'void',
        voided_at: '2026-11-13T03:00:00Z',
      }),
    ];
    const html = await ledgerHtml(rows);
    expect(html.match(/<li\b[^>]*\bclass="game-card"/g)).toHaveLength(rows.length);
    expect(html.match(/id="pick-[^"]+"/g)).toHaveLength(rows.length);
    for (const row of rows) {
      expect(html).toContain(`id="pick-${row.pick_id}"`);
    }
    expect(html).toContain('class="card-list"');
    for (const anchor of anchors(html)) {
      expect(anchor).not.toContain('<details');
    }
  });

  it('matches the home card for the same game, ignoring the accordion', async () => {
    const row = pick();
    const home = await cardHtml([row]);
    const ledger = await ledgerHtml([row]);
    const inner = cardInner(ledger, row.pick_id);
    expect(compact(withoutDetails(inner))).toBe(compact(withoutDetails(home)));
    expect(home).toContain('<span class="badge badge--none pick-line">DET -3.0</span>');
    expect(inner).toContain('class="pick-grade"');
    expect(inner).toContain('Closed DET -6.5 · Status pending');
    expect(inner.indexOf('class="pick-grade"')).toBeLessThan(inner.indexOf('Submitted (server time)'));
    expect(inner.indexOf('Submitted (server time)')).toBeLessThan(inner.indexOf('Cover the number'));
    expect(inner).toContain(`datetime="${row.picked_at}"`);
  });

  it('shows a void pick as its own muted card and leaves the home card unpicked', async () => {
    const row = pick({ status: 'void', voided_at: '2026-11-13T03:00:00Z' });
    const home = await cardHtml([row]);
    const ledger = await ledgerHtml([row], []);
    const block = cardBlock(ledger, row.pick_id);
    expect(home).toContain('No official pick');
    expect(home).not.toContain('<details');
    expect(block).toContain('<span class="badge badge--none badge--quiet">Void</span>');
    expect(block).not.toContain('pick-line');
    expect(block).toContain('Pick DET -3.0 · Closed DET -6.5 · Status void');
    expect(block).toContain('Pick details');
    expect(block).toContain('<span class="visually-hidden"> for NE at DET</span>');
    expect(block).not.toContain('Cover the number');
    expect(block).not.toContain('Read the write-up');
  });

  it('shows a rejected-late pick as its own muted card', async () => {
    const row = pick({ status: 'rejected_late', closing_line: -4, close_source: null });
    const home = await cardHtml([row]);
    const ledger = await ledgerHtml([row]);
    const block = cardBlock(ledger, row.pick_id);
    expect(home).toContain('No official pick');
    expect(home).not.toContain('Rejected');
    expect(block).toContain('<span class="badge badge--none badge--quiet">Rejected \u2013 late</span>');
    expect(block).toContain('Pick DET -3.0');
    expect(block).toContain('Status rejected_late');
    expect(block).not.toContain('pick-line');
    for (const anchor of anchors(block)) {
      expect(anchor).not.toContain('<details');
    }
  });

  it('uses Pick details when the ledger card has no write-up', async () => {
    const row = pick({ closing_line: null, clv_pts: null, result: null, close_source: null });
    const html = await ledgerHtml([row], []);
    const summary = html.match(/<summary>[\s\S]*?<\/summary>/)?.[0] ?? '';
    expect(summary).toContain('Pick details');
    expect(summary).toContain('Hide the pick details');
    expect(summary).toContain('<span class="visually-hidden"> for NE at DET</span>');
    expect(summary).not.toContain('Read the write-up');
    expect(html).toContain('Submitted (server time)');
    expect(html).toContain('Status pending');
    expect(html).not.toContain('Cover the number');
    expect(html).not.toContain('<writeup');
    expect(html.match(/<details\b[^>]*>/g)).toEqual(['<details class="writeup-details">']);
  });

  it('still renders a card when the game JSON is missing', async () => {
    const row = pick({
      pick_id: '2026_11_BUF_KC-spread',
      game_id: '2026_11_BUF_KC',
      side: 'BUF',
      line_at_pick: -2.5,
      closing_line: null,
      clv_pts: null,
      close_source: null,
    });
    const html = await ledgerHtml([row], []);
    const block = cardBlock(html, row.pick_id);
    expect(block).toContain('BUF @ KC');
    expect(block).toContain('<span class="visually-hidden"> for BUF at KC</span>');
    expect(block).toContain('<span class="when">n/a (source unavailable)</span>');
    expect(block).toContain('Line n/a (source unavailable)');
    expect(block).not.toContain('game-angle');
    expect(block).not.toContain('href="/games/2026_11_BUF_KC/"');
    expect(block).toContain('Submitted (server time)');
    expect(block).toContain('Status pending');
  });
});

describe('card grid layout', () => {
  it('starts card grids at the top of the row', () => {
    const cardList = block(css, '.card-list {', '.game-card {');
    const gamesGrid = block(css, '.games-grid {', '.slate-card {');
    const slate = block(css, '.slate-card {', '.slate-card .when');
    expect(cardList).toMatch(/align-items:\s*start/);
    expect(gamesGrid).toMatch(/align-items:\s*start/);
    expect(slate).not.toMatch(/min-height:\s*100%/);
  });

  it('gives the summary a 44px hit area, a rotating chevron, and no native marker', () => {
    const summary = block(css, '.writeup-details > summary {', '.writeup-details > summary::-webkit-details-marker');
    const marker = block(css, '.writeup-details > summary::-webkit-details-marker {', '.writeup-details > summary:focus-visible');
    const focus = block(css, '.writeup-details > summary:focus-visible {', '.writeup-label--hide');
    const openChevron = block(css, '.writeup-details[open] .writeup-chevron {', '.writeup-details .writeup');
    expect(summary).toMatch(/list-style:\s*none/);
    expect(summary).toMatch(/min-height:\s*44px/);
    expect(marker).toMatch(/display:\s*none/);
    expect(focus).toMatch(/box-shadow:\s*var\(--focus-ring\)/);
    expect(openChevron).toMatch(/transform:\s*rotate\(225deg\)/);
    const cell = block(css, '.pick-writeup {', '.status {');
    expect(cell).toMatch(/white-space:\s*normal/);
    expect(cell).toMatch(/min-width:\s*16rem/);
    const small = block(css, '@media (max-width: 48rem)', '@media (min-width: 40.01rem)');
    expect(small).toMatch(/\.picks td\.pick-writeup\s*\{[^}]*min-width:\s*0/);
    expect(small).toMatch(/\.picks td\.pick-writeup\s*\{[^}]*white-space:\s*normal/);
  });
});
