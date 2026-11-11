import { describe, expect, it } from 'vitest';
import type { AdminData } from '../src/lib/admin/admin-data';
import { signToken } from '../src/lib/admin/cookie';
import { csrfForSession, issueState } from '../src/lib/admin/csrf';
import { DISPATCH_KEYS, DISPATCH_URL, isUuidV4 } from '../src/lib/admin/dispatch';
import type { HandlerDeps } from '../src/lib/admin/handlers';
import { handleCallback, handleLogin, handlePick, handleVoid, OAUTH_REDIRECT_URI } from '../src/lib/admin/handlers';
import { prefillLine } from '../src/lib/admin/line';
import { gameMenuLabel } from '../src/lib/admin/label';
import { handleMiddleware } from '../src/lib/admin/middleware';
import { OWNER_GITHUB_ID, OWNER_LOGIN } from '../src/lib/admin/owner';
import { issueSession, readSession, SESSION_TTL_SEC } from '../src/lib/admin/session';
import type { PagesEnv } from '../src/lib/admin/env';

const SECRET = 'session-secret';
const KICKOFF = '2026-11-22T18:00:00.000Z';
const KICKOFF_MS = Date.parse(KICKOFF);

const env: PagesEnv = {
  GITHUB_OAUTH_CLIENT_ID: 'client-id',
  GITHUB_OAUTH_CLIENT_SECRET: 'client-secret',
  GH_DISPATCH_TOKEN: 'dispatch-token',
  SESSION_SECRET: SECRET,
};

function schedule(picks: AdminData['picks'] = []): AdminData {
  return {
    open_week: { season: 2026, week: 12 },
    games: [
      {
        game_id: '2026_12_AWY_HOM',
        season: 2026,
        week: 12,
        away: 'AWY',
        home: 'HOM',
        kickoff_utc: KICKOFF,
        home_spread: -3.5,
        away_spread: null,
        total: 45.5,
      },
      {
        game_id: '2026_11_OLD_NEW',
        season: 2026,
        week: 11,
        away: 'OLD',
        home: 'NEW',
        kickoff_utc: KICKOFF,
        home_spread: 1,
        away_spread: null,
        total: 41,
      },
    ],
    picks,
  };
}

function post(path: string, body: unknown, headers: { cookie?: string; csrf?: string; origin?: string | null }) {
  const init = new Headers({ 'content-type': 'application/json' });
  if (headers.origin !== null) {
    init.set('origin', headers.origin ?? 'https://film.example');
  }
  if (headers.cookie) {
    init.set('cookie', headers.cookie);
  }
  if (headers.csrf) {
    init.set('x-csrf-token', headers.csrf);
  }
  return new Request(`https://film.example${path}`, {
    method: 'POST',
    headers: init,
    body: JSON.stringify(body),
  });
}

async function authed(now: Date) {
  const issued = await issueSession(SECRET, OWNER_GITHUB_ID, OWNER_LOGIN, now);
  const csrf = await csrfForSession(SECRET, issued.session);
  return { cookie: `ffr_session=${encodeURIComponent(issued.token)}`, csrf };
}

function deps(nowMs: number, data: AdminData, fetchImpl: typeof fetch, uuid = () => crypto.randomUUID()): HandlerDeps {
  return {
    fetch: fetchImpl,
    now: () => new Date(nowMs),
    uuid,
    loadAdminData: async () => data,
  };
}

describe('session', () => {
  it('signs a 12 hour payload and rejects tampered or expired cookies', async () => {
    const now = new Date('2026-11-11T12:00:00.000Z');
    const issued = await issueSession(SECRET, OWNER_GITHUB_ID, OWNER_LOGIN, now);
    expect(await readSession(SECRET, issued.token, now)).toEqual(issued.session);
    expect(issued.session.exp - Math.floor(now.getTime() / 1000)).toBe(SESSION_TTL_SEC);

    const [payload, signature] = issued.token.split('.');
    const flipped = signature?.endsWith('a') ? `${signature.slice(0, -1)}b` : `${signature?.slice(0, -1)}a`;
    expect(await readSession(SECRET, `${payload}.${flipped}`, now)).toBeNull();

    const decoded = JSON.parse(Buffer.from(payload ?? '', 'base64url').toString('utf8')) as { login: string };
    decoded.login = 'someone-else';
    const forged = `${Buffer.from(JSON.stringify(decoded)).toString('base64url')}.${signature}`;
    expect(await readSession(SECRET, forged, now)).toBeNull();

    const expired = await signToken(SECRET, JSON.stringify({ uid: OWNER_GITHUB_ID, login: OWNER_LOGIN, exp: 100 }));
    const stillValid = await readSession(SECRET, expired, new Date(99 * 1000));
    const justExpired = await readSession(SECRET, expired, new Date(100 * 1000));
    expect(stillValid?.uid).toBe(OWNER_GITHUB_ID);
    expect(justExpired).toBeNull();
  });
});

