import { encodeBase64Url, hmacBytes, readCookie, signToken, timingSafeEqualString, verifyToken } from './cookie';
import type { Session } from './session';

export const STATE_COOKIE = 'ffr_oauth_state';
export const STATE_TTL_SEC = 10 * 60;

/** HMAC of the session identity. Stable for the life of that cookie. */
export async function csrfForSession(secret: string, session: Session): Promise<string> {
  const message = `csrf|${session.uid}|${session.login}|${session.exp}`;
  return encodeBase64Url(await hmacBytes(secret, message));
}

export async function csrfMatches(secret: string, session: Session, header: string | null): Promise<boolean> {
  if (!header) {
    return false;
  }
  const expected = await csrfForSession(secret, session);
  return timingSafeEqualString(expected, header);
}

/**
 * True when the Origin header's host equals the request host.
 * A missing or unparseable Origin is rejected.
 */
export function originAllowed(request: Request): boolean {
  const origin = request.headers.get('origin');
  if (!origin) {
    return false;
  }
  try {
    return new URL(origin).host === new URL(request.url).host;
  } catch {
    return false;
  }
}

export async function issueState(secret: string, now: Date): Promise<{ state: string; token: string }> {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  const state = encodeBase64Url(bytes);
  const exp = Math.floor(now.getTime() / 1000) + STATE_TTL_SEC;
  const token = await signToken(secret, JSON.stringify({ state, exp }));
  return { state, token };
}

export async function readState(secret: string, token: string, now: Date): Promise<string | null> {
  const json = await verifyToken(secret, token);
  if (json === null) {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') {
    return null;
  }
  const record = parsed as Record<string, unknown>;
  if (typeof record.state !== 'string' || record.state.length === 0) {
    return null;
  }
  if (typeof record.exp !== 'number' || !Number.isFinite(record.exp)) {
    return null;
  }
  if (Math.floor(now.getTime() / 1000) >= record.exp) {
    return null;
  }
  return record.state;
}

export function stateCookie(request: Request): string | null {
  return readCookie(request.headers.get('cookie'), STATE_COOKIE);
}
