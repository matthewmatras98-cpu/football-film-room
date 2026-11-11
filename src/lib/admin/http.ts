/**
 * Linked stylesheets only: the built CSS is one same-origin file (well over the
 * inline threshold) and there are no style attributes. Fonts are same-origin woff2,
 * so style-src stays 'self' without 'unsafe-inline'.
 */
export const CONTENT_SECURITY_POLICY =
  "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; form-action 'self' https://github.com; frame-ancestors 'none'; base-uri 'none'; object-src 'none'";

export function securityHeaders(headers?: Headers): Headers {
  const next = headers ?? new Headers();
  next.set('x-content-type-options', 'nosniff');
  next.set('referrer-policy', 'same-origin');
  next.set('x-frame-options', 'DENY');
  if (!next.has('cache-control')) {
    next.set('cache-control', 'no-store');
  }
  return next;
}

export function json(body: unknown, status: number, headers?: Headers): Response {
  const next = securityHeaders(headers);
  next.set('content-type', 'application/json; charset=utf-8');
  next.set('cache-control', 'no-store');
  return new Response(JSON.stringify(body), { status, headers: next });
}

const NOT_CONFIGURED_HTML =
  '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><title>Not configured</title><p>not configured</p>';

export function notConfigured(kind: 'json' | 'html'): Response {
  if (kind === 'json') {
    return json({ error: 'not configured' }, 503);
  }
  return html(NOT_CONFIGURED_HTML, 503);
}

export function html(body: string, status: number, headers?: Headers): Response {
  const next = securityHeaders(headers);
  next.set('content-type', 'text/html; charset=utf-8');
  next.set('content-security-policy', CONTENT_SECURITY_POLICY);
  next.set('cache-control', 'no-store');
  return new Response(body, { status, headers: next });
}

export function forbiddenHtml(): Response {
  return html(
    '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><title>Forbidden</title><p>Forbidden</p>',
    403,
  );
}
