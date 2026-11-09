import { beforeEach, describe, expect, it, vi } from 'vitest';

const getCollection = vi.fn();

vi.mock('astro:content', () => ({
  getCollection: (...args: unknown[]) => getCollection(...args),
}));

const { formatPublishedAt, getPublishedWeeks } = await import('../src/lib/posts');

function entry(id: string, publishedAt: string, draft: boolean) {
  return {
    id,
    data: {
      title: id,
      season: 2026,
      week: 1,
      published_at: publishedAt,
      summary: 'Notes.',
      picks: [] as string[],
      tags: [] as string[],
      draft,
    },
  };
}

describe('formatPublishedAt', () => {
  it('formats an offset timestamp in Phoenix time', () => {
    const formatted = formatPublishedAt('2026-11-14T18:00:00-07:00').replace(/[\u00a0\u202f]/g, ' ');
    expect(formatted).toBe('Nov 14, 2026, 6:00 PM MST');
  });
});

describe('getPublishedWeeks', () => {
  beforeEach(() => {
    getCollection.mockReset();
  });

  it('drops drafts and sorts newest first', async () => {
    getCollection.mockResolvedValue([
      entry('older', '2026-09-10T12:00:00-07:00', false),
      entry('hidden', '2026-12-01T12:00:00-07:00', true),
      entry('newer', '2026-11-14T18:00:00-07:00', false),
    ]);

    const posts = await getPublishedWeeks();

    expect(getCollection).toHaveBeenCalledWith('weeks');
    expect(posts.map((post) => post.id)).toEqual(['newer', 'older']);
    expect(posts.every((post) => post.data.draft === false)).toBe(true);
  });
});
