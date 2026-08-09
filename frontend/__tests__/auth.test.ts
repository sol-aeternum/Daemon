import { beforeEach, describe, expect, it, vi } from 'vitest';

// Mocks must be set up before importing the module, so we use vi.doMock() inside
// a beforeEach and import the module fresh inside each test.

const mockNavigator = { value: { locks: undefined } };
const mockLocalStorage = { value: {} as Record<string, string> };
const mockBroadcastChannel = { value: null as BroadcastChannel | null };

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

function installStorage(initial: Record<string, string> = {}): void {
  const store = { ...initial };
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => {
        store[key] = value;
      },
      removeItem: (key: string) => {
        delete store[key];
      },
      clear: () => {
        for (const key of Object.keys(store)) delete store[key];
      },
    },
  });
}

beforeEach(() => {
  mockNavigator.value = { locks: undefined };
  mockLocalStorage.value = {};
  mockBroadcastChannel.value = null;
  vi.resetModules();
});

describe('auth token management', () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    TestBroadcastChannel.instances = [];
    vi.resetModules();
  });

  it('hasValidAccessToken returns false when no token', async () => {
    const { hasValidAccessToken } = await import('../lib/auth');
    expect(hasValidAccessToken()).toBe(false);
  });

  it('hasValidAccessToken returns true when token is fresh', async () => {
    const { hasValidAccessToken, setAccessToken } = await import('../lib/auth');
    setAccessToken('tok', Date.now() + 60_000);
    expect(hasValidAccessToken()).toBe(true);
  });

  it('hasValidAccessToken returns false when token is expired', async () => {
    const { hasValidAccessToken, setAccessToken } = await import('../lib/auth');
    setAccessToken('tok', Date.now() - 1_000);
    expect(hasValidAccessToken()).toBe(false);
  });

  it('hasValidAccessToken returns false when token expires within 30s buffer', async () => {
    const { hasValidAccessToken, setAccessToken } = await import('../lib/auth');
    setAccessToken('tok', Date.now() + 20_000);
    expect(hasValidAccessToken()).toBe(false);
  });

  it('setAccessToken and getAccessToken round-trip correctly', async () => {
    const { setAccessToken, getAccessToken } = await import('../lib/auth');
    setAccessToken('my-token', 99_000);
    expect(getAccessToken()).toBe('my-token');
  });

  it('clearLocalAuthState wipes token and expiry', async () => {
    const {
      clearLocalAuthState,
      getAccessToken,
      hasValidAccessToken,
      setAccessToken,
    } = await import('../lib/auth');
    setAccessToken('tok', Date.now() + 99_000);
    clearLocalAuthState();
    expect(getAccessToken()).toBeNull();
    expect(hasValidAccessToken()).toBe(false);
  });

  it('getAuthHeader returns null when no valid token', async () => {
    const { getAuthHeader } = await import('../lib/auth');
    expect(getAuthHeader()).toBeNull();
  });

  it('getAuthHeader returns Bearer token when valid', async () => {
    const { getAuthHeader, setAccessToken } = await import('../lib/auth');
    setAccessToken('secret', Date.now() + 99_000);
    expect(getAuthHeader()).toBe('Bearer secret');
  });

  it('getAuthHeader returns null when token is expired', async () => {
    const { getAuthHeader, setAccessToken } = await import('../lib/auth');
    setAccessToken('expired', Date.now() - 1_000);
    expect(getAuthHeader()).toBeNull();
  });
});

describe('auth refresh promise singleton', () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    vi.resetModules();
  });

  it('getRefreshPromise returns null initially', async () => {
    const { getRefreshPromise } = await import('../lib/auth');
    expect(getRefreshPromise()).toBeNull();
  });
});

describe('listenForAuthEvents', () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    vi.resetModules();
  });

  it('returns an unsubscribe function', async () => {
    const { listenForAuthEvents } = await import('../lib/auth');
    const unsub = listenForAuthEvents(() => {});
    expect(unsub).toBeDefined();
    expect(typeof unsub).toBe('function');
  });
});

