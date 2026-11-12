import { describe, expect, it } from 'vitest';
import {
  NFLVERSE_TABLE_CREDIT,
  SOURCE_UNAVAILABLE,
  formatEpaStory,
  formatEpaValue,
  formatHomeLine,
  formatRest,
  formatSpreadTeam,
  formatStat,
  formatStatKind,
  formatTotalLine,
  tableSourceNotes,
} from '../src/lib/format-stat';
import { slateLine } from '../src/lib/program';

const unavailable = {
  value: null,
  source: 'nflverse_pbp',
  sample: null,
  range: null,
};

describe('formatStat', () => {
  it('renders null as the exact unavailable string and never as zero', () => {
    expect(formatStat(unavailable)).toBe('n/a (source unavailable)');
    expect(formatStat(null)).toBe(SOURCE_UNAVAILABLE);
    expect(formatStat(undefined)).toBe('n/a (source unavailable)');
    expect(formatStat(unavailable)).not.toMatch(/0/);
    expect(formatStatKind(unavailable, 'rate')).toBe('n/a (source unavailable)');
    expect(formatStatKind(unavailable, 'rate')).not.toMatch(/0/);
    expect(formatStatKind(unavailable, 'epa')).not.toMatch(/0/);
    expect(formatStatKind(unavailable, 'precip')).not.toBe('0%');
  });

  it('shows zero only when the value is zero', () => {
    const zero = { value: 0, source: 'nflverse_pbp', sample: 10, range: '2099 wk1' };
    expect(formatStat(zero)).toBe('0');
    expect(formatStatKind(zero, 'number')).toBe('0');
    expect(formatStatKind(zero, 'rate')).toBe('0.0%');
    expect(formatStatKind(zero, 'epa')).toBe('0.000');
    expect(formatStatKind(zero, 'precip')).toBe('0%');
    expect(formatStatKind(zero, 'seconds')).toBe('0.00 s');
    expect(formatRest(0)).toBe('0 days');
    expect(formatRest(null)).toBe('n/a (source unavailable)');
  });

  it('does not turn a real value into the unavailable string', () => {
    const value = { value: 0.5, source: 'nflverse_pbp', sample: 20, range: '2099 wk1' };
    expect(formatStat(value)).not.toBe('n/a (source unavailable)');
    expect(formatStatKind(value, 'rate')).toBe('50.0%');
    expect(formatStatKind({ value: 30 }, 'precip')).toBe('30%');
    expect(formatStatKind({ value: 2.4 }, 'cpoe')).toBe('+2.4 pp');
    expect(formatStatKind({ value: -1.2 }, 'cpoe')).toBe('\u22121.2 pp');
    expect(formatStatKind({ value: 2.71 }, 'seconds')).toBe('2.71 s');
    expect(formatStatKind({ value: 0.08 }, 'epa')).toBe('+0.080');
    expect(formatStatKind({ value: -0.04 }, 'epa')).toBe('\u22120.040');
    expect(formatEpaValue(0.139771)).toBe('+0.140');
    expect(formatEpaValue(-0.027817)).toBe('\u22120.028');
    expect(formatEpaValue(-0.027817)).not.toContain('-');
    expect(
      formatEpaStory(
        'Offense EPA per play',
        'BUF is 0.139771 and NYJ is -0.027817 over 2026 wk1\u20139.',
      ),
    ).toBe('BUF is +0.140 and NYJ is \u22120.028 over 2026 wk1\u20139.');
    expect(formatEpaStory('Sack rate for', 'BUF defense is 0.051829')).toBe(
      'BUF defense is 0.051829',
    );
    expect(formatStatKind({ value: 42 }, 'temp')).toBe('42\u00b0F');
    expect(formatStatKind({ value: 12 }, 'wind')).toBe('12 mph');
    expect(formatHomeLine(null)).toBe('n/a (source unavailable)');
    expect(formatHomeLine(-2.5)).toBe('\u22122.5');
    expect(formatTotalLine(null)).toBe('n/a (source unavailable)');
    expect(formatTotalLine(44.5)).toBe('44.5');
  });
});

describe('spread label', () => {
  const point = {
    ts: '2026-11-11T16:29:30Z',
    home_spread: 8,
    total: 51,
    n_books: 9,
  };

  it('uses the team abbreviation, one decimal, and an explicit sign', () => {
    expect(formatSpreadTeam('NYJ', 8)).toBe('NYJ +8.0');
    expect(formatSpreadTeam('BUF', -3.5)).toBe('BUF -3.5');
    expect(formatSpreadTeam('BUF', -3.5)).not.toContain('\u2212');
    expect(formatSpreadTeam('KC', 0)).toBe('KC +0.0');
    expect(formatSpreadTeam('NYJ', null)).toBe('NYJ n/a (source unavailable)');
    expect(slateLine(point, 'NYJ')).toBe('NYJ +8.0 · Total 51.0');
    expect(slateLine({ ...point, home_spread: -3.5, total: 45 }, 'BUF')).toBe('BUF -3.5 · Total 45.0');
    expect(slateLine(null, 'NYJ')).toBe('Line n/a (source unavailable)');
  });
});

describe('table source notes', () => {
  const stat = (sample: number, source = 'nflverse_pbp') => ({
    source,
    sample,
    range: '2026 wk1–9',
  });

  it('lists each source line once per table and keeps the nflverse CC-BY credit', () => {
    const shared = tableSourceNotes([stat(535), stat(535)]);
    expect(shared[0]).toBe('nflverse_pbp · n=535 · 2026 wk1–9');
    expect(shared.filter((note) => note.includes('n=535'))).toHaveLength(1);
    expect(shared.filter((note) => note === NFLVERSE_TABLE_CREDIT)).toHaveLength(1);

    const mixed = tableSourceNotes([stat(535), stat(535), stat(549)]);
    expect(mixed.filter((note) => note.includes('nflverse_pbp'))).toHaveLength(1);
    expect(mixed.filter((note) => note.includes('n=535'))).toHaveLength(0);
    expect(mixed.filter((note) => note === NFLVERSE_TABLE_CREDIT)).toHaveLength(1);
    expect(mixed.join('\n')).toContain('Data: nflverse (CC-BY 4.0).');
    expect(mixed.filter((note) => note.includes('CC-BY 4.0'))).toHaveLength(1);
  });

  it('adds the Next Gen Stats credit once when that source is in the table', () => {
    const notes = tableSourceNotes([
      stat(271, 'ngs_via_nflverse'),
      stat(271, 'ngs_via_nflverse'),
      stat(301),
    ]);
    expect(notes.filter((note) => note.includes('nflverse_pbp'))).toHaveLength(1);
    expect(notes.filter((note) => note.includes('ngs_via_nflverse'))).toHaveLength(1);
    expect(notes.filter((note) => note.includes('n=271'))).toHaveLength(0);
    expect(notes.filter((note) => note.includes('Next Gen Stats'))).toHaveLength(1);
    expect(notes.filter((note) => note.includes('CC-BY 4.0'))).toHaveLength(1);
  });
});
