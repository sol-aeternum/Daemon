import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthCookieTestLocks } from './helpers/authCookieLocks';
import { AUTH_COORDINATION_KEY } from '../lib/authCookieCoordinator';

// Module isolation: the shared beforeEach uses vi.resetModules() so every test
// imports the auth stack fresh. No test resets modules midway: multi-lifetime
// sequences run on one real module instance, so lifetime leaks and stale
// ownership cannot be concealed behind fixture resets.

const STABLE_EPOCH = 'fictional-epoch-a';

const stableRecord = (): string =>
  JSON.stringify({ version: 1, epoch: STABLE_EPOCH, status: 'stable' });

// Small microtask drain: the native-model FIFO lock fixture and coordinator
// finalization settle through ordinary promise resolutions here.
const flush = async (): Promise<void> => {
  for (let i = 0; i < 30; i++) await Promise.resolve();
};

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

class TestBroadcastChannel extends EventTarget implements BroadcastChannel {
  static instances: TestBroadcastChannel[] = [];
  name: string;
  onmessage: ((this: BroadcastChannel, ev: MessageEvent) => unknown) | null =
    null;
  onmessageerror:
    | ((this: BroadcastChannel, ev: MessageEvent) => unknown)
    | null = null;

  constructor(name: string) {
    super();
    this.name = name;
    TestBroadcastChannel.instances.push(this);
  }

  postMessage(message: unknown): void {
    this.dispatch(message);
  }

  close(): void {}

  dispatch(message: unknown): void {
    const event = new MessageEvent('message', { data: message });
    this.dispatchEvent(event);
    this.onmessage?.call(this, event);
  }

  addEventListener(
    type: string,
    callback: EventListenerOrEventListenerObject | null,
    options?: boolean | AddEventListenerOptions,
  ): void {
    if (callback) super.addEventListener(type, callback, options);
  }

  removeEventListener(
    type: string,
    callback: EventListenerOrEventListenerObject | null,
    options?: boolean | EventListenerOptions,
  ): void {
    if (callback) super.removeEventListener(type, callback, options);
  }
}

function installStorage(
  initial: Record<string, string> = {},
): Map<string, string> {
  const store = new Map(Object.entries(initial));
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
      clear: () => {
        store.clear();
      },
    },
  });
  return store;
}

// Owner-approved P0.2 fixture surface: a qualified navigator (native-model
// FIFO Web Locks via AuthCookieTestLocks) plus usable nonsecret shared
// storage. The default installs the stable coordination record so tokens can
// be bound with real coordinator metadata; `record: 'missing'` exercises
// fresh bootstrap. There is no localStorage lease fallback to model — that
// path was owner-rejected, and unsupported environments fail closed.
function installCoordination(
  options: {
    record?: string | 'missing';
    broadcastChannel?: boolean;
    locks?: boolean;
  } = {},
): { locks: AuthCookieTestLocks; storage: Map<string, string> } {
  const locks = new AuthCookieTestLocks();
  vi.stubGlobal('navigator', options.locks === false ? {} : { locks });
  const storage = installStorage(
    options.record === 'missing'
      ? {}
      : { [AUTH_COORDINATION_KEY]: options.record ?? stableRecord() },
  );
  if (options.broadcastChannel) {
    Object.defineProperty(globalThis, 'BroadcastChannel', {
      configurable: true,
      value: TestBroadcastChannel,
    });
  }
  return { locks, storage };
}

// P0.2 barrier fixture: a fresh-module fetch mock answering the trusted
// cache-clear acknowledgement (exact 204 + readable no-store) and refresh
// responses, plus a qualified (secure-context) test window — jsdom defaults
// to insecure HTTP origins, real browsers qualify loopback/HTTPS.
function assumeQualifiedSecureContext(): void {
  Object.defineProperty(window, 'isSecureContext', {
    configurable: true,
    value: true,
  });
}

