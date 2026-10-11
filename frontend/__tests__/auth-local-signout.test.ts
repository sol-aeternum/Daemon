import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as auth from '../lib/auth';
import {
  ensureBrowserCacheCleared,
  invalidateBrowserCache,
  startBrowserCacheClearAttempt,
} from '../lib/browserCache';
import { AUTH_COORDINATION_KEY } from '../lib/authCookieCoordinator';
import {
  AuthTestBroadcastChannel,
  createAuthTestStorage,
  FIXTURE_AUTH_EPOCH,
  installAuthCookieTestEnvironment,
} from './helpers/authCookieEnvironment';

const ack = () =>
  new Response(null, { status: 204, headers: { 'cache-control': 'no-store' } });
const identity = () =>
  new Response(
    JSON.stringify({
      access_token: 'fictional-b',
      expires_at: Date.now() / 1000 + 1800,
    }),
    { status: 200 },
  );
let tab: Storage;
const signIn = (signal?: AbortSignal) =>
  auth.completeEmailSignIn(
    'fictional',
    '000000',
    'temporary',
    undefined,
    signal,
  );

beforeEach(() => {
  tab = createAuthTestStorage();
  vi.stubGlobal('sessionStorage', tab);
  vi.stubGlobal('localStorage', createAuthTestStorage());
  installAuthCookieTestEnvironment(undefined, { broadcastChannel: true });
  Object.defineProperty(window, 'isSecureContext', {
    configurable: true,
    value: true,
  });
  vi.stubEnv('NEXT_PUBLIC_API_URL', '');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url) =>
      String(url).endsWith('/cache/clear') ? ack() : identity(),
    ),
  );
  auth.setAccessToken('fictional-a', Date.now() + 600_000, FIXTURE_AUTH_EPOCH);
});

