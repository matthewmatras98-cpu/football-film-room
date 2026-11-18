import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import type { PickView } from '../src/lib/picks-schema';

vi.mock('astro:content', () => ({
  getCollection: async () => [],
}));

vi.mock('../src/lib/picks', async () => {
  const { readFileSync: read } = await import('node:fs');
  const actual = await vi.importActual<typeof import('../src/lib/picks')>('../src/lib/picks');
  const rows = JSON.parse(read(new URL('./fixtures/picks_view.json', import.meta.url), 'utf8')) as PickView[];
  return { ...actual, picks: rows };
});

const { experimental_AstroContainer: AstroContainer } = await import('astro/container');
const { default: AdminPickPage } = await import('../src/pages/admin/pick.astro');
const { siteUrl } = await import('../src/data/site-url.mjs');

describe('admin void list', () => {
  it('renders fixture voids and the -r2 re-pick on the admin page only', async () => {
    const container = await AstroContainer.create({
      astroConfig: { site: siteUrl() },
    });
    const html = await container.renderToString(AdminPickPage, {
      request: new Request(`${siteUrl()}/admin/pick`),
    });
    const source = readFileSync(new URL('../src/pages/admin/pick.astro', import.meta.url), 'utf8');

    expect(source.match(/<h1\b/g)?.length).toBe(1);
    expect(html.match(/<h1\b/g)?.length).toBe(1);
    const voids = html.slice(html.indexOf('Voided picks'), html.indexOf('Your open picks'));
    expect(voids).toContain('data-void-game="2099_01_ARI_LAR"');
    expect(voids).toContain('data-void-market="spread"');
    expect(voids).toContain('No re-pick');
    expect(voids).toContain('data-void-game="2099_01_DET_PHI"');
    expect(voids).toContain('data-repick="2099_01_DET_PHI-spread-r2"');
    expect(voids).toContain('Re-pick 2099_01_DET_PHI-spread-r2');
    expect(voids).not.toContain('2099_01_KC_BUF-spread');
    expect(voids).not.toContain('2099_01_BAL_PIT-spread');
    expect(voids).not.toContain('2099_01_DET_PHI-total');
  });
});