function installAuthBarrierFetchMocks(): ReturnType<typeof vi.fn> {
  const mockFetch = vi.fn(
    async (input?: unknown, init?: RequestInit): Promise<Response> => {
      const url =
        typeof input === 'string'
          ? input
          : String((input as Request).url ?? '');
      const method = init?.method ?? 'GET';
      if (method === 'POST' && url === '/api/v1/auth/cache/clear') {
        return new Response(null, {
          status: 204,
          headers: { 'cache-control': 'no-store' },
        });
      }
      if (method === 'POST' && url === '/api/v1/auth/refresh') {
        return new Response(
          JSON.stringify({ access_token: 'refreshed-token', expires_in: 1800 }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      return new Response(
        JSON.stringify({ error: `unexpected ${method} ${url}` }),
        { status: 404, headers: { 'content-type': 'application/json' } },
      );
    },
  );
  globalThis.fetch = mockFetch as unknown as typeof globalThis.fetch;
  return mockFetch;
}

beforeEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  TestBroadcastChannel.instances = [];
  vi.useRealTimers();
  vi.resetModules();
});

describe('auth token management', () => {
  it('hasValidAccessToken returns false when no token', async () => {
    const { hasValidAccessToken } = await import('../lib/auth');
    expect(hasValidAccessToken()).toBe(false);
  });

  it('hasValidAccessToken returns true when token is fresh under a stable shared record', async () => {
    const { locks } = installCoordination();
    const { hasValidAccessToken, setAccessToken } = await import('../lib/auth');
    setAccessToken('tok', Date.now() + 60_000, STABLE_EPOCH);
    expect(hasValidAccessToken()).toBe(true);
    // Binding stable nonsecret metadata is local work only; no cookie
    // transaction is needed for a manual installation.
    expect(locks.request).not.toHaveBeenCalled();
  });

  it('hasValidAccessToken returns false when token is expired', async () => {
    installCoordination();
    const { hasValidAccessToken, setAccessToken } = await import('../lib/auth');
    setAccessToken('tok', Date.now() - 1_000, STABLE_EPOCH);
    expect(hasValidAccessToken()).toBe(false);
  });

  it('hasValidAccessToken returns false when token expires within 30s buffer', async () => {
    installCoordination();
    const { hasValidAccessToken, setAccessToken } = await import('../lib/auth');
    setAccessToken('tok', Date.now() + 20_000, STABLE_EPOCH);
    expect(hasValidAccessToken()).toBe(false);
  });

  it('setAccessToken and getAccessToken round-trip correctly', async () => {
    installCoordination();
    const { setAccessToken, getAccessToken, hasValidAccessToken } =
      await import('../lib/auth');
    setAccessToken('my-token', Date.now() + 60_000, STABLE_EPOCH);
    expect(getAccessToken()).toBe('my-token');
    expect(hasValidAccessToken()).toBe(true);
  });

  it('clearLocalAuthState wipes token and expiry', async () => {
    const { locks } = installCoordination();
    const {
      clearLocalAuthState,
      getAccessToken,
      hasValidAccessToken,
      setAccessToken,
    } = await import('../lib/auth');
    setAccessToken('tok', Date.now() + 99_000, STABLE_EPOCH);
    clearLocalAuthState();
    expect(getAccessToken()).toBeNull();
    expect(hasValidAccessToken()).toBe(false);
    // Local-only cleanup never runs a cookie transaction or network request.
    expect(locks.request).not.toHaveBeenCalled();
  });

  it('getAuthHeader returns null when no valid token', async () => {
    const { getAuthHeader } = await import('../lib/auth');
    expect(getAuthHeader()).toBeNull();
  });

  it('getAuthHeader is barrier-blocked right after setAccessToken and works once the new lifetime clear is acknowledged', async () => {
    // P0.2 barrier compatibility: a sync consumer must not receive a Bearer
    // header for the new auth lifetime before its cache clear is
    // acknowledged; this replaces the old unqualified sync expectation with
    // an explicitly stronger one (never weakened — it still proves the token
    // round trip once the qualified barrier opens).
    installAuthBarrierFetchMocks();
    assumeQualifiedSecureContext();
    installCoordination();
    const { getAuthHeader, setAccessToken } = await import('../lib/auth');
    const { ensureBrowserCacheCleared } = await import('../lib/browserCache');
    setAccessToken('secret', Date.now() + 99_000, STABLE_EPOCH);
    expect(getAuthHeader()).toBeNull();
    await ensureBrowserCacheCleared();
    expect(getAuthHeader()).toBe('Bearer secret');
  });

  it('same-lifetime cookie rotation keeps the acknowledged barrier ready; token installation and local clearing invalidate it synchronously', async () => {
    // Real public transaction behavior (replaces a test of the removed
    // low-level doRefresh): one owned navigator.locks cookie transaction
    // performs cookie rotation without re-running the lifetime cache clear,
    // while identity installation and local sign-out still invalidate the
    // acknowledged barrier synchronously.
    const { locks, storage } = installCoordination();
    const fetchMock = installAuthBarrierFetchMocks();
    assumeQualifiedSecureContext();
    const auth = await import('../lib/auth');
    const { ensureBrowserCacheCleared, getBrowserCacheState } =
      await import('../lib/browserCache');
    auth.setAccessToken('secret', Date.now() + 40_000, STABLE_EPOCH);
    await ensureBrowserCacheCleared();
    const acknowledged = getBrowserCacheState();
    expect(acknowledged.status).toBe('ready');
    expect(auth.getAuthHeader()).toBe('Bearer secret');
    // Only this lifetime's own cache-clear acknowledgement ran so far.
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Ordinary rotation: expire the token by time, then refresh through the
    // real coordinator transaction. The SAME auth lifetime keeps its
    // completed barrier ready and no extra cache-clear fetch appears.
    vi.useFakeTimers();
    await vi.advanceTimersByTimeAsync(31_000);
    expect(auth.hasValidAccessToken()).toBe(false);
    const refreshPromise = auth.refreshAccessToken();
    await vi.advanceTimersByTimeAsync(500);
    const refreshed = await refreshPromise;
    expect(refreshed).toEqual({
      success: true,
      generation: auth.getAuthGeneration(),
    });
    expect(getBrowserCacheState()).toBe(acknowledged);
    expect(auth.getAuthHeader()).toBe('Bearer refreshed-token');
    expect(fetchMock).toHaveBeenCalledTimes(2); // ack + one real refresh
    const [refreshUrl, refreshInit] = fetchMock.mock.calls[1] as unknown as [
      string,
      RequestInit,
    ];
    expect(refreshUrl).toBe('/api/v1/auth/refresh');
    // Cookie-writing requests never use keepalive and always own an
    // AbortSignal for bounded cancellation.
    expect(refreshInit.keepalive).toBe(false);
    expect(refreshInit.signal).toBeInstanceOf(AbortSignal);
    vi.useRealTimers();
    // Rotation preserves the shared nonsecret epoch (no identity change).
    expect(JSON.parse(storage.get(AUTH_COORDINATION_KEY) ?? '{}')).toEqual({
      version: 1,
      epoch: STABLE_EPOCH,
      status: 'stable',
    });
    expect(locks.request).toHaveBeenCalledTimes(1); // no lease alternative
    expect(locks.held).toBe(false);
    expect(locks.queue).toHaveLength(0);

    // The documented invalidating lifecycle paths (new sign-in, sign-out)
    // drop the barrier readiness synchronously.
    auth.setAccessToken('new-account', Date.now() + 99_000, STABLE_EPOCH);
    expect(auth.getAuthHeader()).toBeNull();
    expect(getBrowserCacheState().status).not.toBe('ready');
    await ensureBrowserCacheCleared();
    expect(getBrowserCacheState().status).toBe('ready');
    expect(fetchMock).toHaveBeenCalledTimes(3);
    auth.clearLocalAuthState();
    expect(auth.getAuthHeader()).toBeNull();
    expect(getBrowserCacheState().status).not.toBe('ready');
  });

  it('getAuthHeader returns null when token is expired', async () => {
    installCoordination();
    const { getAuthHeader, setAccessToken } = await import('../lib/auth');
    setAccessToken('expired', Date.now() - 1_000, STABLE_EPOCH);
    expect(getAuthHeader()).toBeNull();
  });
});

describe('auth refresh promise singleton', () => {
  it('getRefreshPromise returns null initially', async () => {
    const { getRefreshPromise } = await import('../lib/auth');
    expect(getRefreshPromise()).toBeNull();
  });
});

describe('listenForAuthEvents', () => {
  it('returns an unsubscribe function', async () => {
    const { listenForAuthEvents } = await import('../lib/auth');
    const unsub = listenForAuthEvents(() => {});
    expect(unsub).toBeDefined();
    expect(typeof unsub).toBe('function');
  });
});

describe('startEmailSignIn', () => {
  it('posts to /api/v1/auth/email/start with email and credentials include', async () => {
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            accepted: true,
            challenge_id: 'ch-123',
            expires_at: 1710000000,
          }),
          { status: 202, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { startEmailSignIn } = await import('../lib/auth');
    const result = await startEmailSignIn('user@example.com');

    expect(result.success).toBe(true);
    expect(result.challengeId).toBe('ch-123');
    expect(result.expiresAt).toBe(1710000000);

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/v1/auth/email/start');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(new Headers(init.headers).get('Content-Type')).toBe(
      'application/json',
    );
    expect(JSON.parse(init.body as string)).toEqual({
      email: 'user@example.com',
    });

    vi.restoreAllMocks();
  });

  it('returns generic error on non-202 failure', async () => {
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ error: 'Rate limit exceeded' }), {
          status: 429,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    globalThis.fetch = mockFetch;

    const { startEmailSignIn } = await import('../lib/auth');
    const result = await startEmailSignIn('user@example.com');

    expect(result.success).toBe(false);
    expect(result.error).toContain('Rate limit exceeded');

    vi.restoreAllMocks();
  });
});