describe('owner gate', () => {
  async function callback(login: string, id: number) {
    const now = new Date('2026-11-11T12:00:00.000Z');
    const issued = await issueState(SECRET, now);
    const request = new Request(
      `https://film.example/auth/callback?code=abc&state=${encodeURIComponent(issued.state)}`,
      { headers: { cookie: `ffr_oauth_state=${encodeURIComponent(issued.token)}` } },
    );
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('access_token')) {
        const sent = JSON.parse(String(init?.body ?? '{}')) as { redirect_uri?: string };
        if (sent.redirect_uri !== OAUTH_REDIRECT_URI) {
          throw new Error('redirect_uri');
        }
        return new Response(JSON.stringify({ access_token: 'test-access-token' }), { status: 200 });
      }
      return new Response(JSON.stringify({ login, id }), { status: 200 });
    }) as typeof fetch;
    return handleCallback(request, env, {
      fetch: fetchImpl,
      now: () => now,
      uuid: () => crypto.randomUUID(),
      loadAdminData: async () => schedule(),
    });
  }

  it('rejects a different GitHub login or id with 403 and no session', async () => {
    for (const [login, id] of [
      ['octocat', OWNER_GITHUB_ID],
      [OWNER_LOGIN, OWNER_GITHUB_ID + 1],
    ] as const) {
      const response = await callback(login, id);
      expect(response.status).toBe(403);
      expect(response.headers.getSetCookie().some((cookie) => cookie.startsWith('ffr_session='))).toBe(false);
    }
  });

  it('returns same-origin HTML after setting the strict session cookie', async () => {
    const response = await callback(OWNER_LOGIN, OWNER_GITHUB_ID);
    expect(response.status).toBe(200);
    expect(response.headers.get('location')).toBeNull();
    const body = await response.text();
    expect(body).toContain('<meta http-equiv="refresh" content="0;url=/admin/pick">');
    expect(body).toContain('<a href="/admin/pick">');
    expect(body).toContain('noindex');
    expect(body).not.toContain('<script');
    expect(response.headers.get('x-content-type-options')).toBe('nosniff');
    expect(response.headers.get('referrer-policy')).toBe('same-origin');
    expect(response.headers.get('x-frame-options')).toBe('DENY');
    const session = response.headers.getSetCookie().find((cookie) => cookie.startsWith('ffr_session='));
    expect(session).toMatch(/HttpOnly/i);
    expect(session).toMatch(/Secure/i);
    expect(session).toMatch(/SameSite=Strict/i);
    expect(session).toMatch(/Path=\//);
  });

  it('uses the production OAuth callback on login', async () => {
    const response = await handleLogin(new Request('https://preview.example/auth/login'), env);
    const location = new URL(response.headers.get('location') ?? '');
    expect(location.searchParams.get('redirect_uri')).toBe(OAUTH_REDIRECT_URI);
  });
});

describe('post guards', () => {
  const now = new Date(KICKOFF_MS - 11 * 60 * 1000);
  const body = { game_id: '2026_12_AWY_HOM', market: 'spread', side: 'AWY', line: '', confidence: '', writeup: '' };

  it('returns 403 when the CSRF token is missing or wrong', async () => {
    const auth = await authed(now);
    const missing = await handlePick(post('/api/pick', body, { cookie: auth.cookie, origin: 'https://film.example' }), env, deps(now.getTime(), schedule(), fetch));
    expect(missing.status).toBe(403);
    const wrong = await handlePick(
      post('/api/pick', body, { cookie: auth.cookie, csrf: 'not-the-token', origin: 'https://film.example' }),
      env,
      deps(now.getTime(), schedule(), fetch),
    );
    expect(wrong.status).toBe(403);
  });

  it('returns 403 when the Origin header is missing', async () => {
    const auth = await authed(now);
    const response = await handlePick(
      post('/api/pick', body, { cookie: auth.cookie, csrf: auth.csrf, origin: null }),
      env,
      deps(now.getTime(), schedule(), fetch),
    );
    expect(response.status).toBe(403);
  });

  it('returns 403 when Origin does not match the request host', async () => {
    const auth = await authed(now);
    const response = await handlePick(
      post('/api/pick', body, { cookie: auth.cookie, csrf: auth.csrf, origin: 'https://evil.example' }),
      env,
      deps(now.getTime(), schedule(), fetch),
    );
    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ error: 'Origin mismatch.' });
  });
});

