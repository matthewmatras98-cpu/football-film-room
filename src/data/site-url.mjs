/** Used when SITE_URL is unset. Trailing slashes are stripped so canonical URLs stay stable. */
export const DEFAULT_SITE_URL = 'https://football-film-room.pages.dev';

export function siteUrl(env = process.env) {
  const raw = typeof env.SITE_URL === 'string' ? env.SITE_URL.trim() : '';
  return (raw || DEFAULT_SITE_URL).replace(/\/+$/, '');
}
