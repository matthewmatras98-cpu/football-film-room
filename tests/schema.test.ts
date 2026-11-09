import { describe, expect, it } from 'vitest';
import { weekSchema } from '../src/lib/schemas';

const valid = {
  title: 'Week 11',
  season: 2026,
  week: 11,
  published_at: '2026-11-14T18:00:00-07:00',
  summary: 'One unit on the road side.',
  picks: ['2026_11_DET_PHI'],
  tags: ['rest'],
  draft: false,
};

const fields = [
  'title',
  'season',
  'week',
  'published_at',
  'summary',
  'picks',
  'tags',
  'draft',
] as const;

describe('week schema', () => {
  it('accepts a valid post', () => {
    const parsed = weekSchema.safeParse(valid);
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.published_at).toBe(valid.published_at);
      expect(parsed.data.draft).toBe(false);
    }
  });

  it('accepts a YAML timestamp Date and a two-letter abbreviation', () => {
    const parsed = weekSchema.safeParse({
      ...valid,
      published_at: new Date('2026-11-15T01:00:00.000Z'),
      picks: ['2026_11_LV_KC', '2026_11_LAR_SFO'],
    });
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.published_at).toBe('2026-11-15T01:00:00.000Z');
    }
  });

  it.each(fields)('rejects a post missing %s', (field) => {
    const data: Record<string, unknown> = { ...valid };
    delete data[field];
    expect(weekSchema.safeParse(data).success).toBe(false);
  });

  it('rejects a bad game id', () => {
    expect(weekSchema.safeParse({ ...valid, picks: ['DET-PHI'] }).success).toBe(false);
    expect(weekSchema.safeParse({ ...valid, picks: ['2026_11_det_phi'] }).success).toBe(false);
    expect(weekSchema.safeParse({ ...valid, picks: ['2026_1_DET_PHI'] }).success).toBe(false);
  });

  it('rejects a bad date', () => {
    expect(weekSchema.safeParse({ ...valid, published_at: '2026-11-14' }).success).toBe(false);
    expect(weekSchema.safeParse({ ...valid, published_at: '2026-11-14T18:00:00' }).success).toBe(false);
    expect(weekSchema.safeParse({ ...valid, published_at: 'not-a-date' }).success).toBe(false);
    expect(weekSchema.safeParse({ ...valid, published_at: '2026-02-31T00:00:00Z' }).success).toBe(false);
  });
});