describe('lock and dispatch', () => {
  const pickBody = {
    game_id: '2026_12_AWY_HOM',
    market: 'spread',
    side: 'AWY',
    line: -3.5,
    confidence: 2,
    writeup: 'A\u0000B\r\nC',
  };

  function capture() {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init: init ?? {} });
      return new Response(null, { status: 204 });
    }) as typeof fetch;
    return { calls, fetchImpl };
  }

  function inputsOf(call: { init: RequestInit }) {
    const headers = new Headers(call.init.headers);
    const payload = JSON.parse(String(call.init.body)) as { ref: string; inputs: Record<string, string> };
    return { headers, payload };
  }

  it('accepts 11 minutes before kickoff and rejects the 10 minute mark', async () => {
    const early = KICKOFF_MS - 11 * 60 * 1000;
    const auth = await authed(new Date(early));
    const { calls, fetchImpl } = capture();
    const response = await handlePick(
      post('/api/pick', pickBody, auth),
      env,
      deps(early, schedule(), fetchImpl),
    );
    expect(response.status).toBe(202);
    const saved = (await response.json()) as { request_id: string };
    expect(isUuidV4(saved.request_id)).toBe(true);
    expect(isUuidV4(crypto.randomUUID())).toBe(true);

    expect(calls).toHaveLength(1);
    const call = calls[0];
    if (!call) {
      throw new Error('missing dispatch');
    }
    expect(call.url).toBe(DISPATCH_URL);
    expect(call.init.method).toBe('POST');
    const { headers, payload } = inputsOf(call);
    expect(headers.get('authorization')).toBe('Bearer dispatch-token');
    expect(payload.ref).toBe('main');
    expect(Object.keys(payload.inputs)).toEqual([...DISPATCH_KEYS]);
    expect(Object.values(payload.inputs).every((value) => typeof value === 'string')).toBe(true);
    expect(payload.inputs).not.toHaveProperty('pick_id');
    expect(payload.inputs.action).toBe('pick');
    expect(payload.inputs.request_id).toBe(saved.request_id);
    expect(payload.inputs.game_id).toBe('2026_12_AWY_HOM');
    expect(payload.inputs.market).toBe('spread');
    expect(payload.inputs.side).toBe('AWY');
    expect(payload.inputs.line).toBe('-3.5');
    expect(payload.inputs.confidence).toBe('2');
    expect(payload.inputs.writeup).toBe('AB\nC');
    expect(payload.inputs.server_received_at).toBe(new Date(early).toISOString());
    expect(payload.inputs.server_received_at.endsWith('Z')).toBe(true);
    expect(payload.inputs.void_pick_id).toBe('');

    const lateAuth = await authed(new Date(KICKOFF_MS - 10 * 60 * 1000));
    const lateCalls = capture();
    const late = await handlePick(
      post('/api/pick', pickBody, lateAuth),
      env,
      deps(KICKOFF_MS - 10 * 60 * 1000, schedule(), lateCalls.fetchImpl),
    );
    expect(late.status).toBe(409);
    expect(lateCalls.calls).toHaveLength(0);
  });

  it('rejects a game and market that already has a pick, including a void', async () => {
    const now = KICKOFF_MS - 11 * 60 * 1000;
    const auth = await authed(new Date(now));
    const { calls, fetchImpl } = capture();
    const response = await handlePick(
      post('/api/pick', pickBody, auth),
      env,
      deps(
        now,
        schedule([
          {
            pick_id: '2026_12_AWY_HOM-spread',
            game_id: '2026_12_AWY_HOM',
            market: 'spread',
            side: 'HOM',
            status: 'void',
            kickoff_utc: KICKOFF,
          },
        ]),
        fetchImpl,
      ),
    );
    expect(response.status).toBe(409);
    expect(calls).toHaveLength(0);
  });

  it('dispatches a void with the same ten string keys', async () => {
    const now = KICKOFF_MS - 11 * 60 * 1000;
    const auth = await authed(new Date(now));
    const { calls, fetchImpl } = capture();
    const response = await handleVoid(
      post('/api/void', { pick_id: '2026_12_AWY_HOM-total' }, auth),
      env,
      deps(
        now,
        schedule([
          {
            pick_id: '2026_12_AWY_HOM-total',
            game_id: '2026_12_AWY_HOM',
            market: 'total',
            side: 'over',
            status: 'pending',
            kickoff_utc: KICKOFF,
          },
        ]),
        fetchImpl,
      ),
    );
    expect(response.status).toBe(202);
    const saved = (await response.json()) as { request_id: string };
    expect(isUuidV4(saved.request_id)).toBe(true);
    const call = calls[0];
    if (!call) {
      throw new Error('missing dispatch');
    }
    expect(call.url).toBe(DISPATCH_URL);
    const { headers, payload } = inputsOf(call);
    expect(headers.get('authorization')).toBe('Bearer dispatch-token');
    expect(payload.ref).toBe('main');
    expect(Object.keys(payload.inputs)).toEqual([...DISPATCH_KEYS]);
    expect(Object.values(payload.inputs).every((value) => typeof value === 'string')).toBe(true);
    expect(payload.inputs.action).toBe('void');
    expect(payload.inputs.request_id).toBe(saved.request_id);
    expect(payload.inputs.void_pick_id).toBe('2026_12_AWY_HOM-total');
    expect(payload.inputs.server_received_at.endsWith('Z')).toBe(true);
    expect(payload.inputs.game_id).toBe('');
    expect(payload.inputs.market).toBe('');
    expect(payload.inputs.side).toBe('');
    expect(payload.inputs.line).toBe('');
    expect(payload.inputs.confidence).toBe('');
    expect(payload.inputs.writeup).toBe('');
  });
});

