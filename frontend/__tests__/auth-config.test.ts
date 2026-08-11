import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const ORIGINAL_FETCH = globalThis.fetch;

beforeEach(() => {
  vi.resetModules();
  vi.useRealTimers();
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  globalThis.fetch = ORIGINAL_FETCH;
  vi.restoreAllMocks();
});

function mockFetchResponse(
  body: unknown,
  init: { status?: number } = {},
): void {
  const mocked = vi.mocked(globalThis.fetch);
  mocked.mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status: init.status ?? 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
}

describe('auth-config runtime client', () => {
  it('hits the /api/v1/auth/config proxy path', async () => {
    mockFetchResponse({
      mode: 'self_hosted',
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    });

    const { fetchAuthConfig } = await import('../lib/auth-config');
    await fetchAuthConfig();

    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledTimes(1);
    const url = vi.mocked(globalThis.fetch).mock.calls[0]?.[0];
    expect(String(url)).toContain('/api/v1/auth/config');
  });

  it('returns a typed AuthConfig with mode=hosted', async () => {
    mockFetchResponse({
      mode: 'hosted',
      email: { enabled: true },
      google: {
        enabled: true,
        clientId: 'public-id.apps.googleusercontent.com',
      },
    });

    const { fetchAuthConfig } = await import('../lib/auth-config');
    const result = await fetchAuthConfig();

    expect(result.status).toBe('resolved');
    if (result.status !== 'resolved') throw new Error('unreachable');
    expect(result.config.mode).toBe('hosted');
    expect(result.config.email.enabled).toBe(true);
    expect(result.config.google.enabled).toBe(true);
    expect(result.config.google.clientId).toBe(
      'public-id.apps.googleusercontent.com',
    );
  });

  it('returns a typed AuthConfig with mode=self_hosted', async () => {
    mockFetchResponse({
      mode: 'self_hosted',
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    });

    const { fetchAuthConfig } = await import('../lib/auth-config');
    const result = await fetchAuthConfig();

    expect(result.status).toBe('resolved');
    if (result.status !== 'resolved') throw new Error('unreachable');
    expect(result.config.mode).toBe('self_hosted');
  });

  it('returns status=error on network failure (does not throw)', async () => {
    vi.mocked(globalThis.fetch).mockRejectedValueOnce(
      new Error('network down'),
    );

    const { fetchAuthConfig } = await import('../lib/auth-config');
    const result = await fetchAuthConfig();

    expect(result.status).toBe('error');
  });

  it('returns status=error on non-2xx HTTP', async () => {
    mockFetchResponse({ error: 'boom' }, { status: 500 });

    const { fetchAuthConfig } = await import('../lib/auth-config');
    const result = await fetchAuthConfig();

    expect(result.status).toBe('error');
  });

  it('returns status=error on invalid JSON body', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(
      new Response('not-json', {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );

    const { fetchAuthConfig } = await import('../lib/auth-config');
    const result = await fetchAuthConfig();

    expect(result.status).toBe('error');
  });

  it('caches the result within the 60s TTL', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_000);
    mockFetchResponse({
      mode: 'hosted',
      email: { enabled: true },
      google: { enabled: true, clientId: 'cid' },
    });

    const { fetchAuthConfig, getCachedAuthConfig } =
      await import('../lib/auth-config');
    const r1 = await fetchAuthConfig();
    vi.spyOn(Date, 'now').mockReturnValue(60_999);
    const r2 = await fetchAuthConfig();

    expect(r1.status).toBe('resolved');
    expect(r2.status).toBe('resolved');
    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledTimes(1);
    expect(getCachedAuthConfig()?.mode).toBe('hosted');
  });

  it('re-fetches runtime config when the 60s TTL expires', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_000);
    mockFetchResponse({
      mode: 'hosted',
      email: { enabled: true },
      google: { enabled: true, clientId: 'first-client' },
    });
    mockFetchResponse({
      mode: 'self_hosted',
      email: { enabled: false },
      google: { enabled: false, clientId: '' },
    });

    const { fetchAuthConfig, getCachedAuthConfig } =
      await import('../lib/auth-config');
    await fetchAuthConfig();

    vi.spyOn(Date, 'now').mockReturnValue(61_000);
    expect(getCachedAuthConfig()).toBeUndefined();

    const refreshed = await fetchAuthConfig();

    expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledTimes(2);
    expect(refreshed).toEqual({
      status: 'resolved',
      config: {
        mode: 'self_hosted',
        email: { enabled: false },
        google: { enabled: false, clientId: '' },
      },
    });
    expect(getCachedAuthConfig()?.mode).toBe('self_hosted');
  });

  it('applies a 5s timeout via AbortController', async () => {
    vi.mocked(globalThis.fetch).mockImplementationOnce(
      (_input: RequestInfo | URL, init?: RequestInit) => {
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('aborted', 'AbortError'));
          });
        });
      },
    );

    const TIMEOUT_PATCH = 1;
    const originalSetTimeout = globalThis.setTimeout;
    globalThis.setTimeout = ((handler: () => void, ms?: number) => {
      if (ms === 5_000) {
        return originalSetTimeout(
          handler,
          TIMEOUT_PATCH,
        ) as unknown as ReturnType<typeof setTimeout>;
      }
      return originalSetTimeout(handler, ms);
    }) as typeof setTimeout;

    try {
      const { fetchAuthConfig } = await import('../lib/auth-config');
      const result = await fetchAuthConfig();
      expect(result.status).toBe('error');
    } finally {
      globalThis.setTimeout = originalSetTimeout;
    }
  });

  it('subscribe(cb) fires when first fetch resolves', async () => {
    mockFetchResponse({
      mode: 'hosted',
      email: { enabled: true },
      google: { enabled: true, clientId: 'cid' },
    });

    const { fetchAuthConfig, subscribeAuthConfig } =
      await import('../lib/auth-config');
    const cb = vi.fn();
    subscribeAuthConfig(cb);
    await fetchAuthConfig();

    expect(cb).toHaveBeenCalled();
    const arg = cb.mock.calls[0]?.[0] as { status: string };
    expect(arg.status).toBe('resolved');
  });

  it('does not leak secrets: ignores non-allowlisted response fields', async () => {
    mockFetchResponse({
      mode: 'hosted',
      email: { enabled: true },
      google: { enabled: true, clientId: 'cid' },
      secret: 'leaked',
      pepper: 'should-not-appear',
    });

    const { fetchAuthConfig } = await import('../lib/auth-config');
    const result = await fetchAuthConfig();

    expect(result.status).toBe('resolved');
    if (result.status !== 'resolved') throw new Error('unreachable');
    const json = JSON.stringify(result.config);
    expect(json).not.toContain('leaked');
    expect(json).not.toContain('should-not-appear');
  });
});
