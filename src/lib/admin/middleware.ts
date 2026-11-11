import { csrfMatches, originAllowed } from './csrf';
import { readConfig, type PagesEnv } from './env';
import { json, notConfigured, securityHeaders } from './http';
import { sessionFromRequest } from './session';

export interface PagesContext {
  request: Request;
  env: PagesEnv;
  next: () => Promise<Response>;
}

export function routeKind(pathname: string): 'admin' | 'api' | 'public' {
  const path = pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
  if (path === '/admin' || path.startsWith('/admin/')) {
    return 'admin';
  }
  if (path === '/api' || path.startsWith('/api/')) {
    return 'api';
  }
  return 'public';
}

/** Session gate for /admin and /api. Public pages are passed through. */
export async function handleMiddleware(context: PagesContext, now: Date = new Date()): Promise<Response> {
  const kind = routeKind(new URL(context.request.url).pathname);
  if (kind === 'public') {
    return context.next();
  }
  const config = readConfig(context.env);
  if (!config) {
    return notConfigured(kind === 'api' ? 'json' : 'html');
  }
  const session = await sessionFromRequest(context.request, config.sessionSecret, now);
  if (!session) {
    if (kind === 'admin') {
      return new Response(null, {
        status: 302,
        headers: securityHeaders(
          new Headers({
            location: new URL('/auth/login', context.request.url).href,
            'cache-control': 'no-store',
          }),
        ),
      });
    }
    return json({ error: 'Sign in required.' }, 401);
  }
  if (kind === 'api' && context.request.method === 'POST') {
    if (!originAllowed(context.request)) {
      return json({ error: 'Origin mismatch.' }, 403);
    }
    const ok = await csrfMatches(config.sessionSecret, session, context.request.headers.get('x-csrf-token'));
    if (!ok) {
      return json({ error: 'CSRF token missing or invalid.' }, 403);
    }
  }
  return context.next();
}
