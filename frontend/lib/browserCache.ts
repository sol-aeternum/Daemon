'use client';

/**
 * Browser HTTP-cache clearing barrier for trusted browser origins (P0.2).
 *
 * The server endpoint is `POST /v1/auth/cache/clear` (operator-approved):
 * 204 empty response, no bearer/session/database involvement, existing
 * trusted-origin enforcement. The browser must receive the response for the
 * Clear-Site-Data directive to apply, so the client contacts BOTH operated
 * origins:
 *   - the app proxy path (`/api/v1/auth/cache/clear`, app origin), and
 *   - the configured API origin directly (`NEXT_PUBLIC_API_URL` + path
 *     prefix), when it is a distinct origin.
 *
 * Header contract (Astra diagnosis, Chromium 145 measured): Chromium
 * successfully processes `Clear-Site-Data: "cache"` while hiding that header
 * from page JavaScript — even same-origin — so client code must NOT require a
 * JS-readable Clear-Site-Data value. On-wire CSD assertions belong to the
 * browser/proxy fixtures, not here. What client code CAN check lives in the
 * JS-response surface: the exact 204 status plus `Cache-Control: no-store`
 * (a CORS-safelisted response header). Those checks are the trusted
 * endpoint's acknowledgement under the QUALIFIED browser contract — they are
 * NOT a runtime erasure oracle and do not prove any arbitrary browser or
 * configuration erased the cache. Engine/deployment qualification is owned
 * by browser fixtures and docs, never by this module's wording.
 *
 * Qualification enforced here (fail closed, existing CSD requirement):
 *   - the app window must be a secure context (`window.isSecureContext`);
 *   - EACH direct target must be HTTPS or strict loopback HTTP
 *     (`localhost`, `*.localhost`, 127.0.0.0/8, `[::1]`) — hostname suffix
 *     games, userinfo, query/hash/insecure-LAN targets are rejected;
 *   - unqualified lifetimes never issue the request and never open.
 *
 * Approval scope here is deliberate: no broader/CSRF/cookie/storage clear,
 * no new acknowledgement header or API. Requests carry no bearer or tokens,
 * no sensitive headers/body, `credentials: 'include'`, `cache: 'no-store'`,
 * `redirect: 'error'`, and a bounded abort timeout.
 *
 * The barrier state is module-local and ephemeral (never persisted), scoped
 * to the current local auth lifetime. `invalidateBrowserCache()` is called
 * by the auth module on initialization-relevant lifecycle changes (new sign
 * in, sign out/clear, remote refresh broadcasts). Routine token rotation
 * does not pass through lifecycle invalidation and keeps a completed
 * barrier for the same lifetime or an already-cleared boot.
 */

export type BrowserCacheClearReason =
  | 'initialization'
  | 'revalidate'
  | 'sign-out'
  | 'retry';

export interface BrowserCacheClearRequestResult {
  kind: 'app-proxy' | 'direct-configured';
  url: string;
  origin: string;
  ok: boolean;
  error: string | null;
}

export interface BrowserCacheClearAttemptResult {
  reason: BrowserCacheClearReason;
  generation: number;
  /**
   * True when every targeted origin request passed the qualified endpoint
   * contract (exact 204 + readable no-store). This is an endpoint
   * acknowledgement, not proof that every browser erased the cache.
   */
  endpointAcknowledged: boolean;
  targets: BrowserCacheClearRequestResult[];
  error: string | null;
}

export type BrowserCacheClearStatus = 'idle' | 'pending' | 'ready' | 'failed';

export interface BrowserCacheState {
  status: BrowserCacheClearStatus;
  /** Barrier generation (one per auth lifetime invalidation). */
  generation: number;
  /** Failure detail attached when status is 'failed'; never credentials. */
  error: string | null;
}

export const BROWSER_CACHE_CLEAR_TIMEOUT_MS = 10_000;

const _stateListeners = new Set<() => void>();

function _isSecureContextRequired(): boolean {
  return typeof window === 'undefined'
    ? false
    : window.isSecureContext === true;
}

function _isLoopbackHostname(hostname: string): boolean {
  const host = hostname.replace(/^\[|\]$/g, '').toLowerCase();
  if (host === 'localhost' || host.endsWith('.localhost')) return true;
  if (host === '::1') return true;
  // IPv4 literal in the 127.0.0.0/8 loopback range only; each octet must be
  // a plain decimal literal, so '127.0.0.1.evil.tld' never passes.
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) {
    const octets = host.split('.');
    if (octets[0] !== '127') return false;
    return octets.every((octet) => {
      const value = Number(octet);
      return Number.isInteger(value) && value >= 0 && value <= 255;
    });
  }
  return false;
}

function _isTrustworthyTargetUrl(parsed: URL): boolean {
  if (parsed.protocol === 'https:') return true;
  if (parsed.protocol !== 'http:') return false;
  return _isLoopbackHostname(parsed.hostname);
}

// Barrier state, snapshot-cached so getSnapshot references stay stable.
let _generation = 1;
let _readyGeneration: number | null = null;
let _failedGeneration: number | null = null;
let _failedError: string | null = null;
let _pendingPromise: Promise<BrowserCacheClearAttemptResult> | null = null;
let _pendingGeneration: number | null = null;

