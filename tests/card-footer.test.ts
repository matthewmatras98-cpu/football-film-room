import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PickView } from '../src/lib/picks-schema';

const shared = vi.hoisted(() => ({ picks: [] as PickView[] }));

vi.mock('astro:content', () => ({
  getCollection: async () => [],
}));

vi.mock('../src/lib/picks', async () => {
  const actual = await vi.importActual<typeof import('../src/lib/picks')>('../src/lib/picks');
  return { ...actual, picks: shared.picks };
});

const { experimental_AstroContainer: AstroContainer } = await import('astro/container');
const { default: ProgramCard } = await import('../src/components/ProgramCard.astro');
const { default: GamePage } = await import('../src/pages/games/[game_id].astro');
const { siteUrl } = await import('../src/data/site-url.mjs');
const { formatCardFooter, formatCardPick, formatPickGrade } = await import('../src/lib/card-pick');
const { gameCardSchema } = await import('../src/lib/game-schema');

const view = JSON.parse(readFileSync(new URL('./fixtures/picks_view.json', import.meta.url), 'utf8')) as PickView[];
const card = gameCardSchema.parse(
  JSON.parse(readFileSync(new URL('./fixtures/games/2099_01_KC_BUF.json', import.meta.url), 'utf8')),
);
const kickoff = card.kickoff_utc;
const beforeKickoff = '2099-09-14T12:00:00Z';
const afterKickoff = '2099-09-15T12:00:00Z';
const previousBuildNow = process.env.BUILD_NOW;

function base(overrides: Partial<PickView> = {}): PickView {
  const row = view.find((item) => item.pick_id === '2099_01_KC_BUF-spread');
  if (!row) {
    throw new Error('missing fixture pick');
  }
  return {
    ...row,
    side: 'KC',
    line_at_pick: -2.5,
    closing_line: -3,
    close_source: 'odds_api_stale',
    clv_pts: 0.5,
    result: null,
    status: 'pending',
    units_pl: null,
    ...overrides,
  };
}

const container = await AstroContainer.create({ astroConfig: { site: siteUrl() } });

afterEach(() => {
  if (previousBuildNow === undefined) {
    delete process.env.BUILD_NOW;
  } else {
    process.env.BUILD_NOW = previousBuildNow;
  }
});

async function surfaces(row: PickView, now: string): Promise<{ home: string; game: string }> {
  process.env.BUILD_NOW = now;
  shared.picks.splice(0, shared.picks.length, row);
  const home = await container.renderToString(ProgramCard, {
    props: { card, picks: [row], writeups: [] },
  });
  const game = await container.renderToString(GamePage, {
    props: { game: card },
    request: new Request(`${siteUrl()}/games/${card.game_id}/`),
  });
  return { home, game };
}

describe('card footer', () => {
  it('stays pending before kickoff, after kickoff with no close, and after kickoff with no result', async () => {
    const pendingLine = 'PICK KC -2.5 · Pending';
    const cases = [
      { now: beforeKickoff, row: base() },
      { now: afterKickoff, row: base({ closing_line: null, close_source: null, clv_pts: null }) },
      { now: afterKickoff, row: base() },
    ];
    for (const item of cases) {
      expect(formatCardFooter(item.row, kickoff, new Date(item.now))).toBe(pendingLine);
      const html = await surfaces(item.row, item.now);
      expect(html.home).toContain(pendingLine);
      expect(html.game).toContain(pendingLine);
      expect(html.home).not.toContain('Closed ');
      expect(html.game).not.toContain('stale close');
      expect(html.home).not.toContain('stale close');
    }
  });

  it('reads pick · W, L, or Push once a result exists', async () => {
    const graded = [
      { result: 'W' as const, text: 'KC -2.5 · W' },
      { result: 'L' as const, text: 'KC -2.5 · L' },
      { result: 'P' as const, text: 'KC -2.5 · Push' },
    ];
    for (const item of graded) {
      const row = base({ result: item.result, status: 'graded', units_pl: item.result === 'L' ? -1 : 0.909 });
      expect(formatCardPick(row)).toBe(item.text);
      expect(formatCardFooter(row, kickoff, new Date(beforeKickoff))).toBe(`PICK ${item.text}`);
      const html = await surfaces(row, afterKickoff);
      expect(html.home).toContain(`PICK ${item.text}`);
      expect(html.game).toContain(`PICK ${item.text}`);
      expect(html.home).not.toContain('Result ');
      expect(html.home).not.toContain('stale close');
      expect(html.game).not.toContain('stale close');
    }
  });

  it('leaves a void as no official pick and keeps a rejected-late ledger line', async () => {
    const voided = base({ status: 'void', result: null, voided_at: '2099-09-13T16:00:00Z' });
    const voidHtml = await surfaces(voided, afterKickoff);
    expect(voidHtml.home).toContain('No official pick');
    expect(voidHtml.home).not.toContain('PICK KC');
    expect(voidHtml.game).not.toContain('PICK KC');
    expect(voidHtml.game).not.toContain('<h2>Official pick</h2>');
    expect(voidHtml.game).not.toContain('stale close');

    const rejected = base({ status: 'rejected_late', result: null, closing_line: null, close_source: null, clv_pts: null });
    const ledger = formatPickGrade(rejected, kickoff, new Date(afterKickoff));
    expect(formatCardFooter(rejected, kickoff, new Date(afterKickoff))).toBe(ledger);
    const rejectedHtml = await surfaces(rejected, afterKickoff);
    expect(rejectedHtml.home).toContain('No official pick');
    expect(rejectedHtml.home).not.toContain('PICK KC');
    expect(rejectedHtml.game).toContain(ledger);
    expect(rejectedHtml.game).not.toContain('stale close');
  });
});