describe('completeEmailSignIn', () => {
  it('posts to /api/v1/auth/email/complete with correct body and credentials include', async () => {
    installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'acc-xyz',
            expires_at: Math.floor(Date.now() / 1000) + 3600,
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { completeEmailSignIn, getAccessToken } = await import('../lib/auth');
    const { readAuthCoordination } =
      await import('../lib/authCookieCoordinator');
    const result = await completeEmailSignIn('ch-123', '654321', 'temporary');

    expect(result.success).toBe(true);

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/v1/auth/email/complete');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(new Headers(init.headers).get('Content-Type')).toBe(
      'application/json',
    );
    expect(JSON.parse(init.body as string)).toEqual({
      challenge_id: 'ch-123',
      code: '654321',
      client_kind: 'web',
      device_persistence: 'temporary',
    });
    // The owned cookie writer carries its own bounded AbortSignal, never
    // keepalive.
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(init.keepalive).toBe(false);

    expect(getAccessToken()).toBe('acc-xyz');
    expect(readAuthCoordination()).toMatchObject({ status: 'stable' });

    vi.restoreAllMocks();
  });

  it('includes invite_token in email complete only when non-empty', async () => {
    installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'acc-xyz',
            expires_at: Math.floor(Date.now() / 1000) + 3600,
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { completeEmailSignIn } = await import('../lib/auth');
    await completeEmailSignIn(
      'ch-123',
      '654321',
      'temporary',
      ' invite-secret ',
    );
    await completeEmailSignIn('ch-123', '654321', 'temporary', '   ');

    const [, firstInit] = mockFetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(firstInit.body as string)).toEqual({
      challenge_id: 'ch-123',
      code: '654321',
      client_kind: 'web',
      device_persistence: 'temporary',
      invite_token: 'invite-secret',
    });

    const [, secondInit] = mockFetch.mock.calls[1] as unknown as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(secondInit.body as string)).toEqual({
      challenge_id: 'ch-123',
      code: '654321',
      client_kind: 'web',
      device_persistence: 'temporary',
    });

    vi.restoreAllMocks();
  });

  it('treats backend expires_at as epoch seconds and stores ms', async () => {
    installCoordination();
    const futureEpochSeconds = Math.floor(Date.now() / 1000) + 3600;
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'acc-abc',
            expires_at: futureEpochSeconds,
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { completeEmailSignIn, hasValidAccessToken } =
      await import('../lib/auth');
    await completeEmailSignIn('ch-1', '000000', 'private');

    expect(hasValidAccessToken()).toBe(true);

    vi.restoreAllMocks();
  });

  it('ignores any refresh_token in the response and only stores access_token', async () => {
    installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'acc-only',
            expires_at: Math.floor(Date.now() / 1000) + 3600,
            refresh_token: 'should-be-ignored',
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { completeEmailSignIn, getAccessToken } = await import('../lib/auth');
    const { readAuthCoordination } =
      await import('../lib/authCookieCoordinator');
    const result = await completeEmailSignIn('ch-1', '111111', 'private');

    expect(result.success).toBe(true);
    expect(getAccessToken()).toBe('acc-only');
    expect(readAuthCoordination()).toMatchObject({ status: 'stable' });

    vi.restoreAllMocks();
  });

  it('returns generic error on 401 without exposing account existence', async () => {
    installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ error: 'code_invalid_or_expired' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    globalThis.fetch = mockFetch;

    const { completeEmailSignIn, getAccessToken } = await import('../lib/auth');
    const result = await completeEmailSignIn('ch-1', 'bad', 'private');

    expect(result.success).toBe(false);
    expect(result.error).toContain('Invalid or expired code');
    // The backend's raw rejection string must never surface to callers.
    expect(result.error).not.toContain('code_invalid_or_expired');
    // A failed credential attempt never publishes session readiness.
    expect(getAccessToken()).toBeNull();

    vi.restoreAllMocks();
  });
});