let _snapshot: BrowserCacheState = {
  status: 'idle',
  generation: 1,
  error: null,
};

function _recomputeSnapshot(): void {
  let status: BrowserCacheClearStatus;
  let error: string | null = null;
  if (_readyGeneration === _generation) status = 'ready';
  else if (_pendingPromise) status = 'pending';
  else if (_failedGeneration === _generation) {
    status = 'failed';
    error = _failedError;
  } else status = 'idle';

  if (
    status === _snapshot.status &&
    _snapshot.generation === _generation &&
    _snapshot.error === error
  ) {
    return;
  }
  _snapshot = { status, generation: _generation, error };
  for (const listener of _stateListeners) listener();
}

function _configuredApiBase(): string {
  const raw =
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '');
  return raw.replace(/\/+$/, '');
}

interface DirectTargetResolution {
  /** The request URL, or the raw configured base when unparseable. */
  url: string;
  origin: string;
  /** Non-fatal: the app proxy request already covers this origin. */
  coveredByAppProxy?: boolean;
  /** Fatal configuration problem: the attempt fails closed overall. */
  problem?: string;
}

/**
 * Resolve the configured API origin with any path prefix honored. A
 * same-origin configuration is covered by the app-proxy request (the direct
 * un-prefixed path may simply not be routed), so it is skipped rather than
 * demanded. Direct targets that are not HTTPS or strict-loopback HTTP are
 * rejected before any request is issued.
 */
function _resolveDirectTarget(appOrigin: string): DirectTargetResolution {
  const base = _configuredApiBase();
  if (!base) {
    return { url: '', origin: '', coveredByAppProxy: true };
  }

  let parsed: URL;
  try {
    parsed = new URL(base);
  } catch {
    return {
      url: '[invalid configured API URL]',
      origin: '',
      problem: 'configured API base URL is not an absolute URL',
    };
  }
  if (
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    !_isTrustworthyTargetUrl(parsed)
  ) {
    return {
      url: '[invalid configured API URL]',
      origin: '',
      problem: 'configured API target must be HTTPS or validated loopback HTTP',
    };
  }

  const prefix = parsed.pathname.replace(/\/+$/, '');
  const url = `${parsed.origin}${prefix}/v1/auth/cache/clear`;
  if (parsed.origin === appOrigin) {
    return { url, origin: parsed.origin, coveredByAppProxy: true };
  }
  return { url, origin: parsed.origin };
}

export function resolvedBrowserCacheTargets(
  appOrigin: string,
): BrowserCacheClearRequestResult[] {
  const targets: BrowserCacheClearRequestResult[] = [
    {
      kind: 'app-proxy',
      url: '/api/v1/auth/cache/clear',
      origin: appOrigin,
      ok: false,
      error: 'not attempted',
    },
  ];
  const direct = _resolveDirectTarget(appOrigin);
  if (!direct.problem && !direct.coveredByAppProxy) {
    targets.push({
      kind: 'direct-configured',
      url: direct.url,
      origin: direct.origin,
      ok: false,
      error: 'not attempted',
    });
  }
  return targets;
}

function _configProblemTargets(
  appOrigin: string,
): BrowserCacheClearRequestResult[] {
  const direct = _resolveDirectTarget(appOrigin);
  if (!direct.problem) return [];
  return [
    {
      kind: 'direct-configured',
      url: direct.url,
      origin: direct.origin,
      ok: false,
      error: direct.problem,
    },
  ];
}

function _cacheControlHasNoStore(value: string | null): boolean {
  if (!value) return false;
  return value
    .split(',')
    .some((token) => token.trim().toLowerCase() === 'no-store');
}

async function _clearCacheAt(
  target: BrowserCacheClearRequestResult,
): Promise<BrowserCacheClearRequestResult> {
  if (typeof window === 'undefined' || typeof fetch === 'undefined') {
    return {
      ...target,
      ok: false,
      error: 'cache clearing unavailable outside the browser',
    };
  }

  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(),
    BROWSER_CACHE_CLEAR_TIMEOUT_MS,
  );
  try {
    // Deliberately headerless and bodyless: no bearer, no tokens, nothing
    // sensitive leaves the page for this endpoint.
    const response = await fetch(target.url, {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
      redirect: 'error',
      signal: controller.signal,
    });
    if (controller.signal.aborted) {
      return { ...target, ok: false, error: 'request timed out' };
    }
    if (response.status !== 204) {
      return {
        ...target,
        ok: false,
        error: `unexpected response status ${response.status}`,
      };
    }
    if (!_cacheControlHasNoStore(response.headers.get('cache-control'))) {
      return {
        ...target,
        ok: false,
        error: 'Cache-Control no-store missing or unreadable',
      };
    }
    return { ...target, ok: true, error: null };
  } catch (error) {
    if (controller.signal.aborted) {
      return { ...target, ok: false, error: 'request timed out' };
    }
    return { ...target, ok: false, error: 'cache-clear request failed' };
  } finally {
    clearTimeout(timer);
  }
}

