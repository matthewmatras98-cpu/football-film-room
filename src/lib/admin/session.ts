import { readCookie, signToken, verifyToken } from './cookie';

export const SESSION_COOKIE = 'ffr_session';
export const SESSION_TTL_SEC = 12 * 60 * 60;

export interface Session {
  uid: number;
  login: string;
  exp: number;
}

export async function issueSession(
  secret: string,
  uid: number,
  login: string,
  now: Date,
): Promise<{ token: string; session: Session }> {
  const session: Session = {
    uid,
    login,
    exp: Math.floor(now.getTime() / 1000) + SESSION_TTL_SEC,
  };
  const token = await signToken(secret, JSON.stringify(session));
  return { token, session };
}

export async function readSession(secret: string, token: string, now: Date): Promise<Session | null> {
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
  if (typeof record.uid !== 'number' || !Number.isInteger(record.uid)) {
    return null;
  }
  if (typeof record.login !== 'string' || record.login.length === 0) {
    return null;
  }
  if (typeof record.exp !== 'number' || !Number.isFinite(record.exp)) {
    return null;
  }
  if (Math.floor(now.getTime() / 1000) >= record.exp) {
    return null;
  }
  return { uid: record.uid, login: record.login, exp: record.exp };
}

export async function sessionFromRequest(request: Request, secret: string, now: Date): Promise<Session | null> {
  const token = readCookie(request.headers.get('cookie'), SESSION_COOKIE);
  if (!token) {
    return null;
  }
  return readSession(secret, token, now);
}
