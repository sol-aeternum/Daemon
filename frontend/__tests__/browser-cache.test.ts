import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * Real-module unit tests for the P0.2 trusted browser-cache-clear barrier.
 * These lock the request guards, target resolution (prefix/default/dedup),
 * URL trust validation, exact-204 + readable Cache-Control no-store checks,
 * bounded timeout, failure/retry, stale-completion suppression and concurrent
 * dedup of frontend/lib/browserCache.ts against the approved contract. Actual
 * browser cache-erasure evidence lives in the parent's browser fixtures, not
 * here; 'endpointAcknowledged' must never be read as universal erasure.
 */

const ORIGINAL_NEXT_PUBLIC_API_URL = process.env.NEXT_PUBLIC_API_URL;
const ORIGINAL_NODE_ENV = process.env.NODE_ENV;
const ORIGINAL_FETCH = globalThis.fetch;

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function ok204NoStore(): Response {
  return new Response(null, {
    status: 204,
    headers: { 'cache-control': 'no-store' },
  });
}

function setSecureContext(value: boolean): void {
  Object.defineProperty(window, 'isSecureContext', {
    configurable: true,
    value,
  });
}

type FetchImpl = (
  input: string,
  init: RequestInit,
) => Response | Promise<Response>;

let fetchMock: ReturnType<typeof vi.fn>;
let currentImpl: FetchImpl;

function installSwitchableFetch(): ReturnType<typeof vi.fn> {
  fetchMock = vi.fn(
    (input?: unknown, init?: RequestInit) =>
      currentImpl(input as string, init as RequestInit) as Promise<Response>,
  );
  globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
  return fetchMock;
}

beforeEach(() => {
  delete process.env.NEXT_PUBLIC_API_URL;
  vi.stubEnv('NODE_ENV', 'test');
  setSecureContext(true);
  currentImpl = async () => ok204NoStore();
  installSwitchableFetch();
  // Every test imports a fresh barrier module: no state leakage.
  vi.resetModules();
});

afterEach(() => {
  vi.useRealTimers();
  if (ORIGINAL_NEXT_PUBLIC_API_URL === undefined) {
    delete process.env.NEXT_PUBLIC_API_URL;
  } else {
    process.env.NEXT_PUBLIC_API_URL = ORIGINAL_NEXT_PUBLIC_API_URL;
  }
  vi.unstubAllEnvs();
  globalThis.fetch = ORIGINAL_FETCH;
  vi.restoreAllMocks();
});