async function _runAttempt(
  generation: number,
  reason: BrowserCacheClearReason,
): Promise<BrowserCacheClearAttemptResult> {
  const appOrigin =
    typeof window !== 'undefined' ? window.location.origin : null;
  if (!appOrigin) {
    return {
      reason,
      generation,
      endpointAcknowledged: false,
      targets: [],
      error: 'cache clearing unavailable outside the browser',
    };
  }
  // Clear-Site-Data processing requires a secure context. On unqualified
  // windows the barrier never opens and never issues the request.
  if (!_isSecureContextRequired()) {
    return {
      reason,
      generation,
      endpointAcknowledged: false,
      targets: [],
      error: 'cache clearing requires a secure browser context',
    };
  }
  const problems = _configProblemTargets(appOrigin);
  if (problems.length) {
    return {
      reason,
      generation,
      endpointAcknowledged: false,
      targets: problems,
      error: 'Configured API URL does not permit secure cache clearing',
    };
  }
  const targets = resolvedBrowserCacheTargets(appOrigin);
  const results = await Promise.all(
    targets.map((target) => _clearCacheAt(target)),
  );
  const all = [...results, ..._configProblemTargets(appOrigin)];
  const failures = all.filter((target) => !target.ok);
  const acknowledged = failures.length === 0;
  return {
    reason,
    generation,
    endpointAcknowledged: acknowledged,
    targets: all,
    error:
      failures.length > 0
        ? failures
            .map(
              (target) =>
                `${target.kind} (${target.url}): ${target.error ?? 'failed'}`,
            )
            .join('; ')
        : null,
  };
}

function _startAttempt(
  reason: BrowserCacheClearReason,
): Promise<BrowserCacheClearAttemptResult> {
  if (_pendingPromise && _pendingGeneration === _generation) {
    // Concurrent requests for the current lifetime deduplicate into the one
    // attempt already in flight.
    return _pendingPromise;
  }
  const generation = _generation;
  _readyGeneration = null;
  const attemptPromise = _runAttempt(generation, reason);
  _pendingPromise = attemptPromise;
  _pendingGeneration = generation;
  _recomputeSnapshot();
  void attemptPromise.then((result) => {
    if (_pendingPromise !== attemptPromise) return;
    _pendingPromise = null;
    _pendingGeneration = null;
    if (result.generation === _generation && result.endpointAcknowledged) {
      // An acknowledgement only counts for the auth lifetime it was
      // requested under.
      _readyGeneration = _generation;
      _failedGeneration = null;
      _failedError = null;
      _recomputeSnapshot();
      return;
    }
    if (result.generation === _generation) {
      // A failed attempt invalidates ANY prior readiness for this lifetime:
      // readiness always tracks the latest attempt outcome (fail closed on
      // retry failure, recovery only via explicit retry/start).
      _readyGeneration = null;
      _failedGeneration = _generation;
      _failedError = result.error;
      _recomputeSnapshot();
      return;
    }
    // Superseded by an auth lifetime change: readiness returns to idle; no
    // stale completion can release the newer lifetime.
    _failedGeneration = null;
    _failedError = null;
    _recomputeSnapshot();
  });
  return attemptPromise;
}

export function subscribeBrowserCacheState(listener: () => void): () => void {
  _stateListeners.add(listener);
  return () => {
    _stateListeners.delete(listener);
  };
}

export function getBrowserCacheState(): BrowserCacheState {
  return _snapshot;
}

/**
 * Invalidate the barrier for the current auth lifetime: prior readiness (or
 * failure) can no longer be reused. Synchronous, never issues network calls
 * by itself; callers that must begin a clear trigger `ensure...` or
 * `startBrowserCacheClearAttempt` themselves.
 */
export function invalidateBrowserCache(): void {
  _generation += 1;
  _readyGeneration = null;
  _failedGeneration = null;
  _failedError = null;
  _recomputeSnapshot();
}

/**
 * Wait until the browser cache has been acknowledged cleared for the
 * current auth lifetime. Joins an attempt already in flight, starts one if
 * none exists, resolves only for the CURRENT lifetime, and REJECTS (fail
 * closed, no auto-retry) when the current lifetime's attempt failed —
 * recovery goes through an explicit retry/start attempt.
 */
export async function ensureBrowserCacheCleared(options?: {
  reason?: BrowserCacheClearReason;
}): Promise<void> {
  const reason = options?.reason ?? 'revalidate';
  for (;;) {
    if (_readyGeneration === _generation) return;
    const pending = _pendingPromise;
    if (pending) {
      await pending;
      continue;
    }
    if (_failedGeneration === _generation) {
      throw new Error(
        _failedError ??
          'browser cache clear failed for this authentication lifetime',
      );
    }
    await _startAttempt(reason);
  }
}

/**
 * Force a fresh attempt (retry button, sign-out attempt even with an expired
 * session). Deduplicates into an attempt already in flight for the current
 * lifetime. Never rejects; the attempt result carries the outcome.
 */
export function startBrowserCacheClearAttempt(options: {
  reason: BrowserCacheClearReason;
}): Promise<BrowserCacheClearAttemptResult> {
  return _startAttempt(options.reason);
}
