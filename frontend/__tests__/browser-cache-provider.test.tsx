import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { AUTH_COORDINATION_KEY } from '../lib/authCookieCoordinator';
import { AuthCookieTestLocks } from './helpers/authCookieLocks';
import {
  completeEmailSignIn,
  completeEnrollment,
  clearLocalAuthState,
  getRefreshPromise,
} from '../lib/auth';

/**
 * Real-module provider integration tests for the P0.2 barrier gate: the real
 * frontend/lib/browserCache.ts state machine runs inside the real
 * AuthProvider (only the runtime auth-config seam is stubbed). Actual browser
 * cache erasure evidence is the parent's browser-fixture domain, not here;
 * these tests lock gating, failure UI, retry, local sign-out, and lifecycle
 * races ('endpointAcknowledged' is an endpoint-contract claim, never proof of
 * universal erasure).
 */

vi.mock('../lib/auth-config', () => ({
  AUTH_CONFIG_CACHE_TTL_MS: 60_000,
  fetchAuthConfig: vi.fn(async () => ({
    status: 'resolved' as const,
    config: {
      mode: 'hosted' as const,
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    },
  })),
  refreshAuthConfig: vi.fn(async () => ({
    status: 'resolved' as const,
    config: {
      mode: 'hosted' as const,
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    },
  })),
  subscribeAuthConfig: vi.fn(() => () => {}),
  getCachedAuthConfig: vi.fn(() => undefined),
  getCachedAuthConfigAgeMs: vi.fn(() => undefined),
}));

import { AuthProvider, useAuth } from '../components/AuthProvider';
import {
  ensureBrowserCacheCleared,
  getBrowserCacheState,
  invalidateBrowserCache,
} from '../lib/browserCache';
import {
  clearAuthState,
  getAccessToken,
  getAuthHeader,
  refreshAccessToken,
  setAccessToken,
} from '../lib/auth';

const originalFetch = globalThis.fetch;
const originalLocalStorage = globalThis.localStorage;

/**
 * Install usable shared metadata storage and FIFO Web Locks (jsdom has no
 * native locks). This is a primitive fixture, not a lease fallback. The real
 * auth/provider/coordinator modules persist across all tests and lifetimes.
 */
function installWorkingLocalStorage(): void {
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      get length() {
        return store.size;
      },
      key: (index: number) => [...store.keys()][index] ?? null,
      clear: () => store.clear(),
      getItem: (key: string) => store.get(key) ?? null,
      removeItem: (key: string) => {
        store.delete(key);
      },
      setItem: (key: string, value: string) => {
        store.set(key, String(value));
      },
    },
  });
}

function ok204NoStore(): Response {
  return new Response(null, {
    status: 204,
    headers: { 'cache-control': 'no-store' },
  });
}

type ClearResponder = (url: string) => Response | Promise<Response>;
let clearResponder: ClearResponder = () => ok204NoStore();
let clearRequests = 0;
let locks: AuthCookieTestLocks;
let fetchMock: ReturnType<
  typeof vi.fn<(input?: unknown, init?: RequestInit) => Promise<Response>>
>;

function failWithStatus(status: number): ClearResponder {
  return async () => new Response(null, { status });
}

function defineLocation(value: { pathname: string; setter?: string }): void {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      pathname: value.pathname,
      origin: 'http://localhost:3000',
      get href() {
        return '';
      },
      set href(next: string) {
        value.setter = next;
      },
    },
  });
}

function installTestWindow(): void {
  (globalThis as { fetch: unknown }).fetch = fetchMock;
  // jsdom reports http origins as insecure; the negative qualification path
  // is covered by the module unit tests, while provider tests assume a
  // qualified (secure-context) app window.
  Object.defineProperty(window, 'isSecureContext', {
    configurable: true,
    value: true,
  });
}

function resetWindow(): void {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      pathname: '/',
      origin: 'http://localhost:3000',
      href: 'http://localhost:3000/',
    },
  });
}

interface DeferredResponse {
  promise: Promise<Response>;
  resolve: (value: Response) => void;
  reject: (error: unknown) => void;
}

