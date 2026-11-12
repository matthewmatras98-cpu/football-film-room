import { describe, expect, it } from 'vitest';
import { formatUnits } from '../src/lib/format-picks';
import { formatSeasonLine, homeEmptyNote } from '../src/lib/home';

describe('formatSeasonLine', () => {
  it('builds one compact line from the summary record and units', () => {
    expect(formatSeasonLine({ w: 0, l: 0, p: 0, units: 0 })).toBe(`0-0-0 · ${formatUnits(0)}`);
    expect(formatSeasonLine({ w: 0, l: 0, p: 0, units: 0 })).toBe('0-0-0 · 0.000u');
    expect(formatSeasonLine({ w: 3, l: 1, p: 1, units: 1.727 })).toBe('3-1-1 · +1.727u');
    expect(formatSeasonLine({ w: 0, l: 2, p: 0, units: -2 })).toBe('0-2-0 · \u22122.000u');
  });

  it('stays in units and does not print a dollar amount', () => {
    const line = formatSeasonLine({ w: 1, l: 0, p: 0, units: 0.909 });
    expect(line).toBe('1-0-0 · +0.909u');
    expect(line).not.toContain('$');
  });
});

describe('homeEmptyNote', () => {
  it('shows a factual note only while picks and published posts are both absent', () => {
    expect(homeEmptyNote(0, 0, 4)).toBe(
      "No picks or posts yet this season. This week's games are below.",
    );
    expect(homeEmptyNote(0, 0, 0)).toBe('No picks or posts yet this season.');
  });

  it('hides the note once picks or posts exist', () => {
    expect(homeEmptyNote(1, 0, 4)).toBeNull();
    expect(homeEmptyNote(0, 1, 4)).toBeNull();
    expect(homeEmptyNote(2, 3, 0)).toBeNull();
  });
});