describe('Google sign-in helpers', () => {
  it('posts to /api/v1/auth/google/start with credentials include', async () => {
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            challenge_id: 'google-challenge',
            nonce: 'server-nonce',
            expires_at: 1710000000,
          }),
          { status: 202, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { startGoogleSignIn } = await import('../lib/auth');
    const result = await startGoogleSignIn();

    expect(result).toEqual({
      success: true,
      challengeId: 'google-challenge',
      nonce: 'server-nonce',
      expiresAt: 1710000000,
    });
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/v1/auth/google/start');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');

    vi.restoreAllMocks();
  });

  it('posts Google ID token and nonce to complete with credentials include', async () => {
    installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'daemon-access',
            expires_at: Math.floor(Date.now() / 1000) + 3600,
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { completeGoogleSignIn, getAccessToken } =
      await import('../lib/auth');
    const result = await completeGoogleSignIn(
      'google-challenge',
      'server-nonce',
      'google-id-token',
      'temporary',
    );

    expect(result.success).toBe(true);
    const [url, init] = mockFetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/v1/auth/google/complete');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(new Headers(init.headers).get('Content-Type')).toBe(
      'application/json',
    );
    expect(JSON.parse(init.body as string)).toEqual({
      challenge_id: 'google-challenge',
      nonce: 'server-nonce',
      id_token: 'google-id-token',
      client_kind: 'web',
      device_persistence: 'temporary',
    });
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(init.keepalive).toBe(false);
    expect(getAccessToken()).toBe('daemon-access');

    vi.restoreAllMocks();
  });

  it('includes invite_token in Google complete only when non-empty', async () => {
    installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'daemon-access',
            expires_at: Math.floor(Date.now() / 1000) + 3600,
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { completeGoogleSignIn } = await import('../lib/auth');
    await completeGoogleSignIn(
      'google-challenge',
      'server-nonce',
      'google-id-token',
      'private',
      ' invite-secret ',
    );
    await completeGoogleSignIn(
      'google-challenge',
      'server-nonce',
      'google-id-token',
      'private',
      '',
    );

    const [, firstInit] = mockFetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(firstInit.body as string)).toEqual({
      challenge_id: 'google-challenge',
      nonce: 'server-nonce',
      id_token: 'google-id-token',
      client_kind: 'web',
      device_persistence: 'private',
      invite_token: 'invite-secret',
    });

    const [, secondInit] = mockFetch.mock.calls[1] as unknown as [
      string,
      RequestInit,
    ];
    expect(JSON.parse(secondInit.body as string)).toEqual({
      challenge_id: 'google-challenge',
      nonce: 'server-nonce',
      id_token: 'google-id-token',
      client_kind: 'web',
      device_persistence: 'private',
    });

    vi.restoreAllMocks();
  });

  it('treats Google complete expires_at as epoch seconds and stores ms', async () => {
    installCoordination();
    const futureEpochSeconds = Math.floor(Date.now() / 1000) + 3600;
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'daemon-access',
            expires_at: futureEpochSeconds,
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { completeGoogleSignIn, hasValidAccessToken } =
      await import('../lib/auth');
    await completeGoogleSignIn('ch-1', 'nonce-1', 'id-token-1', 'private');

    expect(hasValidAccessToken()).toBe(true);

    vi.restoreAllMocks();
  });

  it('ignores any Google complete refresh_token in web JS', async () => {
    installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'daemon-access-only',
            expires_at: Math.floor(Date.now() / 1000) + 3600,
            refresh_token: 'ignore-me',
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { completeGoogleSignIn, getAccessToken } =
      await import('../lib/auth');
    const result = await completeGoogleSignIn(
      'ch-1',
      'nonce-1',
      'id-token-1',
      'private',
    );

    expect(result.success).toBe(true);
    expect(getAccessToken()).toBe('daemon-access-only');

    vi.restoreAllMocks();
  });

  it('does not store a Google access token when completion is aborted', async () => {
    installCoordination();
    // Hold the owned request body open so the abort lands while the cookie
    // writer is still dispatching, as a native browser would settle it.
    const body = deferred<ArrayBuffer>();
    const mockFetch = vi.fn(
      async () =>
        ({
          status: 200,
          statusText: 'OK',
          headers: new Headers(),
          arrayBuffer: () => body.promise,
        }) as unknown as Response,
    );
    globalThis.fetch = mockFetch;

    const { completeGoogleSignIn, getAccessToken } =
      await import('../lib/auth');
    const { readAuthCoordination } =
      await import('../lib/authCookieCoordinator');
    const controller = new AbortController();
    const resultPromise = completeGoogleSignIn(
      'ch-aborted',
      'nonce-aborted',
      'id-token-aborted',
      'private',
      undefined,
      controller.signal,
    );
    await flush();
    controller.abort();

    const result = await resultPromise;
    expect(result.success).toBe(false);
    expect(result.error).toBe('Authentication changed');
    expect(getAccessToken()).toBeNull();
    // The owned transport was fenced through the transaction signal and
    // terminated without keepalive.
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [, init] = mockFetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect((init.signal as AbortSignal).aborted).toBe(true);
    expect(init.keepalive).toBe(false);
    // An aborted, already-dispatched attempt cannot be classified signed-out:
    // shared state stays uncertain and must not silently restore readiness.
    expect(readAuthCoordination()).toMatchObject({
      status: 'uncertain',
      attempt: expect.any(String),
    });

    vi.restoreAllMocks();
  });
});

