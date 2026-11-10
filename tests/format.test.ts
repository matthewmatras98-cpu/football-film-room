import { describe, expect, it } from 'vitest';
import {
  EM_DASH,
  STATUS_LABEL,
  formatClv,
  formatMst,
  formatPct,
  formatPickLine,
  formatSignedLine,
  formatUnits,
  formatWinPct,
} from '../src/lib/format-picks';
import { byWeek, currentWeekPicks, picksForWeek, weekPickEntries } from '../src/lib/picks';

describe('formatUnits', () => {
  it('shows wins, losses, and pushes in units', () => {
    expect(formatUnits(0.909)).toBe('+0.909u');
    expect(formatUnits(-1)).toBe('\u22121.000u');
    expect(formatUnits(0)).toBe('0.000u');
  });

  it('shows null as an em dash and never as zero', () => {
    expect(formatUnits(null)).toBe(EM_DASH);
    expect(formatUnits(null)).not.toContain('0');
    expect(formatClv(null)).toBe(EM_DASH);
    expect(formatClv(null)).not.toContain('0');
    expect(formatSignedLine(null)).toBe(EM_DASH);
    expect(formatPickLine('spread', null)).toBe(EM_DASH);
    expect(formatPickLine('total', null)).toBe(EM_DASH);
    expect(formatPct(null)).toBe(EM_DASH);
    expect(formatPct(null)).not.toContain('0');
  });
});

describe('line sign', () => {
  it('keeps the sign already stored from the picked side', () => {
    expect(formatSignedLine(2.5)).toBe('+2.5');
    expect(formatSignedLine(-3)).toBe('\u22123.0');
    expect(formatSignedLine(-2.5)).toBe('\u22122.5');
    expect(formatPickLine('spread', 3.5)).toBe('+3.5');
    expect(formatPickLine('spread', -7)).toBe('\u22127.0');
    expect(formatPickLine('total', 46.5)).toBe('46.5');
    expect(formatPickLine('total', 47)).toBe('47.0');
  });
});

describe('formatWinPct', () => {
  it('reads the summary figure and does not recompute it', () => {
    expect(formatWinPct({ win_pct: 0.429 })).toBe('42.9%');
    expect(formatPct(0.778)).toBe('77.8%');
    const disagreeing = { w: 9, l: 1, p: 4, win_pct: 0.1 };
    expect(formatWinPct(disagreeing)).toBe('10.0%');
    expect(formatWinPct(disagreeing)).not.toBe('90.0%');
    expect(formatWinPct({ win_pct: null })).toBe(EM_DASH);
  });
});

describe('formatClv', () => {
  it('signs CLV and keeps three decimal places', () => {
    expect(formatClv(0.5)).toBe('+0.500');
    expect(formatClv(-0.5)).toBe('\u22120.500');
    expect(formatClv(1)).toBe('+1.000');
    expect(formatClv(0)).toBe('0.000');
  });
});

describe('formatMst', () => {
  it('labels Phoenix time as MST', () => {
    expect(formatMst('2099-09-13T15:00:00Z')).toBe('Sep 13, 2099, 8:00 AM MST');
    expect(formatMst('2099-09-13T12:00:00Z')).toBe('Sep 13, 2099, 5:00 AM MST');
    expect(formatMst('2099-09-20T00:00:00Z')).toBe('Sep 19, 2099, 5:00 PM MST');
    expect(formatMst('2026-11-14T18:00:00-07:00')).toBe('Nov 14, 2026, 6:00 PM MST');
    expect(formatMst(null)).toBe(EM_DASH);
  });
});

describe('status label', () => {
  it('uses the exact rejected label', () => {
    expect(STATUS_LABEL.rejected_late).toBe('Rejected \u2013 late');
    expect(STATUS_LABEL.graded).toBe('Final');
    expect(STATUS_LABEL.pending).toBe('Pending');
    expect(STATUS_LABEL.ungraded).toBe('Ungraded');
  });
});

describe('this week', () => {
  const rows = [
    { season: 2099, week: 1, id: 'early' },
    { season: 2099, week: 2, id: 'current' },
    { season: 2099, week: 1, id: 'also-early' },
  ];

  it('uses the latest season, then week, and does not fall back', () => {
    expect(currentWeekPicks(rows).map((row) => row.id)).toEqual(['current']);
    expect(picksForWeek(rows, 3)).toEqual([]);
    expect(currentWeekPicks([])).toEqual([]);
    expect(byWeek(rows).map((group) => group.week)).toEqual([2, 1]);
    expect(byWeek(rows)[1]?.picks.map((row) => row.id)).toEqual(['early', 'also-early']);
  });

  it('keeps an unknown game id as plain text', () => {
    const picks = [{ game_id: '2099_01_DET_PHI', pick_id: '2099_01_DET_PHI-spread', market: 'spread' }];
    expect(weekPickEntries(['2099_01_DET_PHI', '2026_11_DET_PHI'], picks)).toEqual([
      {
        href: '/picks/#pick-2099_01_DET_PHI-spread',
        label: 'DET at PHI spread',
      },
      { href: null, label: '2026_11_DET_PHI' },
    ]);
    expect(weekPickEntries([], picks)).toEqual([]);
  });
});