function deferredResponse(): DeferredResponse {
  let resolve!: (value: Response) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<Response>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  // Each case starts in an independent tab; local-exit persistence is exercised
  // across documents by the dedicated native-browser regressions.
  sessionStorage.clear();
  delete process.env.NEXT_PUBLIC_API_URL;
  clearResponder = () => ok204NoStore();
  clearRequests = 0;
  fetchMock = vi.fn(async (_input?: unknown, init?: RequestInit) => {
    const url =
      typeof _input === 'string'
        ? _input
        : String((_input as Request).url ?? '');
    const method = init?.method ?? 'GET';
    if (url === '/api/v1/auth/cache/clear' && method === 'POST') {
      clearRequests += 1;
      return clearResponder(url);
    }
    if (url === '/api/v1/auth/refresh' && method === 'POST') {
      return new Response(
        JSON.stringify({
          access_token: 'refreshed-session-token',
          expires_in: 1800,
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    }
    return new Response(
      JSON.stringify({ error: `unexpected fetch ${method} ${url}` }),
      { status: 501, headers: { 'content-type': 'application/json' } },
    );
  });
  resetWindow();
  installTestWindow();
  installWorkingLocalStorage();
  locks = new AuthCookieTestLocks();
  vi.stubGlobal('navigator', { locks });
  localStorage.setItem(
    AUTH_COORDINATION_KEY,
    JSON.stringify({ version: 1, epoch: 'provider-fixture', status: 'stable' }),
  );
  setAccessToken(
    'provider-initial-expired',
    Date.now() - 1000,
    'provider-fixture',
  );
  invalidateBrowserCache();
});

afterEach(async () => {
  clearLocalAuthState();
  for (let i = 0; i < 30; i++) await Promise.resolve();
  expect(locks.held).toBe(false);
  expect(locks.queue).toHaveLength(0);
  expect(getRefreshPromise()).toBeNull();
  vi.restoreAllMocks();
  (globalThis as { fetch: unknown }).fetch = originalFetch;
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: originalLocalStorage,
  });
  resetWindow();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  invalidateBrowserCache();
});

function ProviderGateFlow() {
  const auth = useAuth();
  return (
    <div>
      <button
        type="button"
        data-testid="simulate-signin"
        onClick={() => {
          auth.setAccessToken('rotated-account-token', Date.now() + 60_000);
        }}
      >
        sign-in
      </button>
      <span data-testid="protected-content">protected-ready</span>
      <button data-testid="normal-signout" onClick={() => void auth.logout()}>
        Normal sign-out
      </button>
    </div>
  );
}

