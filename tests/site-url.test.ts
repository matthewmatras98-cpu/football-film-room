import { describe, expect, it } from 'vitest';
import { DEFAULT_SITE_URL, siteUrl } from '../src/data/site-url.mjs';

describe('siteUrl', () => {
  it('falls back to the pages.dev origin', () => {
    expect(siteUrl({})).toBe(DEFAULT_SITE_URL);
    expect(siteUrl({ SITE_URL: '   ' })).toBe(DEFAULT_SITE_URL);
  });

  it('strips trailing slashes', () => {
    expect(siteUrl({ SITE_URL: 'https://example.com/' })).toBe('https://example.com');
    expect(siteUrl({ SITE_URL: 'https://example.com///' })).toBe('https://example.com');
    expect(siteUrl({ SITE_URL: '  https://example.com/path/  ' })).toBe('https://example.com/path');
  });
});
