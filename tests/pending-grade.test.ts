import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it } from 'vitest';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import ProgramCard from '../src/components/ProgramCard.astro';
import { formatPickGrade } from '../src/lib/card-pick';
import { closeMarker } from '../src/lib/format-picks';
import { gameCardSchema, type GameCard } from '../src/lib/game-schema';
import type { PickView } from '../src/lib/picks-schema';

const view = JSON.parse(readFileSync(new URL('./fixtures/picks_view.json', import.meta.url), 'utf8')) as PickView[];
const card = gameCardSchema.parse(
  JSON.parse(readFileSync(new URL('./fixtures/games/2099_01_KC_BUF.json', import.meta.url), 'utf8')),
) as GameCard;
const kickoff = card.kickoff_utc;
const beforeKickoff = '2099-09-14T12:00:00Z';
const afterKickoff = '2099-09-15T12:00:00Z';

function fixtureRow(pickId: string): PickView {
  const row = view.find((item) => item.pick_id === pickId);
  if (!row) {
    throw new Error(`missing fixture pick ${pickId}`);
  }
  return row;
}

function pending(overrides: Partial<PickView> = {}): PickView {
  const base = fixtureRow('2099_01_KC_BUF-spread');
  return {
    ...base,
    side: 'BUF',
    line_at_pick: -3,
    closing_line: -6.5,
    close_source: 'odds_api_stale',
    clv_pts: null,
    result: null,
    status: 'pending',
    units_pl: null,
    ...overrides,
  };
}

const container = await AstroContainer.create();
const previousBuildNow = process.env.BUILD_NOW;

afterEach(() => {
  if (previousBuildNow === undefined) {
    delete process.env.BUILD_NOW;
  } else {
    process.env.BUILD_NOW = previousBuildNow;
  }
});

async function renderAt(iso: string, picks: PickView[]): Promise<string> {
  process.env.BUILD_NOW = iso;
  return container.renderToString(ProgramCard, {
    props: { card, picks, writeups: [] },
  });
}

describe('pending grade line', () => {
  it('reads Line at pick before kickoff, even when a close is already stored', async () => {
    const row = pending();
    expect(formatPickGrade(row, kickoff, new Date(beforeKickoff))).toBe('Line at pick BUF -3.0 · Pending');
    const html = await renderAt(beforeKickoff, [row]);
    expect(html).toContain('Line at pick BUF -3.0 · Pending');
    expect(html).not.toContain('Closed ');
    expect(html).not.toContain('stale close');
  });

  it('shows the closing line only after kickoff, and never the stale label', async () => {
    const row = pending();
    expect(formatPickGrade(row, kickoff, new Date(afterKickoff))).toBe('Closed BUF -6.5 · Status pending');
    expect(closeMarker('odds_api_stale')).toBeNull();
    const html = await renderAt(afterKickoff, [row]);
    expect(html).toContain('Line at pick BUF -3.0 · Pending');
    expect(html).not.toContain('Closed ');
    expect(html).not.toContain('stale close');
  });

  it('stays pending after kickoff when no close was captured', async () => {
    const row = pending({ closing_line: null, close_source: null, clv_pts: null });
    expect(formatPickGrade(row, kickoff, new Date(afterKickoff))).toBe('Line at pick BUF -3.0 · Pending');
    const html = await renderAt(afterKickoff, [row]);
    expect(html).toContain('Line at pick BUF -3.0 · Pending');
    expect(html).not.toContain('Closed ');
    expect(html).not.toContain('stale close');
  });

  it('keeps graded W, L, and Push lines on either side of kickoff', () => {
    const win = fixtureRow('2099_01_DET_PHI-spread-r2');
    const loss = fixtureRow('2099_01_DET_PHI-total');
    const push = fixtureRow('2099_01_KC_BUF-spread');
    const expected = {
      win: 'Closed DET +3.0 · nflverse line · CLV \u22120.5 · Result W · Status graded',
      loss: 'Closed Over 47.5 · nflverse line · CLV +1 · Result L · Status graded',
      push: 'Closed KC -3.0 · nflverse line · CLV +0.5 · Result Push · Status graded',
    };
    for (const now of [beforeKickoff, afterKickoff]) {
      const at = new Date(now);
      expect(formatPickGrade(win, kickoff, at)).toBe(expected.win);
      expect(formatPickGrade(loss, kickoff, at)).toBe(expected.loss);
      expect(formatPickGrade(push, kickoff, at)).toBe(expected.push);
    }
  });
});
