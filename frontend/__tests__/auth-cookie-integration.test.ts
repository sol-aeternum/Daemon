import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as auth from '../lib/auth';
import { ensureBrowserCacheCleared } from '../lib/browserCache';
import {
  AUTH_COOKIE_LOCK,
  AUTH_COORDINATION_KEY,
  readAuthCoordination,
} from '../lib/authCookieCoordinator';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

class TestLocks {
  held = false;
  queue: Array<() => Promise<void>> = [];
  request = vi.fn(
    <T>(
      name: string,
      options: { signal: AbortSignal },
      action: () => Promise<T>,
    ) => {
      expect(name).toBe(AUTH_COOKIE_LOCK);
      const result = deferred<T>();
      const start = async () => {
        if (options.signal.aborted) {
          result.reject(options.signal.reason);
          return;
        }
        this.held = true;
        options.signal.removeEventListener('abort', cancel);
        try {
          result.resolve(await action());
        } catch (error) {
          result.reject(error);
        } finally {
          this.held = false;
          this.drain();
        }
      };
      const cancel = () => {
        this.queue = this.queue.filter((item) => item !== start);
        result.reject(options.signal.reason);
      };
      options.signal.addEventListener('abort', cancel, { once: true });
      this.queue.push(start);
      this.drain();
      return result.promise;
    },
  );
  drain() {
    if (!this.held) void this.queue.shift()?.();
  }
}

const flush = async () => {
  for (let i = 0; i < 30; i++) await Promise.resolve();
};
const ack = () =>
  new Response(null, { status: 204, headers: { 'cache-control': 'no-store' } });
const token = (value: string, expiresIn = 1800) =>
  new Response(JSON.stringify({ access_token: value, expires_in: expiresIn }), {
    status: 200,
  });
const identity = (value: string, expiresIn = 1800) =>
  new Response(
    JSON.stringify({
      access_token: value,
      expires_at: Date.now() / 1000 + expiresIn,
      expires_in: expiresIn,
    }),
    { status: 200 },
  );
let locks: TestLocks;
let storage: Map<string, string>;

type IdentityKind = 'email' | 'google' | 'setup' | 'enrollment';
function completeAttempt(kind: IdentityKind, signal?: AbortSignal) {
  if (kind === 'email')
    return auth.completeEmailSignIn(
      'fictional-challenge',
      '000000',
      'temporary',
      undefined,
      signal,
    );
  if (kind === 'google')
    return auth.completeGoogleSignIn(
      'fictional-challenge',
      'fictional-nonce',
      'fictional-proof',
      'temporary',
      undefined,
      signal,
    );
  if (kind === 'setup')
    return auth.completeSetup('fictional-setup', undefined, signal);
  return auth.completeEnrollment('fictional-pending', '000000', signal);
}
const verifiedRejections: Array<[IdentityKind, number, string]> = [
  ['email', 401, 'code_invalid_or_expired'],
  ['google', 401, 'google_sign_in_failed'],
  ['setup', 401, 'Invalid setup token'],
  ['setup', 409, 'setup_already_complete'],
  ['enrollment', 401, 'Invalid enrollment session'],
  ['enrollment', 401, 'Invalid enrollment code'],
  ['enrollment', 410, 'Enrollment session already consumed'],
  ['enrollment', 410, 'Enrollment session expired'],
];
const rejection = (status = 401, detail = 'Invalid enrollment code') =>
  new Response(JSON.stringify({ detail }), { status });

beforeEach(() => {
  locks = new TestLocks();
  storage = new Map();
  vi.stubGlobal('navigator', { locks });
  vi.stubGlobal(
    'BroadcastChannel',
    class {
      postMessage() {}
      addEventListener() {}
      removeEventListener() {}
    },
  );
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => {
      storage.set(key, value);
    },
    removeItem: (key: string) => {
      storage.delete(key);
    },
    get length() {
      return storage.size;
    },
    key: (index: number) => [...storage.keys()][index] ?? null,
  });
  Object.defineProperty(window, 'isSecureContext', {
    configurable: true,
    value: true,
  });
  vi.stubEnv('NEXT_PUBLIC_API_URL', '');
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ack()),
  );
  storage.set(
    AUTH_COORDINATION_KEY,
    JSON.stringify({ version: 1, epoch: 'fictional-a', status: 'stable' }),
  );
  auth.setAccessToken('fictional-a', Date.now() - 1000, 'fictional-a');
});

