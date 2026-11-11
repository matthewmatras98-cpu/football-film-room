import { parseAdminData, type AdminData } from './admin-data';
import { clearCookie, setCookie, timingSafeEqualString } from './cookie';
import { csrfForSession, csrfMatches, issueState, originAllowed, readState, stateCookie, STATE_COOKIE, STATE_TTL_SEC } from './csrf';
import {
  githubHeaders,
  isUuidV4,
  pickInputs,
  RUNS_URL,
  sendDispatch,
  stateFromRuns,
  voidInputs,
  type WorkflowRun,
} from './dispatch';
import { readConfig, type PagesEnv } from './env';
import { forbiddenHtml, html, json, notConfigured, securityHeaders } from './http';
import { isOwner } from './owner';
import { issueSession, sessionFromRequest, SESSION_COOKIE, SESSION_TTL_SEC } from './session';
import { selectableGames } from './open-week';
import { validatePick, validateVoid } from './validate';

export interface HandlerDeps {
  fetch: typeof fetch;
  now: () => Date;
  uuid: () => string;
  loadAdminData: (request: Request, env: PagesEnv) => Promise<AdminData | null>;
}

export function defaultDeps(env: PagesEnv): HandlerDeps {
  return {
    fetch: globalThis.fetch.bind(globalThis),
    now: () => new Date(),
    uuid: () => crypto.randomUUID(),
    loadAdminData: (request) => loadAdminDataFromAssets(request, env),
  };
}

export async function loadAdminDataFromAssets(request: Request, env: PagesEnv): Promise<AdminData | null> {
  if (!env.ASSETS) {
    return null;
  }
  try {
    const url = new URL('/admin-data.json', request.url);
    const response = await env.ASSETS.fetch(new Request(url));
    if (!response.ok) {
      return null;
    }
    return parseAdminData(await response.json());
  } catch {
    return null;
  }
}

export const OAUTH_REDIRECT_URI = 'https://football-film-room.pages.dev/auth/callback';

const SIGNED_IN_HTML =
  '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><meta http-equiv="refresh" content="1;url=/admin/pick"><title>Signed in</title><p><a href="/admin/pick">Continue to pick page</a></p><script src="/auth-continue.js"></script>';

function redirect(location: string, cookies: string[] = []): Response {
  const headers = securityHeaders(new Headers({ location, 'cache-control': 'no-store' }));
  for (const cookie of cookies) {
    headers.append('set-cookie', cookie);
  }
  return new Response(null, { status: 302, headers });
}

export async function handleLogin(_request: Request, env: PagesEnv, deps: HandlerDeps = defaultDeps(env)): Promise<Response> {
  const config = readConfig(env);
  if (!config) {
    return notConfigured('html');
  }
  const { state, token } = await issueState(config.sessionSecret, deps.now());
  const authorize = new URL('https://github.com/login/oauth/authorize');
  authorize.searchParams.set('client_id', config.clientId);
  authorize.searchParams.set('redirect_uri', OAUTH_REDIRECT_URI);
  authorize.searchParams.set('scope', 'read:user');
  authorize.searchParams.set('state', state);
  return redirect(authorize.href, [setCookie(STATE_COOKIE, token, { maxAge: STATE_TTL_SEC, sameSite: 'Lax' })]);
}

async function githubUser(
  accessToken: string,
  fetchImpl: typeof fetch,
): Promise<{ ok: true; login: unknown; id: unknown } | { ok: false }> {
  try {
    const response = await fetchImpl('https://api.github.com/user', {
      redirect: 'manual',
      headers: {
        accept: 'application/vnd.github+json',
        authorization: `Bearer ${accessToken}`,
        'user-agent': 'football-film-room',
      },
    });
    if (!response.ok) {
      return { ok: false };
    }
    const body: unknown = await response.json();
    if (!body || typeof body !== 'object') {
      return { ok: false };
    }
    const record = body as Record<string, unknown>;
    return { ok: true, login: record.login, id: record.id };
  } catch {
    return { ok: false };
  }
}

