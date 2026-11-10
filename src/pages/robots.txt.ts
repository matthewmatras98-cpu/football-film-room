import type { APIRoute } from 'astro';

/** Allows every crawler and points at the sitemap for the configured site. */
export const GET: APIRoute = ({ site }) => {
  const lines = ['User-agent: *', 'Allow: /'];
  if (site) {
    lines.push('', `Sitemap: ${new URL('/sitemap-index.xml', site).href}`);
  }
  return new Response(`${lines.join('\n')}\n`, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
};
