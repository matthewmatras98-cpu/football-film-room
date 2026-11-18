import { describe, expect, it } from 'vitest';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import PicksTable from '../src/components/PicksTable.astro';
import ProgramCard from '../src/components/ProgramCard.astro';
import SeasonSummary from '../src/components/SeasonSummary.astro';
import type { PickView, Summary } from '../src/lib/picks-schema';

const summary = {
  season: 2026,
  w: 3,
  l: 1,
  p: 0,
  win_pct: 0.75,
  units: 1.727,
  avg_clv: -0.25,
  pct_clv_pos: 0.5,
  rejected_count: 0,
  ungraded_count: 0,
  void_count: 0,
  generated_at: '2026-11-17T15:00:00Z',
} satisfies Summary;

const row = {
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
  closing_line: -2.5,
  close_source: 'nflverse',
  close_ts: null,
  close_books: null,
  close_age_min: null,
  final_home: 20,
  final_away: 17,
  result: 'W',
  units_pl: 0.909,
  clv_pts: -0.5,
  status: 'graded',
  first_pushed_at: '2026-11-13T02:17:17Z',
  voided_at: null,
} satisfies PickView;

describe('rendered CLV and units', () => {
  it('shows trimmed CLV and two-decimal units in the summary and grade line', async () => {
    const container = await AstroContainer.create();
    const summaryHtml = await container.renderToString(SeasonSummary, { props: { summary } });
    const cardHtml = await container.renderToString(ProgramCard, {
      props: { card: null, picks: [], record: row, writeups: [] },
    });
    expect(summaryHtml).toContain('+1.73u');
    expect(summaryHtml).toContain('Average CLV: \u22120.25');
    expect(summaryHtml).not.toContain('+1.727u');
    expect(summaryHtml).not.toContain('\u22120.250');
    expect(cardHtml).toContain('CLV \u22120.5');
    expect(cardHtml).not.toContain('CLV \u22120.50');
    const table = await container.renderToString(PicksTable, { props: { picks: [row], writeups: [] } });
    expect(table).toContain('+0.91u');
    expect(table).toContain('CLV \u22120.5');
  });
});
