import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import * as auth from '../lib/auth';
import * as cache from '../lib/browserCache';
import {
  AUTH_COORDINATION_KEY,
  readAuthCoordination,
} from '../lib/authCookieCoordinator';
import { AuthCookieTestLocks } from './helpers/authCookieLocks';

let locks: AuthCookieTestLocks;
const identity = () =>
  new Response(
    JSON.stringify({
      access_token: 'fictional-b',
      expires_at: (Date.now() + 600_000) / 1000,
    }),
  );
const flush = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

const ack = () =>
  new Response(null, { status: 204, headers: { 'Cache-Control': 'no-store' } });
beforeEach(() => {
  auth.clearLocalAuthState();
  locks = new AuthCookieTestLocks();
  vi.stubGlobal('navigator', { locks });
  const storage = new Map([
    [
      AUTH_COORDINATION_KEY,
      JSON.stringify({ version: 1, epoch: 'fictional-a', status: 'stable' }),
    ],
  ]);
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => {
      storage.set(key, value);
    },
    removeItem: (key: string) => {
      storage.delete(key);
    },
  });
  auth.setAccessToken('fictional-a', Date.now() + 600_000, 'fictional-a');
  vi.stubEnv('NEXT_PUBLIC_API_URL', '');
  vi.stubEnv('NODE_ENV', 'test');
  Object.defineProperty(window, 'isSecureContext', {
    configurable: true,
    value: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ack()),
  );
});

it('local-only expired signout never starts credential refresh', async () => {
  auth.setAccessToken('expired-a', Date.now() - 1000);
  expect(await auth.logoutCurrentSession({ allowRefresh: false })).toEqual({
    success: false,
    error: 'No active session',
    generation: auth.getAuthGeneration(),
  });
  auth.clearAuthState();
  expect(fetch).not.toHaveBeenCalled();
  expect(auth.getAccessToken()).toBeNull();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it('forced clearing withholds readiness and auth headers until the new attempt finishes', async () => {
  auth.setAccessToken('fictional-a', Date.now() + 600_000);
  await cache.ensureBrowserCacheCleared();
  let release!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const clearing = cache.startBrowserCacheClearAttempt({ reason: 'sign-out' });
  expect(cache.getBrowserCacheState().status).toBe('pending');
  expect(auth.getAuthHeader()).toBeNull();
  release(ack());
  await clearing;
  expect(auth.getAuthHeader()).toBe('Bearer fictional-a');
});

it('a token waiter for A never returns B after a switch during pending cleanup', async () => {
  auth.setAccessToken('fictional-a', Date.now() + 600_000);
  let release!: (response: Response) => void;
  vi.mocked(fetch).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const token = auth.refreshIfNeeded();
  vi.mocked(fetch).mockResolvedValueOnce(identity());
  expect(
    (await auth.completeEmailSignIn('fictional-proof', '000000', 'temporary'))
      .success,
  ).toBe(true);
  release(ack());
  expect(await token).toBeNull();
  expect(await auth.refreshIfNeeded()).toBe('fictional-b');
});

it('failed cleanup returns neither a direct token nor an auth header', async () => {
  auth.setAccessToken('fictional-b', Date.now() + 600_000);
  vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 503 }));
  expect(await auth.refreshIfNeeded()).toBeNull();
  expect(await auth.ensureAuthHeader()).toBeNull();
});

it('an A refresh resolved after sign-in B cannot return B or authorize the queued history read', async () => {
  auth.setAccessToken('fictional-expired-a', Date.now() - 1000);
  let release!: (response: Response) => void;
  vi.mocked(fetch).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const queuedToken = auth.refreshIfNeeded();
  expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/v1/auth/refresh');
  vi.mocked(fetch).mockResolvedValueOnce(identity());
  const b = auth.completeEmailSignIn('fictional-proof', '000000', 'temporary');
  expect(locks.held).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(1);
  release(
    new Response(
      JSON.stringify({
        access_token: 'fictional-refreshed-a',
        expires_in: 1800,
      }),
      { status: 200 },
    ),
  );
  expect(await queuedToken).toBeNull();
  expect((await b).success).toBe(true);
  expect(cache.getBrowserCacheState().status).toBe('idle');
  expect(auth.getAuthHeader()).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(await auth.refreshIfNeeded()).toBe('fictional-b');
});

it('invalid configuration performs no requests and never reflects userinfo/query secrets', async () => {
  vi.stubEnv(
    'NEXT_PUBLIC_API_URL',
    'https://fictional-user:fictional-password@example.invalid/?key=fictional-secret',
  );
  const result = await cache.startBrowserCacheClearAttempt({ reason: 'retry' });
  expect(result.endpointAcknowledged).toBe(false);
  expect(fetch).not.toHaveBeenCalled();
  expect(JSON.stringify(result)).not.toMatch(
    /fictional-user|fictional-password|fictional-secret/,
  );
});

it('logout carries A without keepalive, is bounded, and local exit cannot release an unterminated transport', async () => {
  vi.useFakeTimers();
  auth.setAccessToken('fictional-a', Date.now() + 600_000);
  let release!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  const logout = auth.logoutCurrentSession();
  expect(auth.getAccessToken()).toBeNull();
  const [url, init] = vi.mocked(fetch).mock.calls[0];
  expect(url).toBe('/api/v1/auth/logout');
  expect(new Headers(init?.headers).get('Authorization')).toBe(
    'Bearer fictional-a',
  );
  expect(init?.keepalive).toBe(false);
  await vi.advanceTimersByTimeAsync(10_000);
  expect(await logout).toMatchObject({
    success: false,
    error: 'Session operation timed out',
  });
  expect(init?.signal?.aborted).toBe(true);
  expect(locks.held).toBe(true);
  auth.clearAuthState();
  release(new Response(null, { status: 204 }));
  await flush();
  expect(locks.held).toBe(false);
  expect(auth.getAccessToken()).toBeNull();
  expect(readAuthCoordination()?.status).toBe('uncertain');
});