afterEach(async () => {
  vi.restoreAllMocks();
  // Recover through the same explicit validated identity contract, never a
  // module-generation reset or an unconditional fence-clear test hook.
  vi.stubGlobal('sessionStorage', tab);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url) =>
      String(url).endsWith('/cache/clear') ? ack() : identity(),
    ),
  );
  const result = await signIn();
  await startBrowserCacheClearAttempt({ reason: 'retry' });
  expect(
    auth.completeIdentityReadiness(result, new AbortController().signal),
  ).toBe(true);
  auth.clearLocalAuthState();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('tab-local persistent exit, actual auth exposure paths', () => {
  it.each(['1', '', 'corrupt', '0'])(
    'any present marker %j blocks tokens, headers and automatic refresh',
    async (value) => {
      await ensureBrowserCacheCleared();
      tab.setItem(auth.LOCAL_SIGNOUT_KEY, value);
      expect(auth.getAccessToken()).toBeNull();
      expect(auth.getAuthHeader()).toBeNull();
      expect(auth.hasValidAccessToken()).toBe(false);
      vi.mocked(fetch).mockClear();
      expect(await auth.refreshAccessToken()).toMatchObject({
        success: false,
        preserveProtectedUrl: true,
      });
      expect(await auth.ensureAuthHeader()).toBeNull();
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it('a storage read failure stays closed after storage recovery until explicit validated identity', async () => {
    vi.spyOn(tab, 'getItem').mockImplementationOnce(() => {
      throw new Error('blocked');
    });
    expect((await auth.refreshAccessToken()).success).toBe(false);
    expect((await auth.refreshAccessToken()).success).toBe(false);
    expect(auth.getAccessToken()).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(['throw', 'ignored', 'read-back'])(
    'exit %s failure still clears immediately and reports the reload boundary',
    async (failure) => {
      await ensureBrowserCacheCleared();
      if (failure === 'throw')
        vi.spyOn(tab, 'setItem').mockImplementationOnce(() => {
          throw new Error('blocked');
        });
      if (failure === 'ignored')
        vi.spyOn(tab, 'setItem').mockImplementationOnce(() => {});
      if (failure === 'read-back')
        vi.spyOn(tab, 'getItem').mockImplementationOnce(() => {
          throw new Error('blocked');
        });
      const generation = auth.getAuthGeneration();
      const result = auth.signOutLocally();
      expect(result.success).toBe(false);
      expect(result.error).toContain('Reloading may restore');
      expect(auth.getAuthGeneration()).toBeGreaterThan(generation);
      expect(auth.getAccessToken()).toBeNull();
      expect(auth.getRefreshPromise()).toBeNull();
      expect((await auth.refreshAccessToken()).success).toBe(false);
      expect(auth.signOutLocally().success).toBe(true);
      expect(tab.getItem(auth.LOCAL_SIGNOUT_KEY)).toBe('1');
    },
  );

  it('successful current identity cannot retire before cache acknowledgement, then verified retirement permits exposure', async () => {
    auth.signOutLocally();
    const controller = new AbortController();
    const result = await signIn(controller.signal);
    expect(result.success).toBe(true);
    expect(auth.completeIdentityReadiness(result, controller.signal)).toBe(
      false,
    );
    expect(tab.getItem(auth.LOCAL_SIGNOUT_KEY)).toBe('1');
    expect(auth.getAccessToken()).toBeNull();
    await ensureBrowserCacheCleared();
    expect(auth.getAuthHeader()).toBeNull();
    expect(auth.completeIdentityReadiness(result, controller.signal)).toBe(
      true,
    );
    expect(tab.getItem(auth.LOCAL_SIGNOUT_KEY)).toBeNull();
    expect(auth.getAuthHeader()).toBe('Bearer fictional-b');
  });

  it.each(['throw', 'ignored', 'read-back'])(
    'removal %s failure keeps the validated identity fenced and retryable',
    async (failure) => {
      auth.signOutLocally();
      const result = await signIn();
      await ensureBrowserCacheCleared();
      if (failure === 'throw')
        vi.spyOn(tab, 'removeItem').mockImplementationOnce(() => {
          throw new Error('blocked');
        });
      if (failure === 'ignored')
        vi.spyOn(tab, 'removeItem').mockImplementationOnce(() => {});
      if (failure === 'read-back') {
        const remove = tab.removeItem;
        vi.spyOn(tab, 'removeItem').mockImplementationOnce((key) => {
          remove(key);
          vi.spyOn(tab, 'getItem').mockImplementationOnce(() => {
            throw new Error('blocked');
          });
        });
      }
      const signal = new AbortController().signal;
      expect(auth.completeIdentityReadiness(result, signal)).toBe(false);
      expect(auth.getAuthHeader()).toBeNull();
      expect((await auth.refreshAccessToken()).success).toBe(false);
      expect(auth.completeIdentityReadiness(result, signal)).toBe(true);
      expect(auth.getAuthHeader()).toBe('Bearer fictional-b');
    },
  );

  it.each([
    'cancel',
    'supersede',
    'cache-failure',
    'coordination-change',
    'peer-refresh',
  ])('%s cannot release successful fenced credentials', async (failure) => {
    auth.signOutLocally();
    const controller = new AbortController();
    const result = await signIn(controller.signal);
    await ensureBrowserCacheCleared();
    if (failure === 'cancel') controller.abort();
    if (failure === 'supersede')
      auth.setAccessToken('fictional-manual', Date.now() + 600_000);
    if (failure === 'cache-failure') {
      invalidateBrowserCache();
      vi.mocked(fetch).mockResolvedValueOnce(
        new Response(null, { status: 503 }),
      );
      await expect(ensureBrowserCacheCleared()).rejects.toThrow();
    }
    if (failure === 'coordination-change') {
      localStorage.setItem(
        AUTH_COORDINATION_KEY,
        JSON.stringify({
          version: 1,
          epoch: 'fictional-peer',
          status: 'stable',
        }),
      );
    }
    if (failure === 'peer-refresh') {
      const unsubscribe = auth.listenForAuthEvents(() => {});
      const peer = new AuthTestBroadcastChannel('daemon-auth');
      peer.postMessage({
        type: 'refreshed',
        tabId: 'peer',
        epoch: JSON.parse(localStorage.getItem(AUTH_COORDINATION_KEY)!).epoch,
      });
      await Promise.resolve();
      unsubscribe();
      peer.close();
    }
    expect(auth.completeIdentityReadiness(result, controller.signal)).toBe(
      false,
    );
    expect(tab.getItem(auth.LOCAL_SIGNOUT_KEY)).toBe('1');
    expect(auth.getAuthHeader()).toBeNull();
  });

  it.each(['rejected', 'malformed'])(
    '%s identity cannot remove a fence',
    async (failure) => {
      auth.signOutLocally();
      vi.mocked(fetch).mockResolvedValueOnce(
        failure === 'rejected'
          ? new Response(
              JSON.stringify({ detail: 'code_invalid_or_expired' }),
              { status: 401 },
            )
          : new Response(
              JSON.stringify({ access_token: 'fictional', expires_at: 0 }),
              { status: 200 },
            ),
      );
      const result = await signIn();
      await ensureBrowserCacheCleared();
      expect(
        auth.completeIdentityReadiness(result, new AbortController().signal),
      ).toBe(false);
      expect(tab.getItem(auth.LOCAL_SIGNOUT_KEY)).toBe('1');
      expect(auth.getAuthHeader()).toBeNull();
    },
  );
});