describe('startEmailSignIn', () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    vi.resetModules();
  });

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
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    vi.resetModules();
  });

  it('posts to /api/v1/auth/email/complete with correct body and credentials include', async () => {
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'acc-xyz',
            expires_at: 1710003600,
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { completeEmailSignIn, getAccessToken } = await import('../lib/auth');
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

    expect(getAccessToken()).toBe('acc-xyz');

    vi.restoreAllMocks();
  });

  it('includes invite_token in email complete only when non-empty', async () => {
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'acc-xyz',
            expires_at: 1710003600,
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
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'acc-only',
            expires_at: 1710003600,
            refresh_token: 'should-be-ignored',
            token_type: 'Bearer',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { completeEmailSignIn, getAccessToken } = await import('../lib/auth');
    const result = await completeEmailSignIn('ch-1', '111111', 'private');

    expect(result.success).toBe(true);
    expect(getAccessToken()).toBe('acc-only');

    vi.restoreAllMocks();
  });

  it('returns generic error on 401 without exposing account existence', async () => {
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ error: 'code_invalid_or_expired' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    globalThis.fetch = mockFetch;

    const { completeEmailSignIn } = await import('../lib/auth');
    const result = await completeEmailSignIn('ch-1', 'bad', 'private');

    expect(result.success).toBe(false);
    expect(result.error).toContain('Invalid or expired code');

    vi.restoreAllMocks();
  });
});

describe('Google sign-in helpers', () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    vi.resetModules();
  });

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
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'daemon-access',
            expires_at: 1710003600,
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
    expect(getAccessToken()).toBe('daemon-access');

    vi.restoreAllMocks();
  });

  it('includes invite_token in Google complete only when non-empty', async () => {
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'daemon-access',
            expires_at: 1710003600,
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
});