export async function handleCallback(
  request: Request,
  env: PagesEnv,
  deps: HandlerDeps = defaultDeps(env),
): Promise<Response> {
  const config = readConfig(env);
  if (!config) {
    return notConfigured('html');
  }
  const url = new URL(request.url);
  const code = url.searchParams.get('code');
  const state = url.searchParams.get('state');
  const stateToken = stateCookie(request);
  if (!code || !state || !stateToken) {
    return forbiddenHtml();
  }
  const expected = await readState(config.sessionSecret, stateToken, deps.now());
  if (!expected || !timingSafeEqualString(expected, state)) {
    return forbiddenHtml();
  }

  let accessToken = '';
  try {
    const tokenResponse = await deps.fetch('https://github.com/login/oauth/access_token', {
      method: 'POST',
      redirect: 'manual',
      headers: {
        accept: 'application/json',
        'content-type': 'application/json',
        'user-agent': 'football-film-room',
      },
      body: JSON.stringify({
        client_id: config.clientId,
        client_secret: config.clientSecret,
        code,
        redirect_uri: OAUTH_REDIRECT_URI,
      }),
    });
    if (!tokenResponse.ok) {
      return html('<!doctype html><meta charset="utf-8"><title>Sign-in failed</title><p>Sign-in failed.</p>', 502);
    }
    const tokenBody: unknown = await tokenResponse.json();
    const candidate =
      tokenBody && typeof tokenBody === 'object' ? (tokenBody as { access_token?: unknown }).access_token : undefined;
    if (typeof candidate !== 'string' || candidate.length === 0) {
      return html('<!doctype html><meta charset="utf-8"><title>Sign-in failed</title><p>Sign-in failed.</p>', 502);
    }
    accessToken = candidate;
  } catch {
    return html('<!doctype html><meta charset="utf-8"><title>Sign-in failed</title><p>Sign-in failed.</p>', 502);
  }

  const profile = await githubUser(accessToken, deps.fetch);
  if (!profile.ok) {
    return html('<!doctype html><meta charset="utf-8"><title>Sign-in failed</title><p>Sign-in failed.</p>', 502);
  }
  if (!isOwner(profile.login, profile.id) || typeof profile.id !== 'number' || typeof profile.login !== 'string') {
    return forbiddenHtml();
  }
  const issued = await issueSession(config.sessionSecret, profile.id, profile.login, deps.now());
  const headers = new Headers();
  headers.append('set-cookie', setCookie(SESSION_COOKIE, issued.token, { maxAge: SESSION_TTL_SEC, sameSite: 'Strict' }));
  headers.append('set-cookie', clearCookie(STATE_COOKIE, 'Lax'));
  return html(SIGNED_IN_HTML, 200, headers);
}

export function handleLogout(request: Request): Response {
  return redirect(new URL('/', request.url).href, [
    clearCookie(SESSION_COOKIE, 'Strict'),
    clearCookie(STATE_COOKIE, 'Lax'),
  ]);
}

async function guardApi(
  request: Request,
  env: PagesEnv,
  now: Date,
  csrf: boolean,
): Promise<{ sessionSecret: string; dispatchToken: string; login: string; uid: number; exp: number } | Response> {
  const config = readConfig(env);
  if (!config) {
    return notConfigured('json');
  }
  const session = await sessionFromRequest(request, config.sessionSecret, now);
  if (!session) {
    return json({ error: 'Sign in required.' }, 401);
  }
  if (csrf) {
    if (!originAllowed(request)) {
      return json({ error: 'Origin mismatch.' }, 403);
    }
    const header = request.headers.get('x-csrf-token');
    if (!(await csrfMatches(config.sessionSecret, session, header))) {
      return json({ error: 'CSRF token missing or invalid.' }, 403);
    }
  }
  return {
    sessionSecret: config.sessionSecret,
    dispatchToken: config.dispatchToken,
    login: session.login,
    uid: session.uid,
    exp: session.exp,
  };
}

export async function handleSession(
  request: Request,
  env: PagesEnv,
  deps: HandlerDeps = defaultDeps(env),
): Promise<Response> {
  const guard = await guardApi(request, env, deps.now(), false);
  if (guard instanceof Response) {
    return guard;
  }
  const csrf = await csrfForSession(guard.sessionSecret, { uid: guard.uid, login: guard.login, exp: guard.exp });
  return json({ login: guard.login, csrf }, 200);
}

async function readJsonBody(request: Request): Promise<{ ok: true; body: unknown } | { ok: false; response: Response }> {
  const text = await request.text();
  if (text.length > 20_000) {
    return { ok: false, response: json({ error: 'Body is too large.' }, 422) };
  }
  try {
    return { ok: true, body: JSON.parse(text) };
  } catch {
    return { ok: false, response: json({ error: 'Invalid JSON.' }, 422) };
  }
}

