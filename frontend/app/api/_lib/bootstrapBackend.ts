/**
 * The one backend authority the server-side bootstrap proxies use.
 *
 * ``/api/v1/auth/*`` and ``/api/entitlements`` must never disagree about which
 * upstream is authoritative, and a request must never be replayed against a
 * second host. The base is selected once per request:
 *
 * 1. ``DAEMON_INTERNAL_API_URL`` when nonempty (server-only override);
 * 2. otherwise ``NEXT_PUBLIC_API_URL`` when nonempty;
 * 3. otherwise ``http://backend:8000``.
 *
 * There is no ``localhost`` candidate and no fallback to another candidate
 * after selection. A selected value that is not an absolute ``http(s)`` base
 * without credentials, query or fragment is invalid: the caller refuses with
 * an opaque 502 instead of trying the next candidate. A trailing slash is
 * trimmed; a path prefix is preserved.
 */

export const DEFAULT_BACKEND_BASE = 'http://backend:8000';

export type BackendSelection =
  | { readonly ok: true; readonly base: string }
  | { readonly ok: false };

const INVALID: BackendSelection = { ok: false };

function firstNonEmpty(...values: (string | undefined)[]): string | undefined {
  for (const value of values) {
    const trimmed = value?.trim();
    if (trimmed) return trimmed;
  }
  return undefined;
}

export function selectBackendBase(
  internal: string | undefined = process.env.DAEMON_INTERNAL_API_URL,
  publicUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL,
): BackendSelection {
  const raw = firstNonEmpty(internal, publicUrl) ?? DEFAULT_BACKEND_BASE;
  // URL.search/hash omit a bare delimiter; it is still a forbidden component.
  if (raw.includes('?') || raw.includes('#')) return INVALID;

  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return INVALID;
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return INVALID;
  }
  if (
    parsed.username !== '' ||
    parsed.password !== '' ||
    parsed.search !== '' ||
    parsed.hash !== ''
  ) {
    return INVALID;
  }

  const base = `${parsed.origin}${parsed.pathname.replace(/\/+$/, '')}`;
  return { ok: true, base };
}