describe('refreshAccessToken', () => {
  it('returns success immediately when token is already valid', async () => {
    const { locks } = installCoordination();
    const rejectAll = vi.fn(() =>
      Promise.reject(new Error('unexpected network request')),
    );
    vi.stubGlobal('fetch', rejectAll);
    const {
      refreshAccessToken,
      setAccessToken,
      getRefreshPromise,
      getAuthGeneration,
      isAuthResultCurrent,
    } = await import('../lib/auth');

    setAccessToken('valid-token', Date.now() + 60_000, STABLE_EPOCH);
    const generation = getAuthGeneration();
    const result = await refreshAccessToken();

    expect(result.success).toBe(true);
    expect(result.generation).toBe(generation);
    expect(isAuthResultCurrent(result)).toBe(true);
    expect(rejectAll).not.toHaveBeenCalled();
    expect(locks.request).not.toHaveBeenCalled();
    expect(getRefreshPromise()).toBeNull();
  });

  it('uses one owned native lock transaction when refresh succeeds', async () => {
    const { locks } = installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({ access_token: 'new-access', expires_in: 1800 }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      ),
    );
    vi.stubGlobal('fetch', mockFetch);

    const { refreshAccessToken, setAccessToken, hasValidAccessToken } =
      await import('../lib/auth');
    const { readAuthCoordination } =
      await import('../lib/authCookieCoordinator');
    setAccessToken('tok', Date.now() - 1_000, STABLE_EPOCH);
    const result = await refreshAccessToken();

    expect(result.success).toBe(true);
    // Exactly one native Web Lock acquisition of daemon-auth-cookie; the
    // storage lease path does not exist.
    expect(locks.request).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/v1/auth/refresh');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(init.keepalive).toBe(false);
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(hasValidAccessToken()).toBe(true);
    // Rotation preserves the shared epoch; the nonsecret record is unchanged.
    expect(readAuthCoordination()).toEqual({
      version: 1,
      epoch: STABLE_EPOCH,
      status: 'stable',
    });
  });

  it('returns failure when refresh fails with 401 and clears auth to signed-out', async () => {
    installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    vi.stubGlobal('fetch', mockFetch);

    const {
      refreshAccessToken,
      setAccessToken,
      getAccessToken,
      isAuthResultCurrent,
    } = await import('../lib/auth');
    const { readAuthCoordination } =
      await import('../lib/authCookieCoordinator');
    setAccessToken('old-token', Date.now() - 1_000, STABLE_EPOCH);
    const result = await refreshAccessToken();

    expect(result.success).toBe(false);
    expect(result.error).toBe('Session expired');
    expect(isAuthResultCurrent(result)).toBe(true);
    expect(getAccessToken()).toBeNull();
    // Confirmed 401 expires the session: the record becomes signed-out under a
    // rotated epoch, so no stale holder can re-derive readiness.
    const record = readAuthCoordination();
    expect(record?.status).toBe('signed-out');
    expect(record?.epoch).not.toBe(STABLE_EPOCH);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('a fresh token installation fences an expired refresh queued behind the lock instead of issuing a redundant refresh', async () => {
    const { locks } = installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.reject(new Error('unexpected network request')),
    );
    vi.stubGlobal('fetch', mockFetch);
    const auth = await import('../lib/auth');

    // Hold the native lock so the expired refresh queues without running.
    locks.held = true;
    auth.setAccessToken('tok', Date.now() - 1_000, STABLE_EPOCH);
    const refreshPromise = auth.refreshAccessToken();
    await flush();
    expect(locks.request).toHaveBeenCalledTimes(1);
    expect(locks.queue).toHaveLength(1);
    expect(mockFetch).not.toHaveBeenCalled();

    // The documented installation path cancels this tab's stale owned work;
    // the queued attempt never dispatches a network request.
    auth.setAccessToken('already-refreshed', Date.now() + 60_000, STABLE_EPOCH);
    const result = await refreshPromise;
    expect(result.success).toBe(false);
    expect(result.error).toBe('Authentication changed');
    expect(mockFetch).not.toHaveBeenCalled();
    expect(auth.getAccessToken()).toBe('already-refreshed');
    expect(auth.hasValidAccessToken()).toBe(true);
    expect(locks.queue).toHaveLength(0);
    expect(auth.getRefreshPromise()).toBeNull();
    locks.held = false; // release the manual hold; the queued slot was fenced
  });

  it('listenForAuthEvents does NOT apply token or expiry from a scoped refreshed event (secret-free broadcast)', async () => {
    installCoordination({ broadcastChannel: true });
    const auth = await import('../lib/auth');
    const channel = auth._getChannel();
    expect(channel).not.toBeNull();
    const addEventListenerSpy = vi.spyOn(channel!, 'addEventListener');

    auth.setAccessToken('old-token', Date.now() - 1_000, STABLE_EPOCH);
    expect(auth.hasValidAccessToken()).toBe(false);

    auth.listenForAuthEvents(() => {});

    const messageHandler = addEventListenerSpy.mock.calls[0]?.[1] as (
      event: MessageEvent,
    ) => void;
    const generation = auth.getAuthGeneration();
    messageHandler({
      data: {
        type: 'refreshed',
        tabId: 'other-tab',
        epoch: STABLE_EPOCH,
        accessToken: 'token-in-message',
        expiresAt: Date.now() + 60_000,
        refreshToken: 'rt-in-message',
        idToken: 'it-in-message',
        credential: 'cred-in-message',
        code: 'code-in-message',
        nonce: 'nonce-in-message',
        setupToken: 'setup-in-message',
      },
    } as MessageEvent);

    // A broadcast can never carry credentials: the token is unchanged and
    // stays refused, while the scoped notice still invalidates once.
    expect(auth.getAccessToken()).toBe('old-token');
    expect(auth.hasValidAccessToken()).toBe(false);
    expect(auth.getAuthGeneration()).toBe(generation + 1);
  });

  it('foreign or unscoped broadcasts ignore even secret payloads and never reach the consumer callback', async () => {
    installCoordination({ broadcastChannel: true });
    const {
      _getChannel,
      listenForAuthEvents,
      setAccessToken,
      getAccessToken,
      hasValidAccessToken,
      getAuthGeneration,
    } = await import('../lib/auth');
    setAccessToken('scoped-token', Date.now() + 60_000, STABLE_EPOCH);
    expect(hasValidAccessToken()).toBe(true);

    const channel = _getChannel();
    const addEventListenerSpy = vi.spyOn(channel!, 'addEventListener');
    let callbackCalls = 0;
    listenForAuthEvents(() => {
      callbackCalls += 1;
    });
    const messageHandler = addEventListenerSpy.mock.calls[0]?.[1] as (
      event: MessageEvent,
    ) => void;

    const generation = getAuthGeneration();
    // A notice scoped to a foreign/older epoch and an epoch-less unscoped
    // notice must neither clear, unbind, nor invalidate the local token — and
    // secret-payload fields change nothing.
    messageHandler({
      data: {
        type: 'cleared',
        tabId: 'other-tab',
        epoch: 'fictional-stale-epoch',
        accessToken: 'smuggled-token',
      },
    } as MessageEvent);
    messageHandler({
      data: {
        type: 'refreshed',
        tabId: 'other-tab',
        accessToken: 'smuggled-token',
        expiresAt: Date.now() + 60_000,
      },
    } as MessageEvent);

    expect(callbackCalls).toBe(0);
    expect(getAccessToken()).toBe('scoped-token');
    expect(hasValidAccessToken()).toBe(true);
    expect(getAuthGeneration()).toBe(generation);
  });

  it('listenForAuthEvents passes a scoped cleared event through to the user callback', async () => {
    installCoordination({ broadcastChannel: true });
    const {
      _getChannel,
      listenForAuthEvents,
      getAccessToken,
      hasValidAccessToken,
      setAccessToken,
    } = await import('../lib/auth');

    setAccessToken('doomed-token', Date.now() + 60_000, STABLE_EPOCH);

    const channel = _getChannel();
    expect(channel).not.toBeNull();
    const addEventListenerSpy = vi.spyOn(channel!, 'addEventListener');

    let receivedType: string | null = null;
    listenForAuthEvents((type) => {
      receivedType = type;
    });

    const messageHandler = addEventListenerSpy.mock.calls[0]?.[1] as (
      event: MessageEvent,
    ) => void;
    messageHandler({
      data: {
        type: 'cleared',
        tabId: 'other-tab',
        epoch: STABLE_EPOCH,
      },
    } as MessageEvent);

    expect(receivedType).toBe('cleared');
    expect(getAccessToken()).toBeNull();
    expect(hasValidAccessToken()).toBe(false);
  });

  it('unsupported Web Locks fail closed: no lease fallback, zero network, and local sign-out remains available', async () => {
    installCoordination({ locks: false });
    const mockFetch = vi.fn(() =>
      Promise.reject(new Error('unexpected network request')),
    );
    vi.stubGlobal('fetch', mockFetch);

    const {
      refreshAccessToken,
      setAccessToken,
      getAccessToken,
      hasValidAccessToken,
      clearAuthState,
      completeSetup,
    } = await import('../lib/auth');

    // Metadata installation itself needs no lock, but nothing may grant
    // protected readiness without qualified coordination.
    setAccessToken('local-token', Date.now() + 60_000, STABLE_EPOCH);

    const refreshResult = await refreshAccessToken();
    expect(refreshResult.success).toBe(false);
    expect(refreshResult.error).toContain('unavailable');
    const setupResult = await completeSetup('fictional-setup');
    expect(setupResult.success).toBe(false);
    expect(setupResult.error).toContain('unavailable');

    // No network request whatsoever: fail closed, never a lease-ready
    // refresh or completion.
    expect(mockFetch).not.toHaveBeenCalled();
    expect(getAccessToken()).toBeNull();

    // Local sign-out always remains available.
    clearAuthState();
    expect(hasValidAccessToken()).toBe(false);
    expect(getAccessToken()).toBeNull();
  });

  it('unusable shared storage fails closed with zero requests, and local sign-out remains available', async () => {
    const locks = new AuthCookieTestLocks();
    vi.stubGlobal('navigator', { locks });
    const setItem = vi.fn(() => {
      throw new Error('localStorage unavailable');
    });
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: {
        getItem: () => null,
        setItem,
        removeItem: () => {},
        clear: () => {},
      },
    });
    const mockFetch = vi.fn(() =>
      Promise.reject(new Error('unexpected network request')),
    );
    vi.stubGlobal('fetch', mockFetch);

    const {
      refreshAccessToken,
      getAccessToken,
      hasValidAccessToken,
      clearAuthState,
    } = await import('../lib/auth');

    const result = await refreshAccessToken();

    // Storage writes cannot be verified, so the coordinator refuses before
    // dispatching: under the native lock, with no network request at all.
    expect(result.success).toBe(false);
    expect(result.error).toContain('unavailable');
    expect(locks.request).toHaveBeenCalledTimes(1);
    expect(mockFetch).not.toHaveBeenCalled();
    expect(hasValidAccessToken()).toBe(false);
    expect(getAccessToken()).toBeNull();

    // Local sign-out always remains available.
    clearAuthState();
    expect(getAccessToken()).toBeNull();
    expect(hasValidAccessToken()).toBe(false);
  });

  it('same-tab refresh waiters join one owned transaction', async () => {
    installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'shared-refreshed-token',
            expires_in: 1800,
          }),
          { status: 200 },
        ),
      ),
    );
    vi.stubGlobal('fetch', mockFetch);

    const {
      refreshAccessToken,
      getAccessToken,
      hasValidAccessToken,
      getAuthGeneration,
    } = await import('../lib/auth');

    const refreshA = refreshAccessToken();
    const refreshB = refreshAccessToken();
    const [resultA, resultB] = await Promise.all([refreshA, refreshB]);

    expect(resultA.success).toBe(true);
    expect(resultB.success).toBe(true);
    // Waiters resolve from the same single-flight result with one generation.
    expect(resultB.generation).toBe(resultA.generation);
    expect(resultA.generation).toBe(getAuthGeneration());
    expect(getAccessToken()).toBe('shared-refreshed-token');
    expect(hasValidAccessToken()).toBe(true);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });
});