export async function handleSlate(
  request: Request,
  env: PagesEnv,
  deps: HandlerDeps = defaultDeps(env),
): Promise<Response> {
  try {
    const now = deps.now();
    const guard = await guardApi(request, env, now, false);
    if (guard instanceof Response) {
      return guard;
    }
    const data = await deps.loadAdminData(request, env);
    if (!data) {
      return json({ error: 'Schedule unavailable.' }, 500);
    }
    return json({ games: selectableGames(data.games, now) }, 200);
  } catch {
    return json({ error: 'internal error' }, 500);
  }
}

export async function handlePick(
  request: Request,
  env: PagesEnv,
  deps: HandlerDeps = defaultDeps(env),
): Promise<Response> {
  try {
    const now = deps.now();
    const guard = await guardApi(request, env, now, true);
    if (guard instanceof Response) {
      return guard;
    }
    const parsedBody = await readJsonBody(request);
    if (!parsedBody.ok) {
      return parsedBody.response;
    }
    const data = await deps.loadAdminData(request, env);
    if (!data) {
      return json({ error: 'Schedule unavailable.' }, 500);
    }
    const parsed = validatePick(parsedBody.body, data, now);
    if (!parsed.ok) {
      return json({ error: parsed.error }, parsed.status);
    }
    const requestId = deps.uuid();
    const receivedAt = now.toISOString();
    if (!isUuidV4(requestId) || !receivedAt.endsWith('Z')) {
      return json({ error: 'Could not record the submission.' }, 500);
    }
    const sent = await sendDispatch(
      guard.dispatchToken,
      pickInputs(parsed.value, { requestId, receivedAt }),
      deps.fetch,
    );
    if (!sent.ok) {
      return json({ error: 'Dispatch failed.' }, 502);
    }
    return json({ request_id: requestId }, 202);
  } catch {
    return json({ error: 'internal error' }, 500);
  }
}

export async function handleVoid(
  request: Request,
  env: PagesEnv,
  deps: HandlerDeps = defaultDeps(env),
): Promise<Response> {
  try {
    const now = deps.now();
    const guard = await guardApi(request, env, now, true);
    if (guard instanceof Response) {
      return guard;
    }
    const parsedBody = await readJsonBody(request);
    if (!parsedBody.ok) {
      return parsedBody.response;
    }
    const data = await deps.loadAdminData(request, env);
    if (!data) {
      return json({ error: 'Schedule unavailable.' }, 500);
    }
    const parsed = validateVoid(parsedBody.body, data, now);
    if (!parsed.ok) {
      return json({ error: parsed.error }, parsed.status);
    }
    const requestId = deps.uuid();
    const receivedAt = now.toISOString();
    if (!isUuidV4(requestId) || !receivedAt.endsWith('Z')) {
      return json({ error: 'Could not record the submission.' }, 500);
    }
    const sent = await sendDispatch(
      guard.dispatchToken,
      voidInputs(parsed.value.pick_id, { requestId, receivedAt }),
      deps.fetch,
    );
    if (!sent.ok) {
      return json({ error: 'Dispatch failed.' }, 502);
    }
    return json({ request_id: requestId }, 202);
  } catch {
    return json({ error: 'internal error' }, 500);
  }
}

export async function handleStatus(
  request: Request,
  env: PagesEnv,
  deps: HandlerDeps = defaultDeps(env),
): Promise<Response> {
  try {
    const guard = await guardApi(request, env, deps.now(), false);
    if (guard instanceof Response) {
      return guard;
    }
    const requestId = new URL(request.url).searchParams.get('request_id') ?? '';
    if (!isUuidV4(requestId)) {
      return json({ error: 'request_id must be a UUID v4.' }, 422);
    }
    let response: Response;
    try {
      response = await deps.fetch(RUNS_URL, {
        redirect: 'manual',
        headers: githubHeaders(guard.dispatchToken),
      });
    } catch {
      return json({ error: 'Status unavailable.' }, 502);
    }
    if (!response.ok) {
      return json({ error: 'Status unavailable.' }, 502);
    }
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      return json({ error: 'Status unavailable.' }, 502);
    }
    const runs =
      payload && typeof payload === 'object' && Array.isArray((payload as { workflow_runs?: unknown }).workflow_runs)
        ? ((payload as { workflow_runs: WorkflowRun[] }).workflow_runs)
        : [];
    return json({ state: stateFromRuns(runs, requestId) }, 200);
  } catch {
    return json({ error: 'internal error' }, 500);
  }
}
