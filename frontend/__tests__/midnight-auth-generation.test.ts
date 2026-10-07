import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

class AuthChannel extends EventTarget {
  static instance: AuthChannel;
  postMessage = vi.fn();
  constructor() {
    super();
    AuthChannel.instance = this;
  }
  receive(type: string) {
    this.dispatchEvent(
      new MessageEvent('message', { data: { type, tabId: 'another-tab' } }),
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
  vi.resetModules();
  vi.stubGlobal('BroadcastChannel', AuthChannel);
  vi.stubGlobal('navigator', {
    locks: { request: async (_name: string, run: () => unknown) => run() },
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('tab-local auth generation', () => {
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
      expect(auth.getAuthGeneration()).toBe(generation + 1);
    },
  );

  it('invalidates synchronously on direct token installations and local clears', async () => {
    const auth = await import('../lib/auth');
    const seen: Array<[number, string | null]> = [];
    const unsubscribe = auth.subscribeAuthGeneration(() => {
      seen.push([auth.getAuthGeneration(), auth.getAccessToken()]);
    });
    auth.setAccessToken('first', Date.now() + 120_000);
    auth.setAccessToken('first', Date.now() + 120_000);
    auth.setAccessToken('replacement', Date.now() + 120_000);
    auth.clearLocalAuthState();
    expect(seen).toEqual([
      [1, 'first'],
      [2, 'first'],
      [3, 'replacement'],
      [4, null],
    ]);
    unsubscribe();
    auth.clearAuthState();
    expect(seen).toHaveLength(4);
    expect(auth.getAuthGeneration()).toBe(5);
  });

  it('preserves the generation on same-tab cookie refresh', async () => {
    const auth = await import('../lib/auth');
    auth.setAccessToken('expired', 0);
    const generation = auth.getAuthGeneration();
    const changed = vi.fn();
    auth.subscribeAuthGeneration(changed);
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
    expect(await auth.refreshAccessToken()).toEqual({ success: true });
    expect(auth.getAccessToken()).toBe('rotated');
    expect(auth.getAuthGeneration()).toBe(generation);
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
      auth.subscribeAuthGeneration(() => {});
      const response = deferred<Response>();
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(response.promise));
      const pending = auth.refreshAccessToken();
      if (intervention === 'login')
        auth.setAccessToken('new', Date.now() + 120_000);
      else auth.clearAuthState();
      const generation = auth.getAuthGeneration();
      response.resolve(new Response(JSON.stringify({ access_token: 'stale' })));
      const result = await pending;
      expect(result.success).toBe(intervention === 'login');
      expect(auth.getAccessToken()).toBe(
        intervention === 'login' ? 'new' : null,
      );
      expect(auth.getAuthGeneration()).toBe(generation);
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
      auth.subscribeAuthGeneration(() => {});
      const response = deferred<Response>();
      vi.stubGlobal('fetch', vi.fn().mockReturnValue(response.promise));
      const pending = auth.refreshAccessToken();
      if (intervention === 'login')
        auth.setAccessToken('new', Date.now() + 120_000);
      else auth.clearAuthState();
      const generation = auth.getAuthGeneration();
      const broadcasts = AuthChannel.instance.postMessage.mock.calls.length;
      response.resolve(new Response(null, { status: 401 }));
      const result = await pending;
      expect(result.error).not.toBe('Session expired');
      expect(auth.getAccessToken()).toBe(
        intervention === 'login' ? 'new' : null,
      );
      expect(auth.getAuthGeneration()).toBe(generation);
      expect(AuthChannel.instance.postMessage.mock.calls).toHaveLength(
        broadcasts,
      );
    },
  );

  it('checks again after asynchronous response JSON parsing', async () => {
    const auth = await import('../lib/auth');
    auth.setAccessToken('old', 0);
    const body = deferred<{ access_token: string }>();
    const parsing = deferred<void>();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => {
          parsing.resolve();
          return body.promise;
        },
      }),
    );
    const pending = auth.refreshAccessToken();
    await parsing.promise;
    auth.setAccessToken('new', Date.now() + 120_000);
    body.resolve({ access_token: 'stale' });
    expect(await pending).toEqual({ success: true });
    expect(auth.getAccessToken()).toBe('new');
  });

  it('does not start a refresh queued behind a Web Lock after logout', async () => {
    const queued = deferred<() => unknown>();
    const release = deferred<void>();
    vi.stubGlobal('navigator', {
      locks: {
        request: async (_name: string, run: () => unknown) => {
          queued.resolve(run);
          await release.promise;
          return run();
        },
      },
    });
    const auth = await import('../lib/auth');
    auth.setAccessToken('old', 0);
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const pending = auth.refreshAccessToken();
    await queued.promise;
    auth.clearAuthState();
    release.resolve();
    expect(await pending).toEqual({
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
    expect(await pending).toEqual({
      success: false,
      error: 'Authentication changed',
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('remote refreshed invalidates once per message, preserves credentials, and precedes observers', async () => {
    const auth = await import('../lib/auth');
    auth.setAccessToken('local', Date.now() + 120_000);
    const changed = vi.fn();
    auth.subscribeAuthGeneration(changed);
    const observed: number[] = [];
    auth.listenForAuthEvents(() => observed.push(auth.getAuthGeneration()));
    auth.listenForAuthEvents(() => observed.push(auth.getAuthGeneration()));
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
    const items = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      get length() {
        return items.size;
      },
      key: (index: number) => [...items.keys()][index] ?? null,
      getItem: (key: string) => items.get(key) ?? null,
      setItem: (key: string, value: string) => void items.set(key, value),
      removeItem: (key: string) => void items.delete(key),
      clear: () => items.clear(),
    });
    const auth = await import('../lib/auth');
    const pending = await import('../lib/pendingSubmission');
    const pend = () => pending.keyForSubmission({ text: 'q' }, 'conv-a').key;

    const first = pend();
    auth.setAccessToken('signed-in', Date.now() + 120_000); // sign-in
    const second = pend();
    expect(second).not.toBe(first);
    AuthChannel.instance.receive('refreshed'); // another tab rotated its token
    expect(pend()).toBe(second);
    AuthChannel.instance.receive('cleared'); // signed out elsewhere
    expect(pend()).not.toBe(second);
  });

  it('a current refresh 401 still clears auth and broadcasts the established event', async () => {
    const auth = await import('../lib/auth');
    auth.setAccessToken('old', 0);
    auth.subscribeAuthGeneration(() => {});
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(null, { status: 401 })),
    );
    expect(await auth.refreshAccessToken()).toEqual({
      success: false,
      error: 'Session expired',
    });
    expect(auth.getAuthGeneration()).toBe(2);
    expect(auth.getAccessToken()).toBeNull();
    expect(AuthChannel.instance.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'cleared' }),
    );
  });
});
