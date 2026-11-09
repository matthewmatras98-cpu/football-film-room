import { describe, expect, it } from 'vitest';
import {
  SOURCE_UNAVAILABLE,
  formatHomeLine,
  formatRest,
  formatStat,
  formatStatKind,
  formatTotalLine,
} from '../src/lib/format-stat';

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
    expect(formatStatKind({ value: 42 }, 'temp')).toBe('42\u00b0F');
    expect(formatStatKind({ value: 12 }, 'wind')).toBe('12 mph');
    expect(formatHomeLine(null)).toBe('n/a (source unavailable)');
    expect(formatHomeLine(-2.5)).toBe('\u22122.5');
    expect(formatTotalLine(null)).toBe('n/a (source unavailable)');
    expect(formatTotalLine(44.5)).toBe('44.5');
  });
});
