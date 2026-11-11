export function json(body: unknown, status: number, headers?: Headers): Response {
  const next = headers ?? new Headers();
  next.set('content-type', 'application/json; charset=utf-8');
  next.set('cache-control', 'no-store');
  return new Response(JSON.stringify(body), { status, headers: next });
}

export function notConfigured(kind: 'json' | 'html'): Response {
  if (kind === 'json') {
    return json({ error: 'not configured' }, 503);
  }
  return new Response('<!doctype html><meta charset="utf-8"><title>Not configured</title><p>not configured</p>', {
    status: 503,
    headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' },
  });
}

export function html(body: string, status: number, headers?: Headers): Response {
  const next = headers ?? new Headers();
  next.set('content-type', 'text/html; charset=utf-8');
  next.set('cache-control', 'no-store');
  return new Response(body, { status, headers: next });
}

export function forbiddenHtml(): Response {
  return html('<!doctype html><meta charset="utf-8"><title>Forbidden</title><p>Forbidden</p>', 403);
}