describe('logoutCurrentSession', () => {
  it('posts to logout with the current access token and credentials in one owned transaction', async () => {
    const { locks } = installCoordination();
    const mockFetch = vi.fn(async (input?: unknown): Promise<Response> => {
      const url =
        typeof input === 'string'
          ? input
          : String((input as Request).url ?? '');
      // Revocation must not depend on successful cache cleanup: the barrier
      // path is answerable but must never be required here.
      if (url === '/api/v1/auth/cache/clear') {
        return new Response(null, {
          status: 204,
          headers: { 'cache-control': 'no-store' },
        });
      }
      return new Response(null, { status: 204 });
    });
    vi.stubGlobal('fetch', mockFetch);

    const {
      logoutCurrentSession,
      setAccessToken,
      getAccessToken,
      isAuthResultCurrent,
    } = await import('../lib/auth');
    const { readAuthCoordination } =
      await import('../lib/authCookieCoordinator');
    setAccessToken('active-access', Date.now() + 60_000, STABLE_EPOCH);

    const result = await logoutCurrentSession();
    expect(result.success).toBe(true);
    expect(isAuthResultCurrent(result)).toBe(true);
    // One revocation request inside one native lock transaction.
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(locks.request).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/v1/auth/logout');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    expect(new Headers(init.headers).get('Authorization')).toBe(
      'Bearer active-access',
    );
    expect(init.keepalive).toBe(false);
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(getAccessToken()).toBeNull();
    expect(readAuthCoordination()).toMatchObject({ status: 'signed-out' });
  });

  it('expired-session logout refresh 401 completes the transaction as a signed-out session', async () => {
    installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    vi.stubGlobal('fetch', mockFetch);

    const { logoutCurrentSession, getAccessToken, isAuthResultCurrent } =
      await import('../lib/auth');
    const { readAuthCoordination } =
      await import('../lib/authCookieCoordinator');
    const result = await logoutCurrentSession();

    expect(result.success).toBe(false);
    expect(result.error).toBe('Session expired');
    expect(isAuthResultCurrent(result)).toBe(true);
    // Only the refresh ran; no revocation can be issued for an expired cookie.
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url] = mockFetch.mock.calls[0] as unknown as [string];
    expect(url).toBe('/api/v1/auth/refresh');
    expect(getAccessToken()).toBeNull();
    expect(readAuthCoordination()).toMatchObject({ status: 'signed-out' });
  });

  it('surfaces backend logout failures to the caller', async () => {
    installCoordination();
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: 'logout unavailable' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    vi.stubGlobal('fetch', mockFetch);

    const {
      logoutCurrentSession,
      setAccessToken,
      getAccessToken,
      isAuthResultCurrent,
    } = await import('../lib/auth');
    const { readAuthCoordination } =
      await import('../lib/authCookieCoordinator');
    setAccessToken('active-access', Date.now() + 60_000, STABLE_EPOCH);

    const result = await logoutCurrentSession();

    expect(result.success).toBe(false);
    // The real contract surfaces the failed backend logout status code
    // (response bodies are not further classified for the logout path).
    expect(result.error).toBe('Logout failed: 503');
    expect(isAuthResultCurrent(result)).toBe(true);
    // A dispatched-but-failed revocation is not proof of signed-out: shared
    // state is uncertain and protected exposure stays withheld.
    expect(readAuthCoordination()).toMatchObject({ status: 'uncertain' });
    expect(getAccessToken()).toBeNull();
  });
});

