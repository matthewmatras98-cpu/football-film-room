const textEncoder = new TextEncoder();

export function encodeBase64Url(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

export function decodeBase64Url(value: string): Uint8Array | null {
  if (!/^[-_A-Za-z0-9]+$/.test(value)) {
    return null;
  }
  const padded = value.replace(/-/g, '+').replace(/_/g, '/');
  const pad = padded.length % 4 === 0 ? '' : '='.repeat(4 - (padded.length % 4));
  try {
    const binary = atob(padded + pad);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  } catch {
    return null;
  }
}

/** Constant-time compare. Length differences still return false. */
export function timingSafeEqual(left: Uint8Array, right: Uint8Array): boolean {
  const length = Math.max(left.length, right.length);
  let diff = left.length ^ right.length;
  for (let index = 0; index < length; index += 1) {
    diff |= (left[index] ?? 0) ^ (right[index] ?? 0);
  }
  return diff === 0;
}

export function timingSafeEqualString(left: string, right: string): boolean {
  return timingSafeEqual(textEncoder.encode(left), textEncoder.encode(right));
}

export async function hmacBytes(secret: string, message: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    'raw',
    textEncoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign('HMAC', key, textEncoder.encode(message));
  return new Uint8Array(signature);
}

/** `payload.signature`, both base64url. The signature covers the payload segment. */
export async function signToken(secret: string, payloadJson: string): Promise<string> {
  const payload = encodeBase64Url(textEncoder.encode(payloadJson));
  const signature = await hmacBytes(secret, payload);
  return `${payload}.${encodeBase64Url(signature)}`;
}

/** Returns the JSON payload when the signature matches. */
export async function verifyToken(secret: string, token: string): Promise<string | null> {
  const parts = token.split('.');
  if (parts.length !== 2) {
    return null;
  }
  const payload = parts[0] ?? '';
  const signature = parts[1] ?? '';
  if (!payload || !signature) {
    return null;
  }
  const provided = decodeBase64Url(signature);
  if (!provided) {
    return null;
  }
  const expected = await hmacBytes(secret, payload);
  if (!timingSafeEqual(provided, expected)) {
    return null;
  }
  const jsonBytes = decodeBase64Url(payload);
  if (!jsonBytes) {
    return null;
  }
  return new TextDecoder().decode(jsonBytes);
}

export function readCookie(header: string | null, name: string): string | null {
  if (!header) {
    return null;
  }
  for (const part of header.split(';')) {
    const eq = part.indexOf('=');
    if (eq === -1) {
      continue;
    }
    if (part.slice(0, eq).trim() !== name) {
      continue;
    }
    const raw = part.slice(eq + 1).trim();
    try {
      return decodeURIComponent(raw);
    } catch {
      return null;
    }
  }
  return null;
}

export function setCookie(
  name: string,
  value: string,
  options: { maxAge: number; sameSite: 'Lax' | 'Strict' },
): string {
  return `${name}=${encodeURIComponent(value)}; HttpOnly; Secure; SameSite=${options.sameSite}; Path=/; Max-Age=${options.maxAge}`;
}

export function clearCookie(name: string, sameSite: 'Lax' | 'Strict'): string {
  return `${name}=; HttpOnly; Secure; SameSite=${sameSite}; Path=/; Max-Age=0`;
}