describe('refreshAccessToken', () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    vi.resetModules();
  });

  it('returns success immediately when token is already valid', async () => {
    const { refreshAccessToken, setAccessToken } = await import('../lib/auth');
    setAccessToken('valid-token', Date.now() + 60_000);
    const result = await refreshAccessToken();
    expect(result).toEqual({ success: true });
  });

  it('uses navigator.locks when available and refresh succeeds', async () => {
    const mockLockRequest = vi.fn(
      async (_name: string, cb: (impl: { held: boolean }) => void) => {
        await cb({ held: true });
      },
    );
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: { request: mockLockRequest } },
      writable: true,
    });

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
    globalThis.fetch = mockFetch;

    const { refreshAccessToken, setAccessToken } = await import('../lib/auth');
    setAccessToken('tok', Date.now() - 1_000);
    const result = await refreshAccessToken();

    expect(result.success).toBe(true);
    expect(mockLockRequest).toHaveBeenCalledWith(
      'daemon-refresh',
      expect.any(Function),
    );

    Object.defineProperty(globalThis, 'navigator', {
      value: mockNavigator.value,
      writable: true,
    });
    vi.restoreAllMocks();
  });

  it('returns failure when refresh fails with 401 and clears auth', async () => {
    const mockLockRequest = vi.fn(
      async (_name: string, cb: (impl: { held: boolean }) => void) => {
        await cb({ held: true });
      },
    );
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: { request: mockLockRequest } },
      writable: true,
    });

    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    globalThis.fetch = mockFetch;

    const { refreshAccessToken, setAccessToken, getAccessToken } =
      await import('../lib/auth');
    setAccessToken('old-token', Date.now() - 1_000);
    const result = await refreshAccessToken();

    expect(result.success).toBe(false);
    expect(result.error).toBe('Session expired');
    expect(getAccessToken()).toBeNull();

    Object.defineProperty(globalThis, 'navigator', {
      value: mockNavigator.value,
      writable: true,
    });
    vi.restoreAllMocks();
  });

  it('rechecks hasValidAccessToken after acquiring navigator.locks before calling doRefresh', async () => {
    const mockLockRequest = vi.fn(
      async (_name: string, cb: (impl: { held: boolean }) => void) => {
        const { setAccessToken } = await import('../lib/auth');
        setAccessToken('already-refreshed', Date.now() + 60_000);
        await cb({ held: true });
      },
    );
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: { request: mockLockRequest } },
      writable: true,
    });

    const mockFetch = vi.fn();
    globalThis.fetch = mockFetch;

    const { refreshAccessToken } = await import('../lib/auth');
    const result = await refreshAccessToken();

    expect(result.success).toBe(true);
    expect(mockFetch).not.toHaveBeenCalled();

    Object.defineProperty(globalThis, 'navigator', {
      value: mockNavigator.value,
      writable: true,
    });
    vi.restoreAllMocks();
  });

  it('listenForAuthEvents does NOT apply token or expiry from a received refreshed event (secret-free broadcast)', async () => {
    vi.resetModules();
    const { _getChannel } = await import('../lib/auth');
    const channel = _getChannel();
    expect(channel).not.toBeNull();
    const addEventListenerSpy = vi.spyOn(channel!, 'addEventListener');

    const {
      listenForAuthEvents,
      getAccessToken,
      hasValidAccessToken,
      setAccessToken,
    } = await import('../lib/auth');
    setAccessToken('old-token', Date.now() - 1_000);
    expect(hasValidAccessToken()).toBe(false);

    listenForAuthEvents(() => {});

    const messageHandler = addEventListenerSpy.mock.calls[0]?.[1] as (
      event: MessageEvent,
    ) => void;
    messageHandler({
      data: {
        type: 'refreshed',
        tabId: 'other-tab',
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

    expect(getAccessToken()).toBe('old-token');
    expect(hasValidAccessToken()).toBe(false);
  });

  it('listenForAuthEvents passes cleared event through to user callback', async () => {
    vi.resetModules();
    const { _getChannel } = await import('../lib/auth');
    const channel = _getChannel();
    expect(channel).not.toBeNull();
    const addEventListenerSpy = vi.spyOn(channel!, 'addEventListener');

    const { listenForAuthEvents, getAccessToken } = await import('../lib/auth');

    let receivedType: string | null = null;
    listenForAuthEvents((type) => {
      receivedType = type;
    });

    const messageHandler = addEventListenerSpy.mock.calls[0]?.[1] as (
      event: MessageEvent,
    ) => void;
    messageHandler({
      data: { type: 'cleared', tabId: 'other-tab' },
    } as MessageEvent);

    expect(receivedType).toBe('cleared');
    expect(getAccessToken()).toBeNull();
  });

  it('refreshAccessToken no-Web-Locks fallback: unavailable localStorage does not proceed to doRefresh', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      configurable: true,
      value: { locks: undefined },
    });
    const setItem = vi.fn(() => {
      throw new Error('localStorage unavailable');
    });
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: {
        getItem: () => null,
        setItem,
        removeItem: () => {},
      },
    });

    // Mock fetch to return 401 so doRefresh fails if it were ever called
    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    globalThis.fetch = mockFetch;

    const {
      refreshAccessToken,
      hasValidAccessToken,
      getAccessToken,
      setAccessToken,
    } = await import('../lib/auth');

    setAccessToken('invalid-token', Date.now() - 1_000);

    const result = await refreshAccessToken();

    // Lock acquisition failed, so doRefresh was NOT called.
    expect(result.success).toBe(false);
    expect(hasValidAccessToken()).toBe(false);
    expect(getAccessToken()).toBe('invalid-token');
    expect(setItem).toHaveBeenCalled();
    expect(mockFetch).not.toHaveBeenCalled();

    vi.restoreAllMocks();
  });

  it('refreshAccessToken no-Web-Locks fallback waits for refreshed broadcast, then performs one serialized cookie-based refresh', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: undefined },
      writable: true,
    });
    Object.defineProperty(globalThis, 'BroadcastChannel', {
      configurable: true,
      value: TestBroadcastChannel,
    });
    installStorage({
      'daemon:refresh-lock': JSON.stringify({
        ownerTabId: 'other-tab',
        nonce: 'other-nonce',
        expiresAt: Date.now() + 10_000,
      }),
    });

    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'self-refreshed-token',
            expires_in: 1800,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { refreshAccessToken, getAccessToken, hasValidAccessToken } =
      await import('../lib/auth');
    const refreshPromise = refreshAccessToken();

    await Promise.resolve();
    TestBroadcastChannel.instances[0]?.dispatch({
      type: 'refreshed',
      tabId: 'other-tab',
    });

    const result = await refreshPromise;
    expect(result.success).toBe(true);
    expect(getAccessToken()).toBe('self-refreshed-token');
    expect(hasValidAccessToken()).toBe(true);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe('/api/v1/auth/refresh');
    expect(init.credentials).toBe('include');

    vi.restoreAllMocks();
  });

  it('refreshAccessToken no-Web-Locks fallback serializes multiple same-tab waiters into one refresh', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: undefined },
      writable: true,
    });
    Object.defineProperty(globalThis, 'BroadcastChannel', {
      configurable: true,
      value: TestBroadcastChannel,
    });
    installStorage({});

    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'shared-refreshed-token',
            expires_in: 1800,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { refreshAccessToken, getAccessToken, hasValidAccessToken } =
      await import('../lib/auth');
    const refreshA = refreshAccessToken();
    const refreshB = refreshAccessToken();

    const [resultA, resultB] = await Promise.all([refreshA, refreshB]);
    expect(resultA.success).toBe(true);
    expect(resultB.success).toBe(true);
    expect(getAccessToken()).toBe('shared-refreshed-token');
    expect(hasValidAccessToken()).toBe(true);
    expect(mockFetch).toHaveBeenCalledTimes(1);

    vi.restoreAllMocks();
  });
});