describe('routes without secrets', () => {
  it('answers 503 not configured instead of throwing', async () => {
    const login = await handleLogin(new Request('https://film.example/auth/login'), {});
    expect(login.status).toBe(503);
    const loginHtml = await login.text();
    expect(loginHtml).toContain('not configured');
    expect(loginHtml).toContain('noindex');

    const pick = await handlePick(new Request('https://film.example/api/pick', { method: 'POST' }), {});
    expect(pick.status).toBe(503);
    expect(await pick.json()).toMatchObject({ error: 'not configured' });
  });

  it('leaves public pages alone and sends anonymous admin traffic to login', async () => {
    const pub = await handleMiddleware({
      request: new Request('https://film.example/picks/'),
      env: {},
      next: async () => new Response('public'),
    });
    expect(await pub.text()).toBe('public');

    const admin = await handleMiddleware({
      request: new Request('https://film.example/admin/pick'),
      env,
      next: async () => new Response('secret'),
    });
    expect(admin.status).toBe(302);
    expect(new URL(admin.headers.get('location') ?? '').pathname).toBe('/auth/login');
  });
});

describe('spread prefill', () => {
  it('uses the home number for a home pick and the negation for an away pick', () => {
    const shared = { home: 'NYJ', away: 'BUF', homeSpread: 8, total: 50.5 };
    expect(prefillLine({ ...shared, market: 'spread', side: 'NYJ' })).toBe('8');
    expect(prefillLine({ ...shared, market: 'spread', side: 'BUF' })).toBe('-8');
    expect(prefillLine({ ...shared, market: 'total', side: 'over' })).toBe('50.5');
    expect(prefillLine({ ...shared, market: 'spread', side: 'BUF', awaySpread: -7 })).toBe('-7');
    expect(prefillLine({ ...shared, market: 'spread', side: 'BUF', homeSpread: 0 })).toBe('0');
  });
});

describe('game menu label', () => {
  it('uses the program clock in MST', () => {
    expect(gameMenuLabel({ away: 'AWY', home: 'HOM', kickoff_utc: '2026-11-22T18:00:00Z' })).toBe(
      'AWY @ HOM, Sun 11/22 11:00 AM MST',
    );
  });
});