describe('cache clear target resolution', () => {
  it.each([
    ['https://api.example.com', 'https://api.example.com/v1/auth/cache/clear'],
    [
      'https://api.example.com/base/prefix/',
      'https://api.example.com/base/prefix/v1/auth/cache/clear',
    ],
  ])(
    'honors the configured base URL path prefix %s',
    async (apiUrl, expectedUrl) => {
      process.env.NEXT_PUBLIC_API_URL = apiUrl;
      const { startBrowserCacheClearAttempt, resolvedBrowserCacheTargets } =
        await import('../lib/browserCache');
      const plan = resolvedBrowserCacheTargets(window.location.origin);
      expect(plan.map((target) => target.url)).toContain(expectedUrl);
      const result = await startBrowserCacheClearAttempt({ reason: 'retry' });
      const urls = fetchMock.mock.calls.map((call) => call[0] as string);
      expect(urls).toContain(expectedUrl);
      expect(result.endpointAcknowledged).toBe(true);
      expect(result.targets.map((target) => target.url)).toContain(expectedUrl);
    },
  );

  it('uses the dev default localhost:8000 when no API URL is configured', async () => {
    vi.stubEnv('NODE_ENV', 'development');
    const { startBrowserCacheClearAttempt } =
      await import('../lib/browserCache');
    const result = await startBrowserCacheClearAttempt({ reason: 'retry' });
    const urls = fetchMock.mock.calls.map((call) => call[0] as string);
    expect(urls).toContain('http://localhost:8000/v1/auth/cache/clear');
    expect(result.endpointAcknowledged).toBe(true);
  });

  it('requests only the app proxy when no configured origin exists outside test dev', async () => {
    vi.stubEnv('NODE_ENV', 'test');
    const { startBrowserCacheClearAttempt } =
      await import('../lib/browserCache');
    await startBrowserCacheClearAttempt({ reason: 'retry' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/auth/cache/clear');
  });

  it('deduplicates when the configured origin equals the app origin', async () => {
    process.env.NEXT_PUBLIC_API_URL = window.location.origin;
    const { startBrowserCacheClearAttempt, resolvedBrowserCacheTargets } =
      await import('../lib/browserCache');
    // Target plan never places a redundant direct leg on the app origin.
    expect(
      resolvedBrowserCacheTargets(window.location.origin).filter(
        (target) => target.kind === 'direct-configured',
      ),
    ).toHaveLength(0);
    await startBrowserCacheClearAttempt({ reason: 'retry' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/auth/cache/clear');
  });

  it.each([
    ['http://127.0.0.5:8000'],
    ['http://[::1]:8000'],
    ['http://test-host.localhost:8081'],
  ])('accepts strict loopback HTTP target %s', async (apiUrl) => {
    process.env.NEXT_PUBLIC_API_URL = apiUrl;
    const { startBrowserCacheClearAttempt } =
      await import('../lib/browserCache');
    const result = await startBrowserCacheClearAttempt({ reason: 'retry' });
    expect(result.endpointAcknowledged).toBe(true);
    expect(fetchMock.mock.calls.map((call) => call[0])).toContain(
      `${apiUrl}/v1/auth/cache/clear`,
    );
  });

  it.each([
    ['http://insecure.example.com:8000'],
    ['http://10.0.0.1:8000'],
    ['http://192.168.0.1:8000'],
    ['ftp://files.example.com'],
    ['https://user:pass@api.example.com'],
    ['https://api.example.com/?filter=1'],
    ['https://api.example.com/#fragment'],
  ])(
    'rejects insecure or untrustworthy target %s before any request',
    async (apiUrl) => {
      process.env.NEXT_PUBLIC_API_URL = apiUrl;
      const { startBrowserCacheClearAttempt, resolvedBrowserCacheTargets } =
        await import('../lib/browserCache');
      // Target resolution itself never exposes the rejected direct target.
      expect(resolvedBrowserCacheTargets(window.location.origin)).toEqual([
        expect.objectContaining({ kind: 'app-proxy' }),
      ]);
      const result = await startBrowserCacheClearAttempt({ reason: 'retry' });
      expect(result.endpointAcknowledged).toBe(false);
      expect(result.error).toContain('does not permit secure cache clearing');
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );
});

describe('trusted endpoint request guards and checks', () => {
  it('sends the exact guarded request shape to each target', async () => {
    process.env.NEXT_PUBLIC_API_URL = 'https://api.example.com';
    const { startBrowserCacheClearAttempt } =
      await import('../lib/browserCache');
    const result = await startBrowserCacheClearAttempt({ reason: 'retry' });
    expect(result.endpointAcknowledged).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    for (const call of fetchMock.mock.calls) {
      const [, init] = call as [string, RequestInit];
      expect(init.method).toBe('POST');
      expect(init.credentials).toBe('include');
      expect(init.cache).toBe('no-store');
      expect(init.redirect).toBe('error');
      expect(init.body).toBeUndefined();
      expect(init.headers).toBeUndefined();
      expect(init.signal).toBeInstanceOf(AbortSignal);
    }
  });

  it('fails closed when the response is not exactly 204 (incl. trusted-origin 403 and unfollowed redirect)', async () => {
    const { startBrowserCacheClearAttempt } =
      await import('../lib/browserCache');
    for (const status of [200, 302, 403, 500]) {
      currentImpl = async () => new Response(null, { status });
      const result = await startBrowserCacheClearAttempt({ reason: 'retry' });
      expect(result.endpointAcknowledged).toBe(false);
      expect(result.error).toContain(`unexpected response status ${status}`);
    }
  });

  it('fails closed when readable Cache-Control no-store is missing', async () => {
    currentImpl = async () => new Response(null, { status: 204 });
    const { startBrowserCacheClearAttempt, ensureBrowserCacheCleared } =
      await import('../lib/browserCache');
    const result = await startBrowserCacheClearAttempt({ reason: 'retry' });
    expect(result.endpointAcknowledged).toBe(false);
    expect(result.error).toContain(
      'Cache-Control no-store missing or unreadable',
    );
    await expect(ensureBrowserCacheCleared()).rejects.toThrow(
      'Cache-Control no-store missing',
    );
  });

  it('never claims a JS-readable Clear-Site-Data check (Chromium hides CSD from page JS)', async () => {
    // The module must ack on exact 204 + readable no-store alone; the
    // on-wire Clear-Site-Data assertion belongs to browser fixtures.
    const { startBrowserCacheClearAttempt } =
      await import('../lib/browserCache');
    const result = await startBrowserCacheClearAttempt({ reason: 'retry' });
    expect(result.endpointAcknowledged).toBe(true);
  });

  it('fails closed outside a secure browser context without any network call', async () => {
    setSecureContext(false);
    const { startBrowserCacheClearAttempt, ensureBrowserCacheCleared } =
      await import('../lib/browserCache');
    const result = await startBrowserCacheClearAttempt({ reason: 'retry' });
    expect(result.endpointAcknowledged).toBe(false);
    expect(result.targets).toEqual([]);
    expect(result.error).toContain('secure browser context');
    expect(fetchMock).not.toHaveBeenCalled();
    await expect(ensureBrowserCacheCleared()).rejects.toThrow(
      'secure browser context',
    );
  });

  it('marks timed-out requests as failures after the bounded timeout', async () => {
    vi.useFakeTimers();
    const { BROWSER_CACHE_CLEAR_TIMEOUT_MS } =
      await import('../lib/browserCache');
    currentImpl = (_url, init) =>
      new Promise<Response>((_resolve, reject) => {
        if (!init.signal) throw new Error('Abort signal required');
        init.signal.addEventListener('abort', () =>
          reject(new Error('The operation was aborted')),
        );
      });
    const { startBrowserCacheClearAttempt } =
      await import('../lib/browserCache');
    const resultPromise = startBrowserCacheClearAttempt({ reason: 'retry' });
    await vi.advanceTimersByTimeAsync(BROWSER_CACHE_CLEAR_TIMEOUT_MS);
    const result = await resultPromise;
    expect(result.endpointAcknowledged).toBe(false);
    expect(result.error).toContain('timed out');
  });

  it('turns network errors into fail-closed attempt failures', async () => {
    currentImpl = async () => {
      throw new Error('network down');
    };
    const { startBrowserCacheClearAttempt } =
      await import('../lib/browserCache');
    const result = await startBrowserCacheClearAttempt({ reason: 'retry' });
    expect(result.endpointAcknowledged).toBe(false);
    expect(result.error).toContain('cache-clear request failed');
    expect(result.error).not.toContain('network down');
  });
});

describe('barrier lifecycle', () => {
  it('resolves ensure immediately and without fetches once acknowledged', async () => {
    const m = await import('../lib/browserCache');
    await m.ensureBrowserCacheCleared({ reason: 'initialization' });
    const ackCalls = fetchMock.mock.calls.length;
    await m.ensureBrowserCacheCleared();
    expect(fetchMock).toHaveBeenCalledTimes(ackCalls);
    expect(m.getBrowserCacheState()).toEqual({
      status: 'ready',
      generation: 1,
      error: null,
    });
  });

  it('deduplicates concurrent ensures into one attempt', async () => {
    const first = deferred<Response>();
    currentImpl = async () => first.promise;
    const m = await import('../lib/browserCache');
    const a = m.ensureBrowserCacheCleared();
    const b = m.ensureBrowserCacheCleared();
    first.resolve(ok204NoStore());
    await a;
    await b;
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('retry after failure uses a fresh attempt and opens the barrier', async () => {
    currentImpl = async () => new Response(null, { status: 500 });
    const m = await import('../lib/browserCache');
    await expect(m.ensureBrowserCacheCleared()).rejects.toThrow(
      'unexpected response status 500',
    );
    expect(m.getBrowserCacheState().status).toBe('failed');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // A failed barrier keeps rejecting without new fetches until retried.
    await expect(m.ensureBrowserCacheCleared()).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    currentImpl = async () => ok204NoStore();
    const retry = await m.startBrowserCacheClearAttempt({ reason: 'retry' });
    expect(retry.endpointAcknowledged).toBe(true);
    await m.ensureBrowserCacheCleared();
    expect(m.getBrowserCacheState().status).toBe('ready');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not release stale attempt completions after an auth lifetime change', async () => {
    const stale = deferred<Response>();
    currentImpl = async () => stale.promise;
    const m = await import('../lib/browserCache');
    const attempt = m.startBrowserCacheClearAttempt({ reason: 'sign-out' });
    await Promise.resolve();
    m.invalidateBrowserCache();
    stale.resolve(ok204NoStore());
    const result = await attempt;
    expect(result.endpointAcknowledged).toBe(true);
    // Stale completion: the newer lifetime is not ready (idle, not released).
    expect(m.getBrowserCacheState().status).toBe('idle');
    currentImpl = async () => ok204NoStore();
    await m.ensureBrowserCacheCleared();
    expect(m.getBrowserCacheState().status).toBe('ready');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('revalidates when a lifetime change happens while a waiter is joined', async () => {
    const stale = deferred<Response>();
    currentImpl = async () => stale.promise;
    const m = await import('../lib/browserCache');
    const staleWait = m.ensureBrowserCacheCleared({
      reason: 'initialization',
    });
    await Promise.resolve();
    m.invalidateBrowserCache();
    const revalidated = m.ensureBrowserCacheCleared();
    stale.resolve(ok204NoStore());
    // The joined stale attempt cannot satisfy the new lifetime; ensure's
    // loop starts a fresh attempt under the new lifetime (current impl).
    currentImpl = async () => ok204NoStore();
    await staleWait;
    await revalidated;
    expect(m.getBrowserCacheState().status).toBe('ready');
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});
