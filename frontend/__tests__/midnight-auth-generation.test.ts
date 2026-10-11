import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as auth from '../lib/auth';
import { AUTH_COORDINATION_KEY } from '../lib/authCookieCoordinator';
import { AuthCookieTestLocks } from './helpers/authCookieLocks';

let locks: AuthCookieTestLocks;
let items: Map<string, string>;
const subscriptions: Array<() => void> = [];
const flush = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};
const identity = () =>
  new Response(
    JSON.stringify({
      access_token: 'new',
      expires_at: (Date.now() + 120_000) / 1000,
    }),
  );

class AuthChannel extends EventTarget {
  static instance: AuthChannel;
  postMessage = vi.fn();
  constructor() {
    super();
    AuthChannel.instance = this;
  }
  receive(type: string) {
    this.dispatchEvent(
      new MessageEvent('message', {
        data: { type, tabId: 'another-tab', epoch: 'fictional-generation-a' },
      }),
    );
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.stubGlobal('BroadcastChannel', AuthChannel);
  auth.clearLocalAuthState();
  locks = new AuthCookieTestLocks();
  vi.stubGlobal('navigator', { locks });
  items = new Map([
    [
      AUTH_COORDINATION_KEY,
      JSON.stringify({
        version: 1,
        epoch: 'fictional-generation-a',
        status: 'stable',
      }),
    ],
  ]);
  vi.stubGlobal('localStorage', {
    get length() {
      return items.size;
    },
    key: (index: number) => [...items.keys()][index] ?? null,
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => {
      items.set(key, value);
    },
    removeItem: (key: string) => {
      items.delete(key);
    },
  });
  auth.setAccessToken(
    'initial',
    Date.now() + 120_000,
    'fictional-generation-a',
  );
  auth._getChannel();
  AuthChannel.instance.postMessage.mockClear();
});
afterEach(() => {
  for (const stop of subscriptions.splice(0)) stop();
  auth.clearLocalAuthState();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('tab-local auth generation', () => {
  it('keeps readiness-only notifications separate from lifetime invalidation', () => {
    const generation = auth.getAuthGeneration();
    const changed = vi.fn();
    subscriptions.push(auth.subscribeAuthGeneration(changed));
    for (const status of ['pending', 'uncertain', 'stable']) {
      items.set(
        AUTH_COORDINATION_KEY,
        JSON.stringify({
          version: 1,
          epoch: 'fictional-generation-a',
          status,
          ...(status === 'stable'
            ? {}
            : { attempt: 'fictional-refresh', kind: 'refresh' }),
        }),
      );
      window.dispatchEvent(
        new StorageEvent('storage', { key: AUTH_COORDINATION_KEY }),
      );
      expect(auth.getAuthGeneration()).toBe(generation);
      expect(auth.getAccessToken()).toBe(
        status === 'stable' ? 'initial' : null,
      );
      expect(auth.getSessionCoordinationProblem() === null).toBe(
        status === 'stable',
      );
    }
    expect(changed).not.toHaveBeenCalled();
  });

  it('does not create new lifetimes from tokenless lifecycle notifications', () => {
    auth.clearLocalAuthState();
    const generation = auth.getAuthGeneration();
    const changed = vi.fn();
    subscriptions.push(auth.subscribeAuthGeneration(changed));
    window.dispatchEvent(new Event('pageshow'));
    document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(
      new StorageEvent('storage', { key: AUTH_COORDINATION_KEY }),
    );
    expect(auth.getAuthGeneration()).toBe(generation);
    expect(changed).not.toHaveBeenCalled();
    expect(auth.getSessionCoordinationProblem()).toContain('No active session');
  });

  it.each(['email', 'google', 'setup', 'enrollment'] as const)(
    '%s completion starts a new lifetime',
    async (kind) => {
      const auth = await import('../lib/auth');
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue(
          new Response(
            JSON.stringify({
              access_token: 'signed-in',
              expires_at: (Date.now() + 120_000) / 1000,
            }),
          ),
        ),
      );
      const generation = auth.getAuthGeneration();
      const result =
        kind === 'email'
          ? await auth.completeEmailSignIn('challenge', 'code', 'temporary')
          : kind === 'google'
            ? await auth.completeGoogleSignIn(
                'challenge',
                'nonce',
                'id-token',
                'temporary',
              )
            : kind === 'setup'
              ? await auth.completeSetup('setup')
              : await auth.completeEnrollment('pending', 'code');
      expect(result.success).toBe(true);
      // Intent fences old UI; committed B starts the new authenticated lifetime.
      expect(auth.getAuthGeneration()).toBe(generation + 2);
    },
  );

  it('invalidates synchronously on direct token installations and local clears', async () => {
    const auth = await import('../lib/auth');
    const seen: Array<[number, string | null]> = [];
    const generation = auth.getAuthGeneration();
    const unsubscribe = auth.subscribeAuthGeneration(() => {
      seen.push([auth.getAuthGeneration(), auth.getAccessToken()]);
    });
    auth.setAccessToken('first', Date.now() + 120_000);
    auth.setAccessToken('first', Date.now() + 120_000);
    auth.setAccessToken('replacement', Date.now() + 120_000);
    auth.clearLocalAuthState();
    expect(seen).toEqual([
      [generation + 1, 'first'],
      [generation + 2, 'first'],
      [generation + 3, 'replacement'],
      [generation + 4, null],
    ]);
    unsubscribe();
    auth.clearAuthState();
    expect(seen).toHaveLength(4);
    expect(auth.getAuthGeneration()).toBe(generation + 5);
  });

  it('preserves the generation on same-tab cookie refresh', async () => {
    const auth = await import('../lib/auth');
    auth.setAccessToken('expired', 0);
    const generation = auth.getAuthGeneration();
    const changed = vi.fn();
    subscriptions.push(auth.subscribeAuthGeneration(changed));
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ access_token: 'rotated', expires_in: 1800 }),
          ),
        ),
    );
    expect(await auth.refreshAccessToken()).toEqual({
      success: true,
      generation,
    });
    expect(auth.getAccessToken()).toBe('rotated');
    expect(auth.getAuthGeneration()).toBe(generation);
    // Readiness notifications are separate from destructive lifetime changes.
    expect(changed).not.toHaveBeenCalled();
    expect(AuthChannel.instance.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'refreshed' }),
    );
  });

  it.each(['login', 'logout'] as const)(
    'ignores stale refresh success after %s',
    async (intervention) => {
      const auth = await import('../lib/auth');
      auth.setAccessToken('old', 0);
      subscriptions.push(auth.subscribeAuthGeneration(() => {}));
      const response = deferred<Response>();
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(response.promise));
      const generation = auth.getAuthGeneration();
      const pending = auth.refreshAccessToken();
      vi.mocked(fetch).mockResolvedValueOnce(identity());
      const login =
        intervention === 'login' ? auth.completeSetup('fictional-setup') : null;
      if (!login) auth.clearAuthState();
      response.resolve(new Response(JSON.stringify({ access_token: 'stale' })));
      const result = await pending;
      if (login) expect((await login).success).toBe(true);
      await flush();
      expect(result.success).toBe(false);
      expect(auth.isAuthResultCurrent(result)).toBe(false);
      expect(auth.getAccessToken()).toBe(
        intervention === 'login' ? 'new' : null,
      );
      expect(auth.getAuthGeneration()).toBe(generation + (login ? 2 : 1));
      expect(
        AuthChannel.instance.postMessage.mock.calls.some(
          ([event]) => event.type === 'refreshed',
        ),
      ).toBe(false);
    },
  );

  it.each(['login', 'logout'] as const)(
    'ignores stale refresh 401 after %s (no stale expired-session redirect)',
    async (intervention) => {
      const auth = await import('../lib/auth');
      auth.setAccessToken('old', 0);
      subscriptions.push(auth.subscribeAuthGeneration(() => {}));
      const response = deferred<Response>();
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(response.promise));
      const generation = auth.getAuthGeneration();
      const pending = auth.refreshAccessToken();
      vi.mocked(fetch).mockResolvedValueOnce(identity());
      const login =
        intervention === 'login' ? auth.completeSetup('fictional-setup') : null;
      if (!login) auth.clearAuthState();
      const broadcasts = AuthChannel.instance.postMessage.mock.calls.length;
      response.resolve(new Response(null, { status: 401 }));
      const result = await pending;
      if (login) expect((await login).success).toBe(true);
      await flush();
      expect(result.error).not.toBe('Session expired');
      expect(auth.getAccessToken()).toBe(
        intervention === 'login' ? 'new' : null,
      );
      expect(auth.getAuthGeneration()).toBe(generation + (login ? 2 : 1));
      expect(AuthChannel.instance.postMessage.mock.calls).toHaveLength(
        broadcasts,
      );
    },
  );

  it('checks again after asynchronous response JSON parsing', async () => {
    const auth = await import('../lib/auth');
    auth.setAccessToken('old', 0);
    const body = deferred<ArrayBuffer>();
    const parsing = deferred<void>();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        status: 200,
        headers: new Headers(),
        arrayBuffer: () => {
          parsing.resolve();
          return body.promise;
        },
      }),
    );
    const pending = auth.refreshAccessToken();
    await parsing.promise;
    vi.mocked(fetch).mockResolvedValueOnce(identity());
    const login = auth.completeSetup('fictional-setup');
    body.resolve(
      new TextEncoder().encode(
        JSON.stringify({ access_token: 'stale', expires_in: 1800 }),
      ).buffer,
    );
    expect((await pending).success).toBe(false);
    expect((await login).success).toBe(true);
    expect(auth.getAccessToken()).toBe('new');
  });

  it('does not start a refresh queued behind a Web Lock after logout', async () => {
    locks.held = true;
    const auth = await import('../lib/auth');
    auth.setAccessToken('old', 0);
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const pending = auth.refreshAccessToken();
    expect(locks.queue).toHaveLength(1);
    auth.clearAuthState();
    locks.held = false;
    locks.drain();
    expect(await pending).toMatchObject({
      success: false,
      error: 'Authentication changed',
    });
    expect(fetch).not.toHaveBeenCalled();
    expect(auth.getAccessToken()).toBeNull();
  });

  it('stale network rejection does not fall back into another refresh after logout', async () => {
    const response = deferred<Response>();
    const auth = await import('../lib/auth');
    auth.setAccessToken('old', 0);
    const fetch = vi.fn().mockReturnValue(response.promise);
    vi.stubGlobal('fetch', fetch);
    const pending = auth.refreshAccessToken();
    auth.clearLocalAuthState();
    response.reject(new Error('network'));
    expect(await pending).toMatchObject({
      success: false,
      error: 'Authentication changed',
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('remote refreshed invalidates once per message, preserves credentials, and precedes observers', async () => {
    const auth = await import('../lib/auth');
    auth.setAccessToken('local', Date.now() + 120_000);
    const changed = vi.fn();
    subscriptions.push(auth.subscribeAuthGeneration(changed));
    const observed: number[] = [];
    subscriptions.push(
      auth.listenForAuthEvents(() => observed.push(auth.getAuthGeneration())),
    );
    subscriptions.push(
      auth.listenForAuthEvents(() => observed.push(auth.getAuthGeneration())),
    );
    const generation = auth.getAuthGeneration();
    AuthChannel.instance.receive('refreshed');
    expect(auth.getAuthGeneration()).toBe(generation + 1);
    expect(changed).toHaveBeenCalledTimes(1);
    expect(observed).toEqual([generation + 1, generation + 1]);
    expect(auth.getAccessToken()).toBe('local');
    AuthChannel.instance.receive('cleared');
    expect(auth.getAuthGeneration()).toBe(generation + 2);
    expect(auth.getAccessToken()).toBeNull();
  });

  it('clears pending submission keys on sign-in and sign-out, never on a remote token refresh', async () => {
    const auth = await import('../lib/auth');
    const pendingSubmissions = await import('../lib/pendingSubmission');
    const meta = {
      scope: 'conv-a',
      requestConversationId: 'conv-a',
      model: 'auto',
      provider: null,
    };
    const pending = () => pendingSubmissions.unresolvedSubmissions().length;

    pendingSubmissions.registerSubmission('k1', meta);
    auth.setAccessToken('signed-in', Date.now() + 120_000); // sign-in
    expect(pending()).toBe(0);
    pendingSubmissions.registerSubmission('k2', meta);
    AuthChannel.instance.receive('refreshed'); // another tab rotated its token
    expect(pending()).toBe(1);
    AuthChannel.instance.receive('cleared'); // signed out elsewhere
    expect(pending()).toBe(0);
  });

  it('a current refresh 401 still clears auth and broadcasts the established event', async () => {
    const auth = await import('../lib/auth');
    auth.setAccessToken('old', 0);
    subscriptions.push(auth.subscribeAuthGeneration(() => {}));
    const generation = auth.getAuthGeneration();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(null, { status: 401 })),
    );
    expect(await auth.refreshAccessToken()).toEqual({
      success: false,
      error: 'Session expired',
      generation: generation + 1,
    });
    expect(auth.getAuthGeneration()).toBe(generation + 1);
    expect(auth.getAccessToken()).toBeNull();
    expect(AuthChannel.instance.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'cleared' }),
    );
  });
});