describe('logoutCurrentSession', () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    vi.resetModules();
  });

  it('posts to logout with the current access token and credentials', async () => {
    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 204 })),
    );
    globalThis.fetch = mockFetch;

    const { logoutCurrentSession, setAccessToken } =
      await import('../lib/auth');
    setAccessToken('active-access', Date.now() + 60_000);

    const result = await logoutCurrentSession();

    expect(result).toEqual({ success: true });
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const call = mockFetch.mock.calls[0];
    expect(call).toBeDefined();
    const [url, init] = call as unknown as [string, RequestInit];
    expect(url).toBe('/api/v1/auth/logout');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    const headers = init.headers as Headers;
    expect(headers.get('Authorization')).toBe('Bearer active-access');
  });

  it('returns failure without throwing when refresh cannot recover an auth header', async () => {
    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    globalThis.fetch = mockFetch;

    const { logoutCurrentSession } = await import('../lib/auth');
    const result = await logoutCurrentSession();

    expect(result).toEqual({ success: false, error: 'No active session' });
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const call = mockFetch.mock.calls[0];
    expect(call).toBeDefined();
    const [url] = call as unknown as [string, RequestInit];
    expect(url).toBe('/api/v1/auth/refresh');
  });

  it('surfaces backend logout failures to the caller', async () => {
    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ detail: 'logout unavailable' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
    globalThis.fetch = mockFetch;

    const { logoutCurrentSession, setAccessToken } =
      await import('../lib/auth');
    setAccessToken('active-access', Date.now() + 60_000);

    const result = await logoutCurrentSession();

    expect(result).toEqual({ success: false, error: 'logout unavailable' });
  });
});

describe('BroadcastChannel secret-free invariant', () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    TestBroadcastChannel.instances = [];
    vi.resetModules();
  });

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
    expect(keys.sort(), `${label} should have only type and tabId`).toEqual([
      'tabId',
      'type',
    ]);
  }

  it('refreshAccessToken success broadcasts only type and tabId (no accessToken, no expiresAt)', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: undefined },
      writable: true,
    });
    Object.defineProperty(globalThis, 'BroadcastChannel', {
      configurable: true,
      value: TestBroadcastChannel,
    });
    installStorage({});

    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'fresh-access',
            refresh_token: 'should-be-ignored',
            expires_in: 1800,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const postSpy = vi.spyOn(TestBroadcastChannel.prototype, 'postMessage');

    const { refreshAccessToken, setAccessToken } = await import('../lib/auth');
    setAccessToken('expired', Date.now() - 1_000);
    const result = await refreshAccessToken();

    expect(result.success).toBe(true);
    expect(postSpy).toHaveBeenCalled();
    const refreshedMessage = postSpy.mock.calls.find(
      (call) => (call[0] as { type?: string }).type === 'refreshed',
    )?.[0];
    expect(refreshedMessage).toBeDefined();
    assertSecretFree(refreshedMessage, 'refreshed broadcast');
    expect((refreshedMessage as { type: string }).type).toBe('refreshed');
    expect(typeof (refreshedMessage as { tabId: string }).tabId).toBe('string');

    vi.restoreAllMocks();
  });

  it('clearAuthState broadcasts only type and tabId (no accessToken, no expiresAt)', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: undefined },
      writable: true,
    });
    Object.defineProperty(globalThis, 'BroadcastChannel', {
      configurable: true,
      value: TestBroadcastChannel,
    });
    installStorage({});

    const { setAccessToken, clearAuthState } = await import('../lib/auth');
    setAccessToken('doomed', Date.now() + 60_000);

    const postSpy = vi.spyOn(TestBroadcastChannel.prototype, 'postMessage');
    clearAuthState();

    expect(postSpy).toHaveBeenCalled();
    const clearedMessage = postSpy.mock.calls.find(
      (call) => (call[0] as { type?: string }).type === 'cleared',
    )?.[0];
    expect(clearedMessage).toBeDefined();
    assertSecretFree(clearedMessage, 'cleared broadcast');
    expect((clearedMessage as { type: string }).type).toBe('cleared');

    vi.restoreAllMocks();
  });

  it('401 refresh path broadcasts cleared event with only type and tabId', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: undefined },
      writable: true,
    });
    Object.defineProperty(globalThis, 'BroadcastChannel', {
      configurable: true,
      value: TestBroadcastChannel,
    });
    installStorage({});

    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    globalThis.fetch = mockFetch;

    const postSpy = vi.spyOn(TestBroadcastChannel.prototype, 'postMessage');

    const { refreshAccessToken, setAccessToken } = await import('../lib/auth');
    setAccessToken('doomed', Date.now() - 1_000);
    const result = await refreshAccessToken();

    expect(result.success).toBe(false);
    expect(result.error).toBe('Session expired');

    const broadcasts = postSpy.mock.calls.map(
      (call) => call[0] as Record<string, unknown>,
    );
    expect(broadcasts.length).toBeGreaterThan(0);
    for (const message of broadcasts) {
      assertSecretFree(message, '401-path broadcast');
    }
    const clearedBroadcast = broadcasts.find((m) => m.type === 'cleared');
    expect(clearedBroadcast).toBeDefined();

    vi.restoreAllMocks();
  });
});