describe('BroadcastChannel secret-free invariant', () => {
  const FORBIDDEN_FIELDS = [
    'accessToken',
    'access_token',
    'refreshToken',
    'refresh_token',
    'idToken',
    'id_token',
    'expiresAt',
    'expires_at',
    'providerToken',
    'googleCredential',
    'credential',
    'emailCode',
    'code',
    'nonce',
    'setupToken',
    'enrollmentCode',
    'token',
    'secret',
  ];

  // Owner-approved P0.2 scope field: messages may additionally carry the
  // nonsecret shared `epoch` so consumers can ignore stale/unscoped notices.
  function assertSecretFree(message: unknown, label: string): void {
    expect(message, `${label} should be a non-null object`).toBeTruthy();
    expect(typeof message, `${label} should be an object`).toBe('object');
    const keys = Object.keys(message as Record<string, unknown>);
    for (const forbidden of FORBIDDEN_FIELDS) {
      expect(
        keys,
        `${label} must not contain forbidden field "${forbidden}" (saw ${JSON.stringify(
          message,
        )})`,
      ).not.toContain(forbidden);
    }
    expect(
      keys.sort(),
      `${label} should have only type, tabId, and the scope epoch`,
    ).toEqual(['epoch', 'tabId', 'type']);
    expect(
      typeof (message as { epoch?: unknown }).epoch,
      `${label} must always be scoped to a nonsecret epoch`,
    ).toBe('string');
  }

  it('refreshAccessToken success broadcasts only type, tabId, and scope epoch (no secrets)', async () => {
    installCoordination({ broadcastChannel: true });
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'fresh-access',
            refresh_token: 'should-be-ignored',
            expires_in: 1800,
          }),
          { status: 200 },
        ),
      ),
    );
    vi.stubGlobal('fetch', mockFetch);

    const { setAccessToken, refreshAccessToken } = await import('../lib/auth');

    const postSpy = vi.spyOn(TestBroadcastChannel.prototype, 'postMessage');
    setAccessToken('expired', Date.now() - 1_000, STABLE_EPOCH);
    const result = await refreshAccessToken();

    expect(result.success).toBe(true);
    expect(postSpy).toHaveBeenCalledTimes(1);
    const refreshedMessage = postSpy.mock.calls.find(
      (call) => (call[0] as { type?: string }).type === 'refreshed',
    )?.[0];
    expect(refreshedMessage).toBeDefined();
    assertSecretFree(refreshedMessage, 'refreshed broadcast');
    expect((refreshedMessage as { type: string }).type).toBe('refreshed');
    expect(typeof (refreshedMessage as { tabId: string }).tabId).toBe('string');
    expect((refreshedMessage as { epoch: string }).epoch).toBe(STABLE_EPOCH);
  });

  it('clearAuthState broadcasts only type, tabId, and scope epoch (no secrets)', async () => {
    installCoordination({ broadcastChannel: true });
    const { setAccessToken, clearAuthState } = await import('../lib/auth');

    setAccessToken('doomed', Date.now() + 60_000, STABLE_EPOCH);

    const postSpy = vi.spyOn(TestBroadcastChannel.prototype, 'postMessage');
    clearAuthState();

    expect(postSpy).toHaveBeenCalledTimes(1);
    const clearedMessage = postSpy.mock.calls.find(
      (call) => (call[0] as { type?: string }).type === 'cleared',
    )?.[0];
    expect(clearedMessage).toBeDefined();
    assertSecretFree(clearedMessage, 'cleared broadcast');
    expect((clearedMessage as { type: string }).type).toBe('cleared');
    expect((clearedMessage as { epoch: string }).epoch).toBe(STABLE_EPOCH);
  });

  it('401 refresh path broadcasts a scoped cleared event with only type and tabId', async () => {
    installCoordination({ broadcastChannel: true });
    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    vi.stubGlobal('fetch', mockFetch);

    const { setAccessToken, refreshAccessToken, getAccessToken } =
      await import('../lib/auth');

    const postSpy = vi.spyOn(TestBroadcastChannel.prototype, 'postMessage');
    setAccessToken('doomed', Date.now() - 1_000, STABLE_EPOCH);
    const result = await refreshAccessToken();

    expect(result.success).toBe(false);
    expect(result.error).toBe('Session expired');
    expect(getAccessToken()).toBeNull();

    const broadcasts = postSpy.mock.calls.map(
      (call) => call[0] as Record<string, unknown>,
    );
    expect(broadcasts.length).toBeGreaterThan(0);
    for (const message of broadcasts) {
      assertSecretFree(message, '401-path broadcast');
    }
    const clearedBroadcasts = broadcasts.filter((m) => m.type === 'cleared');
    expect(clearedBroadcasts).toHaveLength(1);
    expect((clearedBroadcasts[0] as { epoch: string }).epoch).toBe(
      STABLE_EPOCH,
    );
  });
});