describe('AuthProvider cache-clear gate (real module)', () => {
  it('verified rejected enrollment keeps A private until both coordination and the current cache attempt finish', async () => {
    const location = { pathname: '/', setter: '' };
    defineLocation(location);
    setAccessToken('active-a', Date.now() + 600_000);
    const body = deferredResponse();
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: unknown, init?: RequestInit) =>
      url === '/api/v1/auth/enroll/complete'
        ? body.promise
        : original(url, init),
    );
    render(
      <AuthProvider>
        <ProviderGateFlow />
      </AuthProvider>,
    );
    await screen.findByTestId('protected-content');
    expect(getAuthHeader()).toBe('Bearer active-a');
    let result!: ReturnType<typeof completeEnrollment>;
    act(() => {
      result = completeEnrollment('fictional-pending', '000000');
    });
    await act(async () => {
      await ensureBrowserCacheCleared();
    });
    expect(getBrowserCacheState().status).toBe('ready');
    expect(getAccessToken()).toBeNull();
    expect(getAuthHeader()).toBeNull();
    expect(screen.queryByTestId('protected-content')).toBeNull();
    await act(async () => {
      body.resolve(
        new Response(JSON.stringify({ detail: 'Invalid enrollment code' }), {
          status: 401,
        }),
      );
      expect((await result).success).toBe(false);
    });
    await screen.findByTestId('protected-content');
    expect(getAccessToken()).toBe('active-a');
    expect(getAuthHeader()).toBe('Bearer active-a');
    expect(location.setter).toBe('');
  });

  it('expired normal signout refreshes and revokes, staying hidden after cache acknowledges', async () => {
    const location = { pathname: '/auth', setter: '' };
    defineLocation(location);
    setAccessToken('expired-a', Date.now() - 1000);
    const refresh = deferredResponse();
    const revoke = deferredResponse();
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: unknown, init?: RequestInit) => {
      if (url === '/api/v1/auth/refresh') return refresh.promise;
      if (url === '/api/v1/auth/logout') return revoke.promise;
      return original(url, init);
    });
    render(
      <AuthProvider>
        <ProviderGateFlow />
      </AuthProvider>,
    );
    await screen.findByTestId('normal-signout');
    fireEvent.click(screen.getByTestId('normal-signout'));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) => call[0] === '/api/v1/auth/refresh'),
      ).toBe(true),
    );
    expect(screen.queryByTestId('protected-content')).toBeNull();
    // The token is private during a shared pending writer, even before revoke.
    expect(getAccessToken()).toBeNull();
    await act(async () =>
      refresh.resolve(
        new Response(
          JSON.stringify({ access_token: 'renewed-a', expires_in: 1800 }),
          { status: 200 },
        ),
      ),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) => call[0] === '/api/v1/auth/logout'),
      ).toBe(true),
    );
    const call = fetchMock.mock.calls.find(
      (call) => call[0] === '/api/v1/auth/logout',
    )!;
    expect(new Headers(call[1]?.headers).get('Authorization')).toBe(
      'Bearer renewed-a',
    );
    expect(getBrowserCacheState().status).toBe('ready');
    expect(screen.queryByTestId('protected-content')).toBeNull();
    await act(async () => revoke.resolve(new Response(null, { status: 204 })));
    expect(getAccessToken()).toBeNull();
    expect(location.setter).toBe('/auth');
  });

  it('a new B lifetime supersedes pending A logout without stale clear or redirect', async () => {
    const location = { pathname: '/auth', setter: '' };
    defineLocation(location);
    setAccessToken('a', Date.now() + 600_000);
    const revoke = deferredResponse();
    const original = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: unknown, init?: RequestInit) => {
      if (url === '/api/v1/auth/logout')
        return new Promise((resolve, reject) => {
          init!.signal!.addEventListener(
            'abort',
            () => reject(init!.signal!.reason),
            { once: true },
          );
          void revoke.promise.then(resolve);
        });
      if (url === '/api/v1/auth/email/complete')
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: 'b',
              expires_at: Date.now() / 1000 + 600,
            }),
            { status: 200 },
          ),
        );
      return original(url, init);
    });
    render(
      <AuthProvider>
        <ProviderGateFlow />
      </AuthProvider>,
    );
    await screen.findByTestId('normal-signout');
    fireEvent.click(screen.getByTestId('normal-signout'));
    await act(async () => {
      expect(
        (
          await completeEmailSignIn(
            'fictional-challenge',
            '000000',
            'temporary',
          )
        ).success,
      ).toBe(true);
    });
    await act(async () => revoke.resolve(new Response(null, { status: 204 })));
    expect(getAccessToken()).toBe('b');
    expect(location.setter).toBe('');
  });

  it.each(['revoke', 'refresh'])(
    'hung normal %s clears locally within ten seconds',
    async (phase) => {
      const location = { pathname: '/auth', setter: '' };
      defineLocation(location);
      setAccessToken(
        'a',
        phase === 'refresh' ? Date.now() - 1000 : Date.now() + 600_000,
      );
      const original = fetchMock.getMockImplementation()!;
      fetchMock.mockImplementation((url: unknown, init?: RequestInit) =>
        url ===
        (phase === 'refresh' ? '/api/v1/auth/refresh' : '/api/v1/auth/logout')
          ? new Promise((_resolve, reject) => {
              init!.signal!.addEventListener(
                'abort',
                () => reject(init!.signal!.reason),
                { once: true },
              );
            })
          : original(url, init),
      );
      render(
        <AuthProvider>
          <ProviderGateFlow />
        </AuthProvider>,
      );
      await screen.findByTestId('normal-signout');
      vi.useFakeTimers();
      fireEvent.click(screen.getByTestId('normal-signout'));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(getAccessToken()).toBeNull();
      expect(location.setter).toBe('/auth');
      vi.useRealTimers();
    },
  );
  it('holds children until the boot clear is acknowledged, then mounts them', async () => {
    const gate = deferredResponse();
    clearResponder = () => gate.promise;
    render(
      <AuthProvider>
        <ProviderGateFlow />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('auth-cache-gate')).toBeTruthy();
    });
    expect(screen.queryByTestId('protected-content')).toBeNull();
    expect(clearRequests).toBe(1);
    expect(
      fetchMock.mock.calls.some(
        (call: unknown[]) => call[0] === '/api/v1/auth/cache/clear',
      ),
    ).toBe(true);
    await act(async () => {
      gate.resolve(ok204NoStore());
    });
    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeTruthy();
    });
    expect(getBrowserCacheState().status).toBe('ready');
  });

  it('opens protected content on a successful boot clear without extra fetches', async () => {
    render(
      <AuthProvider>
        <ProviderGateFlow />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeTruthy();
    });
    expect(clearRequests).toBe(1);
    expect(getBrowserCacheState().status).toBe('ready');
    const snapshotBefore = getBrowserCacheState();
    // Ordinary same-lifetime refresh rotation keeps the completed barrier.
    const result = await refreshAccessToken();
    expect(result.success).toBe(true);
    expect(getBrowserCacheState()).toBe(snapshotBefore);
    expect(clearRequests).toBe(1);
    expect(getAuthHeader()).toBe('Bearer refreshed-session-token');
  });

  it('fails closed into the gate UI when the clear is not acknowledged', async () => {
    clearResponder = failWithStatus(500);
    render(
      <AuthProvider>
        <ProviderGateFlow />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText(/could not be acknowledged/)).toBeTruthy();
    });
    expect(screen.queryByTestId('protected-content')).toBeNull();
    expect(screen.getByTestId('auth-cache-gate-retry')).toBeTruthy();
    expect(screen.getByTestId('auth-cache-gate-signout')).toBeTruthy();
    // A failed barrier keeps sync protected reads blocked.
    expect(getAuthHeader()).toBeNull();
  });

  it('retry from the failed gate runs a fresh attempt and mounts children on success', async () => {
    clearResponder = failWithStatus(500);
    render(
      <AuthProvider>
        <ProviderGateFlow />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('auth-cache-gate-retry')).toBeTruthy();
    });
    const attemptsBefore = clearRequests;
    clearResponder = () => ok204NoStore();
    await act(async () => {
      fireEvent.click(screen.getByTestId('auth-cache-gate-retry'));
    });
    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeTruthy();
    });
    expect(clearRequests).toBeGreaterThan(attemptsBefore);
    expect(getBrowserCacheState().status).toBe('ready');
  });

  it('local sign-out from the failed gate does not wait on the clear failure', async () => {
    const location = { pathname: '/', setter: undefined } as unknown as {
      pathname: string;
      setter: string;
    };
    defineLocation(location);
    clearResponder = async () => {
      throw new Error('offline');
    };
    setAccessToken('signed-in-token', Date.now() + 60_000);
    invalidateBrowserCache();
    render(
      <AuthProvider>
        <ProviderGateFlow />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('auth-cache-gate-signout')).toBeTruthy();
    });
    clearRequests = 0;
    await act(async () => {
      fireEvent.click(screen.getByTestId('auth-cache-gate-signout'));
    });
    // Local sign-out is synchronous: it never awaits the failing attempt.
    expect(location.setter).toBe('/auth');
    expect(getAuthHeader()).toBeNull();
    // The auth lifetime changed; no attempt can release the newer lifetime.
    expect(getBrowserCacheState().status).not.toBe('ready');
  });

  it('new sign-in invalidates synchronously and blocks protected reads until the new clear is acknowledged', async () => {
    render(
      <AuthProvider>
        <ProviderGateFlow />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('protected-content')).toBeTruthy();
    });
    // The gate can open before the provider's boot refresh finishes; the
    // header becomes available once both the barrier and the token exist.
    await waitFor(() => {
      expect(getAuthHeader()).toBe('Bearer refreshed-session-token');
    });

    const stale = deferredResponse();
    let attempt = 0;
    clearResponder = () => {
      attempt += 1;
      return attempt === 1 ? stale.promise : ok204NoStore();
    };

    // New sign-in (the same wrapper the sign-in UI uses).
    await act(async () => {
      fireEvent.click(screen.getByTestId('simulate-signin'));
    });
    // Sync invalidation: a direct consumer cannot read a header until the new
    // lifetime's clear is acknowledged.
    expect(getAuthHeader()).toBeNull();
    await waitFor(() => {
      expect(getBrowserCacheState().status).toBe('pending');
    });
    await waitFor(() => {
      expect(screen.queryByTestId('protected-content')).toBeNull();
    });

    const waiter = ensureBrowserCacheCleared();
    // Local sign-out invalidates during the pending clear: the stale attempt
    // must not release the newer lifetime.
    clearAuthState();
    await act(async () => {
      stale.resolve(ok204NoStore());
    });
    await waiter;
    // Stale completion rejected; the waiter revalidated under the newest
    // lifetime and opened the barrier.
    expect(getBrowserCacheState().status).toBe('ready');
  });
});