afterEach(async () => {
  auth.clearLocalAuthState();
  await flush();
  expect(locks.held).toBe(false);
  expect(locks.queue).toHaveLength(0);
  expect(auth.getRefreshPromise()).toBeNull();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('real auth module, one instance across A and B', () => {
  it('hung native A logout refresh -> bounded local exit -> B credentials -> B expiry -> fresh B refresh', async () => {
    vi.useFakeTimers();
    const upstreamA = deferred<Response>();
    let aSignal!: AbortSignal;
    vi.mocked(fetch).mockImplementationOnce(
      (_url, init) =>
        new Promise((yes, no) => {
          aSignal = init!.signal!;
          aSignal.addEventListener('abort', () => no(aSignal.reason), {
            once: true,
          });
          // Models native terminal abort while the independent upstream continues.
          void upstreamA.promise.then(yes);
        }),
    );
    const logout = auth.logoutCurrentSession();
    await flush();
    expect(locks.request).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(8000);
    const result = await logout;
    expect(result.success).toBe(false);
    expect(result.error).toContain('revocation unconfirmed');
    auth.clearAuthState();
    expect(auth.getAccessToken()).toBeNull();
    await flush();
    expect(aSignal.aborted).toBe(true);
    expect(locks.held).toBe(false);
    expect(readAuthCoordination()?.status).toBe('uncertain');
    vi.mocked(fetch).mockResolvedValueOnce(identity('fictional-b', 31));
    const signedIn = await auth.completeEmailSignIn(
      'fictional-challenge',
      '000000',
      'temporary',
    );
    expect(signedIn.success).toBe(true);
    expect(auth.isAuthResultCurrent(signedIn)).toBe(true);
    await ensureBrowserCacheCleared();
    expect(auth.getAuthHeader()).toBe('Bearer fictional-b');
    upstreamA.resolve(token('fictional-late-a'));
    await flush();
    expect(auth.getAccessToken()).toBe('fictional-b');
    expect(auth.isAuthResultCurrent(result)).toBe(false);
    await vi.advanceTimersByTimeAsync(2000);
    expect(auth.hasValidAccessToken()).toBe(false);
    vi.mocked(fetch).mockResolvedValueOnce(token('fictional-refreshed-b'));
    const refreshed = await auth.refreshAccessToken();
    expect(refreshed.success).toBe(true);
    expect(auth.getAuthHeader()).toBe('Bearer fictional-refreshed-b');
    expect(auth.getRefreshPromise()).toBeNull();
    expect(
      vi
        .mocked(fetch)
        .mock.calls.filter(([url]) => url === '/api/v1/auth/refresh'),
    ).toHaveLength(2);
    expect(
      vi.mocked(fetch).mock.calls.every(([, init]) => init?.keepalive !== true),
    ).toBe(true);
  });

  it('abort-ignoring A settles local caller but blocks B until real transport termination', async () => {
    vi.useFakeTimers();
    const transport = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(transport.promise);
    const a = auth.refreshAccessToken();
    await vi.advanceTimersByTimeAsync(10_000);
    expect((await a).success).toBe(false);
    auth.clearLocalAuthState();
    expect(locks.held).toBe(true);
    expect(readAuthCoordination()).toMatchObject({
      status: 'uncertain',
      attempt: expect.any(String),
    });
    const b = auth.completeEmailSignIn(
      'fictional-challenge',
      '000000',
      'temporary',
    );
    await vi.advanceTimersByTimeAsync(10_000);
    expect((await b).success).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
    transport.resolve(token('fictional-late-a'));
    await flush();
    expect(locks.held).toBe(false);
    expect(auth.getAccessToken()).toBeNull();
    vi.mocked(fetch).mockResolvedValueOnce(identity('fictional-b'));
    expect(
      (
        await auth.completeEmailSignIn(
          'fictional-challenge',
          '000000',
          'temporary',
        )
      ).success,
    ).toBe(true);
    expect(auth.getAccessToken()).toBe('fictional-b');
  });

  it('same-lifetime refresh joins, and waiter cancellation does not cancel its owner', async () => {
    const transport = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(transport.promise);
    const owner = auth.refreshAccessToken();
    const waiterController = new AbortController();
    const waiter = auth.refreshAccessToken({ signal: waiterController.signal });
    const other = auth.refreshAccessToken();
    waiterController.abort();
    expect((await waiter).success).toBe(false);
    expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(false);
    expect(locks.request).toHaveBeenCalledTimes(1);
    transport.resolve(token('fictional-refreshed-a'));
    expect((await owner).success).toBe(true);
    expect((await other).success).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('old single-flight finally cannot erase B refresh ownership', async () => {
    const oldTransport = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(oldTransport.promise);
    const a = auth.refreshAccessToken();
    auth.clearLocalAuthState();
    expect((await a).success).toBe(false);
    oldTransport.resolve(token('fictional-late-a'));
    await flush();
    vi.mocked(fetch).mockResolvedValueOnce(identity('fictional-b', 1));
    expect(
      (
        await auth.completeEmailSignIn(
          'fictional-b-challenge',
          '000000',
          'temporary',
        )
      ).success,
    ).toBe(true);
    const bTransport = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(bTransport.promise);
    const b = auth.refreshAccessToken();
    const ownedB = auth.getRefreshPromise();
    await flush();
    expect(ownedB).not.toBeNull();
    expect(auth.getRefreshPromise()).toBe(ownedB);
    bTransport.resolve(token('fictional-refreshed-b'));
    expect((await b).success).toBe(true);
  });
});

describe('one transaction for refresh and terminal writes', () => {
  it('healthy expired logout refreshes then revokes without nested locks or cache gating', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(token('fictional-refreshed-a'))
      .mockResolvedValueOnce(ack());
    const result = await auth.logoutCurrentSession();
    expect(result.success).toBe(true);
    expect(auth.isAuthResultCurrent(result)).toBe(true);
    expect(locks.request).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/auth/refresh',
      '/api/v1/auth/logout',
    ]);
    expect(
      new Headers(vi.mocked(fetch).mock.calls[1][1]?.headers).get(
        'Authorization',
      ),
    ).toBe('Bearer fictional-refreshed-a');
    expect(auth.getAccessToken()).toBeNull();
    expect(readAuthCoordination()?.status).toBe('signed-out');
    expect(readAuthCoordination()?.epoch).not.toBe('fictional-a');
  });

  it('lock waiting is included in expired logout +8s cutoff and never steals frozen lock', async () => {
    vi.useFakeTimers();
    locks.held = true;
    const logout = auth.logoutCurrentSession();
    await vi.advanceTimersByTimeAsync(8000);
    expect((await logout).error).toContain('revocation unconfirmed');
    expect(fetch).not.toHaveBeenCalled();
    expect(locks.held).toBe(true);
    expect(locks.queue).toHaveLength(0);
    locks.held = false;
  });

  it('refresh just before +8s leaves only the remaining overall budget for revoke', async () => {
    vi.useFakeTimers();
    const refresh = deferred<Response>();
    vi.mocked(fetch)
      .mockReturnValueOnce(refresh.promise)
      .mockImplementationOnce(
        (_url, init) =>
          new Promise((_yes, no) => {
            init!.signal!.addEventListener(
              'abort',
              () => no(init!.signal!.reason),
              { once: true },
            );
          }),
      );
    const logout = auth.logoutCurrentSession();
    await vi.advanceTimersByTimeAsync(7999);
    refresh.resolve(token('fictional-refreshed-a'));
    await flush();
    expect(fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(2001);
    expect((await logout).success).toBe(false);
    expect(vi.mocked(fetch).mock.calls[1][1]?.signal?.aborted).toBe(true);
  });

  it('absolute +8s cutoff prevents resumed refresh continuation from starting revoke', async () => {
    vi.useFakeTimers();
    const refresh = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(refresh.promise);
    const logout = auth.logoutCurrentSession();
    vi.setSystemTime(Date.now() + 8000);
    refresh.resolve(token('fictional-refreshed-a'));
    const result = await logout;
    expect(result.error).toContain('revocation unconfirmed');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('immediate local-only expired exit starts no refresh', async () => {
    expect(
      (await auth.logoutCurrentSession({ allowRefresh: false })).error,
    ).toBe('No active session');
    auth.clearLocalAuthState();
    expect(fetch).not.toHaveBeenCalled();
    expect(locks.request).not.toHaveBeenCalled();
    expect(auth.getAccessToken()).toBeNull();
  });

  it('device DELETE and its optional refresh share one lock', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(token('fictional-refreshed-a'))
      .mockResolvedValueOnce(ack());
    const result = await auth.revokeDevice('fictional-other');
    expect(result.success).toBe(true);
    expect(locks.request).toHaveBeenCalledTimes(1);
    expect(readAuthCoordination()?.epoch).toBe('fictional-a');
    expect(readAuthCoordination()?.status).toBe('stable');
    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/auth/refresh',
      '/api/v1/auth/devices/fictional-other',
    ]);
  });

  it('confirmed current-device revocation clears own state and changes shared epoch', async () => {
    auth.setAccessToken('fictional-a', Date.now() + 600_000);
    const result = await auth.revokeDevice('fictional-current', {
      current: true,
    });
    expect(result.success).toBe(true);
    expect(auth.isAuthResultCurrent(result)).toBe(true);
    expect(auth.getAccessToken()).toBeNull();
    expect(readAuthCoordination()?.status).toBe('signed-out');
  });
});

describe('verified rejected identity preserves the prior session, never creates readiness', () => {
  it.each(verifiedRejections)(
    '%s %s %s preserves active A and re-acknowledges its cache lifetime',
    async (kind, status, detail) => {
      auth.setAccessToken('fictional-a', Date.now() + 600_000);
      await ensureBrowserCacheCleared();
      expect(auth.getAuthHeader()).toBe('Bearer fictional-a');
      const before = auth.getAuthGeneration();
      const notices = vi.spyOn(auth._getChannel()!, 'postMessage');
      vi.mocked(fetch).mockResolvedValueOnce(rejection(status, detail));
      const result = await completeAttempt(kind);
      expect(result.success).toBe(false);
      expect(auth.isAuthResultCurrent(result)).toBe(true);
      expect(auth.getAuthGeneration()).toBeGreaterThan(before);
      expect(readAuthCoordination()).toEqual({
        version: 1,
        epoch: 'fictional-a',
        status: 'stable',
      });
      expect(auth.getAccessToken()).toBe('fictional-a');
      expect(auth.getAuthHeader()).toBeNull();
      expect(notices).not.toHaveBeenCalled();
      notices.mockRestore();
      await ensureBrowserCacheCleared();
      expect(auth.getAuthHeader()).toBe('Bearer fictional-a');
    },
  );

  it.each(verifiedRejections)(
    '%s %s %s preserves expired A so a fresh A rotation remains possible',
    async (kind, status, detail) => {
      vi.mocked(fetch).mockResolvedValueOnce(rejection(status, detail));
      const result = await completeAttempt(kind);
      expect(result.success).toBe(false);
      expect(auth.getAccessToken()).toBe('fictional-a');
      expect(auth.hasValidAccessToken()).toBe(false);
      expect(readAuthCoordination()?.epoch).toBe('fictional-a');
      vi.mocked(fetch).mockResolvedValueOnce(token('fictional-renewed-a'));
      expect((await auth.refreshAccessToken()).success).toBe(true);
      await ensureBrowserCacheCleared();
      expect(auth.getAuthHeader()).toBe('Bearer fictional-renewed-a');
    },
  );

  it.each(['uncertain', 'signed-out', 'missing'] as const)(
    'verified rejection cannot promote prior %s into cookie readiness',
    async (prior) => {
      if (prior === 'missing') storage.delete(AUTH_COORDINATION_KEY);
      else
        storage.set(
          AUTH_COORDINATION_KEY,
          JSON.stringify({ version: 1, epoch: 'fictional-a', status: prior }),
        );
      vi.mocked(fetch).mockResolvedValueOnce(rejection());
      expect((await completeAttempt('enrollment')).success).toBe(false);
      expect(readAuthCoordination()?.status).toBe(
        prior === 'missing' ? 'signed-out' : prior,
      );
      expect(auth.getAccessToken()).toBeNull();
      expect((await auth.refreshAccessToken()).success).toBe(false);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it('local-only exit is not undone by a later verified rejected credential', async () => {
    auth.clearLocalAuthState();
    vi.mocked(fetch).mockResolvedValueOnce(rejection());
    expect((await completeAttempt('enrollment')).success).toBe(false);
    expect(readAuthCoordination()).toMatchObject({
      epoch: 'fictional-a',
      status: 'stable',
    });
    expect(auth.getAccessToken()).toBeNull();
    expect((await auth.refreshAccessToken()).error).toBe('No active session');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each([
    [401, JSON.stringify({ detail: 'unknown_rejection' })],
    [403, JSON.stringify({ detail: 'Invalid enrollment code' })],
    [500, JSON.stringify({ detail: 'Invalid enrollment code' })],
    [401, JSON.stringify({ error: 'Invalid enrollment code' })],
    [401, JSON.stringify({ detail: ['Invalid enrollment code'] })],
    [401, JSON.stringify([{ detail: 'Invalid enrollment code' }])],
    [401, 'not-json'],
    [401, 'null'],
    [
      200,
      JSON.stringify({ access_token: 'fictional-invalid-b', expires_in: -1 }),
    ],
  ])(
    'unverified completion %s / %# never restores stable token exposure',
    async (status, body) => {
      vi.mocked(fetch).mockResolvedValueOnce(
        new Response(body as string, { status: status as number }),
      );
      expect((await completeAttempt('enrollment')).success).toBe(false);
      expect(readAuthCoordination()?.status).toBe('uncertain');
      expect(auth.getAccessToken()).toBeNull();
      expect((await auth.refreshAccessToken()).success).toBe(false);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it('another endpoint cannot use a known enrollment detail to restore A', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(rejection());
    expect((await completeAttempt('email')).success).toBe(false);
    expect(readAuthCoordination()?.status).toBe('uncertain');
    expect(auth.getAccessToken()).toBeNull();
  });

  it('A stays private during a body-hung credential, even after cache acknowledgement', async () => {
    auth.setAccessToken('fictional-a', Date.now() + 600_000);
    const body = deferred<ArrayBuffer>();
    vi.mocked(fetch).mockResolvedValueOnce({
      status: 401,
      statusText: 'Unauthorized',
      headers: new Headers(),
      arrayBuffer: () => body.promise,
    } as Response);
    const result = completeAttempt('enrollment');
    await ensureBrowserCacheCleared();
    expect(readAuthCoordination()).toMatchObject({
      epoch: 'fictional-a',
      status: 'pending',
    });
    expect(auth.getAccessToken()).toBeNull();
    expect(auth.getAuthHeader()).toBeNull();
    body.resolve(
      new TextEncoder().encode(
        JSON.stringify({ detail: 'Invalid enrollment code' }),
      ).buffer,
    );
    expect((await result).success).toBe(false);
    expect(auth.getAccessToken()).toBe('fictional-a');
    expect(auth.getAuthHeader()).toBe('Bearer fictional-a');
  });

  it('local exit during a rejection body prevents any late restoration', async () => {
    const body = deferred<ArrayBuffer>();
    vi.mocked(fetch).mockResolvedValueOnce({
      status: 401,
      statusText: 'Unauthorized',
      headers: new Headers(),
      arrayBuffer: () => body.promise,
    } as Response);
    const result = completeAttempt('enrollment');
    await flush();
    auth.clearLocalAuthState();
    expect((await result).success).toBe(false);
    expect(locks.held).toBe(true);
    body.resolve(
      new TextEncoder().encode(
        JSON.stringify({ detail: 'Invalid enrollment code' }),
      ).buffer,
    );
    await flush();
    expect(auth.getAccessToken()).toBeNull();
    expect(readAuthCoordination()?.status).toBe('uncertain');
    expect(locks.held).toBe(false);
  });

  it('new B credentials supersede an old rejected body without restoring A', async () => {
    const body = deferred<ArrayBuffer>();
    vi.mocked(fetch).mockResolvedValueOnce({
      status: 401,
      statusText: 'Unauthorized',
      headers: new Headers(),
      arrayBuffer: () => body.promise,
    } as Response);
    const old = completeAttempt('enrollment');
    await flush();
    vi.mocked(fetch).mockResolvedValueOnce(identity('fictional-b'));
    const b = completeAttempt('email');
    expect((await old).success).toBe(false);
    body.resolve(
      new TextEncoder().encode(
        JSON.stringify({ detail: 'Invalid enrollment code' }),
      ).buffer,
    );
    expect((await b).success).toBe(true);
    expect(auth.getAccessToken()).toBe('fictional-b');
    expect(auth.isAuthResultCurrent(await old)).toBe(false);
    expect(readAuthCoordination()?.epoch).not.toBe('fictional-a');
  });

  it('storage removal during rejection cannot recreate old readiness', async () => {
    const body = deferred<ArrayBuffer>();
    vi.mocked(fetch).mockResolvedValueOnce({
      status: 401,
      statusText: 'Unauthorized',
      headers: new Headers(),
      arrayBuffer: () => body.promise,
    } as Response);
    const result = completeAttempt('enrollment');
    await flush();
    storage.delete(AUTH_COORDINATION_KEY);
    body.resolve(
      new TextEncoder().encode(
        JSON.stringify({ detail: 'Invalid enrollment code' }),
      ).buffer,
    );
    expect((await result).success).toBe(false);
    expect(readAuthCoordination()).toBeNull();
    expect(auth.getAccessToken()).toBeNull();
  });

  it('timeout with a known rejected response body still quarantines A until actual body termination', async () => {
    vi.useFakeTimers();
    auth.setAccessToken('fictional-a', Date.now() + 600_000);
    const body = deferred<ArrayBuffer>();
    vi.mocked(fetch).mockResolvedValueOnce({
      status: 401,
      statusText: 'Unauthorized',
      headers: new Headers(),
      arrayBuffer: () => body.promise,
    } as Response);
    const result = completeAttempt('enrollment');
    await vi.advanceTimersByTimeAsync(10_000);
    expect((await result).success).toBe(false);
    expect(locks.held).toBe(true);
    expect(auth.getAccessToken()).toBeNull();
    expect(readAuthCoordination()).toMatchObject({
      status: 'uncertain',
      attempt: expect.any(String),
    });
    body.resolve(
      new TextEncoder().encode(
        JSON.stringify({ detail: 'Invalid enrollment code' }),
      ).buffer,
    );
    await flush();
    expect(locks.held).toBe(false);
    expect(readAuthCoordination()).toEqual({
      version: 1,
      epoch: 'fictional-a',
      status: 'uncertain',
    });
    expect(auth.getAccessToken()).toBeNull();
  });

  it('identity network failure never restores stable A from the retained private token', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(
      new Error('fictional network failure'),
    );
    expect((await completeAttempt('enrollment')).success).toBe(false);
    await flush();
    expect(readAuthCoordination()?.status).toBe('uncertain');
    expect(auth.getAccessToken()).toBeNull();
  });

  it('failure to persist verified rejection finalization cannot expose the private A token', async () => {
    auth.setAccessToken('fictional-a', Date.now() + 600_000);
    const original = localStorage.setItem;
    const writer = vi
      .spyOn(localStorage, 'setItem')
      .mockImplementation((key, value) => {
        if (
          key === AUTH_COORDINATION_KEY &&
          JSON.parse(value).status === 'stable'
        )
          throw new Error('fictional stable write failure');
        original(key, value);
      });
    vi.mocked(fetch).mockResolvedValueOnce(rejection());
    expect((await completeAttempt('enrollment')).success).toBe(false);
    await flush();
    expect(auth.getAccessToken()).toBeNull();
    expect(readAuthCoordination()?.status).toBe('uncertain');
    writer.mockRestore();
  });
});

describe('shared epoch and fail-closed exposure', () => {
  it.each(['stable', 'signed-out', 'uncertain', 'pending'] as const)(
    'an expired bound token queued behind %s replacement never receives an unbound retry',
    async (status) => {
      locks.held = true;
      const refresh = auth.refreshAccessToken();
      storage.set(
        AUTH_COORDINATION_KEY,
        JSON.stringify({
          version: 1,
          epoch: 'fictional-peer',
          status,
          ...(status === 'pending'
            ? { attempt: 'fictional-writer', kind: 'refresh' }
            : {}),
        }),
      );
      locks.held = false;
      locks.drain();
      expect((await refresh).success).toBe(false);
      expect(fetch).not.toHaveBeenCalled();
      expect(locks.request).toHaveBeenCalledTimes(1);
      expect(auth.getAuthHeader()).toBeNull();
    },
  );
  it('manual token installation cannot silently rebase an old token onto a different shared epoch', () => {
    storage.set(
      AUTH_COORDINATION_KEY,
      JSON.stringify({ version: 1, epoch: 'fictional-b', status: 'stable' }),
    );
    expect(() =>
      auth.setAccessToken('fictional-late-a', Date.now() + 600_000),
    ).toThrow('coordination');
    expect(auth.getAccessToken()).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('a missed storage notification still withholds stale protected headers', async () => {
    auth.setAccessToken('fictional-a', Date.now() + 600_000);
    await ensureBrowserCacheCleared();
    storage.set(
      AUTH_COORDINATION_KEY,
      JSON.stringify({ version: 1, epoch: 'fictional-b', status: 'stable' }),
    );
    expect(auth.getAuthHeader()).toBeNull();
    expect(auth.getSessionCoordinationProblem()).toContain(
      'Authentication changed',
    );
    const generation = auth.getAuthGeneration();
    window.dispatchEvent(
      new StorageEvent('storage', { key: AUTH_COORDINATION_KEY }),
    );
    expect(auth.getAuthGeneration()).toBeGreaterThan(generation);
    expect(auth.getAccessToken()).toBeNull();
  });

  it('late native-aborted A 401 never clears B or supplies a current redirect result', async () => {
    const upstream = deferred<Response>();
    vi.mocked(fetch).mockImplementationOnce(
      (_url, init) =>
        new Promise((yes, no) => {
          init!.signal!.addEventListener(
            'abort',
            () => no(init!.signal!.reason),
            { once: true },
          );
          void upstream.promise.then(yes);
        }),
    );
    const a = auth.refreshAccessToken();
    vi.mocked(fetch).mockResolvedValueOnce(identity('fictional-b'));
    const b = await auth.completeEmailSignIn(
      'fictional-challenge',
      '000000',
      'temporary',
    );
    expect(b.success).toBe(true);
    upstream.resolve(new Response(null, { status: 401 }));
    await flush();
    expect(auth.isAuthResultCurrent(await a)).toBe(false);
    expect(auth.getAccessToken()).toBe('fictional-b');
    expect(auth.isAuthResultCurrent(b)).toBe(true);
  });

  it('refresh 401 inside logout returns its own current completion after local clear', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(null, { status: 401 }));
    const result = await auth.logoutCurrentSession();
    expect(result.error).toBe('Session expired');
    expect(auth.isAuthResultCurrent(result)).toBe(true);
    expect(auth.getAccessToken()).toBeNull();
    expect(readAuthCoordination()?.status).toBe('signed-out');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('malformed refresh data never opens stable token exposure', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response('not-json', { status: 200 }),
    );
    expect((await auth.refreshAccessToken()).error).toBe(
      'Invalid refresh response',
    );
    expect(auth.getAccessToken()).toBeNull();
    expect(readAuthCoordination()?.status).toBe('uncertain');
  });
  it.each(['removed', 'malformed', 'replaced', 'uncertain', 'signed-out'])(
    '%s metadata withholds token/header and makes zero stale refresh requests',
    async (failure) => {
      auth.setAccessToken('fictional-a', Date.now() + 600_000);
      await ensureBrowserCacheCleared();
      expect(auth.getAuthHeader()).toBe('Bearer fictional-a');
      vi.mocked(fetch).mockClear();
      if (failure === 'removed') storage.delete(AUTH_COORDINATION_KEY);
      else if (failure === 'malformed')
        storage.set(AUTH_COORDINATION_KEY, 'not-json');
      else
        storage.set(
          AUTH_COORDINATION_KEY,
          JSON.stringify({
            version: 1,
            epoch: failure === 'replaced' ? 'fictional-b' : 'fictional-a',
            status: failure === 'replaced' ? 'stable' : failure,
          }),
        );
      expect(auth.getAccessToken()).toBeNull();
      expect(auth.getAuthHeader()).toBeNull();
      expect((await auth.refreshAccessToken()).success).toBe(false);
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it('unsupported locks cannot fall through to a lease request; local cleanup remains available', async () => {
    vi.stubGlobal('navigator', {});
    expect((await auth.refreshAccessToken()).error).toContain('unavailable');
    expect((await auth.completeSetup('fictional-setup')).error).toContain(
      'unavailable',
    );
    auth.clearAuthState();
    expect(auth.getAccessToken()).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(['email', 'google', 'setup', 'enrollment'])(
    'the %s cookie writer commits identity under the common lock',
    async (kind) => {
      vi.mocked(fetch).mockResolvedValueOnce(identity('fictional-b'));
      const result =
        kind === 'email'
          ? await auth.completeEmailSignIn(
              'fictional-challenge',
              '000000',
              'temporary',
            )
          : kind === 'google'
            ? await auth.completeGoogleSignIn(
                'fictional-challenge',
                'fictional-nonce',
                'fictional-proof',
                'temporary',
              )
            : kind === 'setup'
              ? await auth.completeSetup('fictional-setup')
              : await auth.completeEnrollment('fictional-pending', '000000');
      expect(result.success).toBe(true);
      expect(auth.isAuthResultCurrent(result)).toBe(true);
      expect(locks.request).toHaveBeenCalledTimes(1);
      expect(auth.getAccessToken()).toBe('fictional-b');
      expect(readAuthCoordination()).toMatchObject({ status: 'stable' });
      expect(readAuthCoordination()?.epoch).not.toBe('fictional-a');
    },
  );

  it('shared pending status suppresses protected exposure until same-epoch rotation commits', async () => {
    auth.setAccessToken('fictional-a', Date.now() + 600_000);
    await ensureBrowserCacheCleared();
    auth.setAccessToken('fictional-a', Date.now() - 1000);
    await ensureBrowserCacheCleared();
    const transport = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(transport.promise);
    const refresh = auth.refreshAccessToken();
    expect(readAuthCoordination()?.status).toBe('pending');
    expect(auth.getAuthHeader()).toBeNull();
    transport.resolve(token('fictional-refreshed-a'));
    expect((await refresh).success).toBe(true);
    expect(auth.getAuthHeader()).toBe('Bearer fictional-refreshed-a');
  });
});