describe('attemptPageLoadRefresh identity-session restore', () => {
  it('memory is empty after module load and refreshAccessToken restores via cookie', async () => {
    // Fresh bootstrap: no prior shared record exists, so the page-load
    // recovery may establish one under the owned lock.
    const { locks } = installCoordination({ record: 'missing' });
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'restored-from-cookie',
            expires_in: 1800,
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
      ),
    );
    vi.stubGlobal('fetch', mockFetch);
    const { refreshAccessToken, getAccessToken, hasValidAccessToken } =
      await import('../lib/auth');
    const { readAuthCoordination } =
      await import('../lib/authCookieCoordinator');

    expect(getAccessToken()).toBeNull();
    expect(hasValidAccessToken()).toBe(false);

    const result = await refreshAccessToken();
    expect(result.success).toBe(true);
    expect(getAccessToken()).toBe('restored-from-cookie');
    expect(hasValidAccessToken()).toBe(true);

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/v1/auth/refresh');
    expect(init.credentials).toBe('include');
    expect(init.method).toBe('POST');
    expect(init.keepalive).toBe(false);
    expect(init.signal).toBeInstanceOf(AbortSignal);
    // The recovered session publishes a stable nonsecret record — a fresh
    // opaque epoch and only nonsecret fields.
    const record = readAuthCoordination();
    expect(record?.status).toBe('stable');
    expect(typeof record?.epoch).toBe('string');
    expect(record!.epoch.length).toBeGreaterThan(0);
    expect(Object.keys(record ?? {}).sort()).toEqual([
      'epoch',
      'status',
      'version',
    ]);
    expect(locks.request).toHaveBeenCalledTimes(1);
    expect(locks.held).toBe(false);
  });

  it('attemptPageLoadRefresh returns true after cookie refresh restores a session', async () => {
    installCoordination({ record: 'missing' });
    const mockFetch = vi.fn().mockReturnValue(
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'identity-session-restored',
            expires_in: 1800,
          }),
          {
            status: 200,
            headers: { 'content-type': 'application/json' },
          },
        ),
      ),
    );
    vi.stubGlobal('fetch', mockFetch);

    const { attemptPageLoadRefresh, getAccessToken, hasValidAccessToken } =
      await import('../lib/auth');

    expect(getAccessToken()).toBeNull();

    const ok = await attemptPageLoadRefresh();
    expect(ok).toBe(true);
    expect(hasValidAccessToken()).toBe(true);
    expect(getAccessToken()).toBe('identity-session-restored');
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('attemptPageLoadRefresh returns false and redirects to /auth on 401 for expired temporary sessions', async () => {
    installCoordination({ record: 'missing' });
    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    vi.stubGlobal('fetch', mockFetch);

    const { readAuthCoordination } =
      await import('../lib/authCookieCoordinator');
    const { attemptPageLoadRefresh, getAccessToken } =
      await import('../lib/auth');

    const replace = vi.fn();
    const originalLocation = window.location;
    const locationSpy = vi.spyOn(window, 'location', 'get').mockReturnValue({
      ...originalLocation,
      replace,
    } as unknown as Location);

    const ok = await attemptPageLoadRefresh();
    expect(ok).toBe(false);
    expect(getAccessToken()).toBeNull();
    expect(replace).toHaveBeenCalledWith('/auth');
    // A confirmed 401 expiry is signed-out, not an uncertain fallback.
    expect(readAuthCoordination()).toMatchObject({ status: 'signed-out' });

    locationSpy.mockRestore();
  });

  it('attemptPageLoadRefresh can suppress the legacy /setup redirect on expired sessions', async () => {
    installCoordination({ record: 'missing' });
    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    vi.stubGlobal('fetch', mockFetch);

    const { attemptPageLoadRefresh, getAccessToken } =
      await import('../lib/auth');

    const replace = vi.fn();
    const originalLocation = window.location;
    const locationSpy = vi.spyOn(window, 'location', 'get').mockReturnValue({
      ...originalLocation,
      replace,
    } as unknown as Location);

    const ok = await attemptPageLoadRefresh({
      redirectOnExpiredSession: false,
    });
    expect(ok).toBe(false);
    expect(getAccessToken()).toBeNull();
    expect(replace).not.toHaveBeenCalled();

    locationSpy.mockRestore();
  });

  it('attemptPageLoadRefresh can avoid mutating read-only window.location on 401', async () => {
    installCoordination({ record: 'missing' });
    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    vi.stubGlobal('fetch', mockFetch);

    const { attemptPageLoadRefresh, getAccessToken } =
      await import('../lib/auth');

    const ok = await attemptPageLoadRefresh({
      redirectOnExpiredSession: false,
    });
    expect(ok).toBe(false);
    expect(getAccessToken()).toBeNull();
  });
});