describe('attemptPageLoadRefresh identity-session restore', () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    vi.resetModules();
  });

  it('memory is empty after module load and refreshAccessToken restores via cookie', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: undefined },
      writable: true,
    });
    Object.defineProperty(globalThis, 'BroadcastChannel', {
      configurable: true,
      value: TestBroadcastChannel,
    });

    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'restored-from-cookie',
            expires_in: 1800,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { refreshAccessToken, getAccessToken, hasValidAccessToken } =
      await import('../lib/auth');

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

    vi.restoreAllMocks();
  });

  it('attemptPageLoadRefresh returns true after cookie refresh restores a session', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: undefined },
      writable: true,
    });
    Object.defineProperty(globalThis, 'BroadcastChannel', {
      configurable: true,
      value: TestBroadcastChannel,
    });

    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            access_token: 'identity-session-restored',
            expires_in: 1800,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    globalThis.fetch = mockFetch;

    const { attemptPageLoadRefresh, getAccessToken, hasValidAccessToken } =
      await import('../lib/auth');

    expect(getAccessToken()).toBeNull();

    const ok = await attemptPageLoadRefresh();
    expect(ok).toBe(true);
    expect(hasValidAccessToken()).toBe(true);
    expect(getAccessToken()).toBe('identity-session-restored');

    vi.restoreAllMocks();
  });

  it('attemptPageLoadRefresh returns false and redirects to /setup on 401 for expired temporary sessions', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: undefined },
      writable: true,
    });
    Object.defineProperty(globalThis, 'BroadcastChannel', {
      configurable: true,
      value: TestBroadcastChannel,
    });

    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    globalThis.fetch = mockFetch;

    const replace = vi.fn();
    const originalLocation = window.location;
    const locationSpy = vi.spyOn(window, 'location', 'get').mockReturnValue({
      ...originalLocation,
      replace,
    } as unknown as Location);

    const { attemptPageLoadRefresh, getAccessToken } =
      await import('../lib/auth');

    const ok = await attemptPageLoadRefresh();
    expect(ok).toBe(false);
    expect(getAccessToken()).toBeNull();
    expect(replace).toHaveBeenCalledWith('/setup');

    locationSpy.mockRestore();
    vi.restoreAllMocks();
  });

  it('attemptPageLoadRefresh can suppress the legacy /setup redirect on expired sessions', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: undefined },
      writable: true,
    });
    Object.defineProperty(globalThis, 'BroadcastChannel', {
      configurable: true,
      value: TestBroadcastChannel,
    });

    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    globalThis.fetch = mockFetch;

    const replace = vi.fn();
    const originalLocation = window.location;
    const locationSpy = vi.spyOn(window, 'location', 'get').mockReturnValue({
      ...originalLocation,
      replace,
    } as unknown as Location);

    const { attemptPageLoadRefresh, getAccessToken } =
      await import('../lib/auth');

    const ok = await attemptPageLoadRefresh({
      redirectOnExpiredSession: false,
    });
    expect(ok).toBe(false);
    expect(getAccessToken()).toBeNull();
    expect(replace).not.toHaveBeenCalled();

    locationSpy.mockRestore();
    vi.restoreAllMocks();
  });

  it('attemptPageLoadRefresh returns false on 401 with read-only window.location (no throw)', async () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: { locks: undefined },
      writable: true,
    });
    Object.defineProperty(globalThis, 'BroadcastChannel', {
      configurable: true,
      value: TestBroadcastChannel,
    });

    const mockFetch = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 401 })),
    );
    globalThis.fetch = mockFetch;

    const { attemptPageLoadRefresh, getAccessToken } =
      await import('../lib/auth');

    const ok = await attemptPageLoadRefresh();
    expect(ok).toBe(false);
    expect(getAccessToken()).toBeNull();
    vi.restoreAllMocks();
  });
});
