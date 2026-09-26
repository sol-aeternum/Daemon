import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const BACKEND_HOSTS = [
  'http://internal-broker-7.cluster.svc:8000',
  'http://daemon-backend-3.internal:8000',
];

async function loadGetHandler() {
  vi.resetModules();
  const route = await import('../app/api/entitlements/route');
  return route.GET;
}

function snapshotResponse() {
  return new Response(
    JSON.stringify({
      plan: 'free',
      capabilities: ['chat'],
      trial: null,
      limits: {},
    }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  );
}

beforeEach(() => {
  // Point the proxy at hosts that must never be named back to the browser.
  process.env.DAEMON_INTERNAL_API_URL = BACKEND_HOSTS[0];
  delete process.env.NEXT_PUBLIC_API_URL;
});

afterEach(() => {
  delete process.env.DAEMON_INTERNAL_API_URL;
  delete process.env.NEXT_PUBLIC_API_URL;
  vi.restoreAllMocks();
});

describe('entitlements proxy header allowlist', () => {
  it('never relays a client-asserted plan, tier, or capability hint', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(snapshotResponse());

    const req = new Request('http://localhost:3000/api/entitlements', {
      method: 'GET',
      headers: {
        authorization: 'Bearer daemon-token',
        cookie: 'daemon_refresh=cookie-value',
        'x-daemon-tier': 'max',
        'x-daemon-plan': 'power',
        'x-daemon-capabilities': 'premium_routing,video_generation',
        'x-daemon-is-admin': 'true',
        'x-daemon-entitlements': '{"plan":"power"}',
        'x-plan': 'power',
        'x-tier': 'byok',
        referer: 'http://localhost:3000/settings/profile',
      },
    });

    const GET = await loadGetHandler();
    await GET(req);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    const headers = new Headers(init.headers);

    for (const name of [
      'x-daemon-tier',
      'x-daemon-plan',
      'x-daemon-capabilities',
      'x-daemon-is-admin',
      'x-daemon-entitlements',
      'x-plan',
      'x-tier',
    ]) {
      expect(headers.get(name)).toBeNull();
    }

    // Nothing the client sent may smuggle a plan through the request body.
    expect(init.body).toBeUndefined();
    expect(headers.get('authorization')).toBe('Bearer daemon-token');
    expect(headers.get('cookie')).toBe('daemon_refresh=cookie-value');
  });

  it('requests the account-scoped path with the caller credentials', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(snapshotResponse());

    const req = new Request('http://localhost:3000/api/entitlements', {
      method: 'GET',
      headers: { authorization: 'Bearer daemon-token' },
    });

    const GET = await loadGetHandler();
    const response = await GET(req);

    expect(response.status).toBe(200);
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(`${BACKEND_HOSTS[0]}/users/me/entitlements`);
    expect(init.method).toBe('GET');
    expect(init.credentials).toBe('include');
    expect(init.cache).toBe('no-store');
  });

  it('preserves the caller query string on the backend path', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(snapshotResponse());

    const GET = await loadGetHandler();
    await GET(
      new Request('http://localhost:3000/api/entitlements?include=limits', {
        method: 'GET',
      }),
    );

    const [url] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(
      `${BACKEND_HOSTS[0]}/users/me/entitlements?include=limits`,
    );
  });

  it('passes an unauthenticated backend status through unchanged', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: { code: 'not_authenticated', message: 'Sign in' },
        }),
        {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );

    const GET = await loadGetHandler();
    const response = await GET(
      new Request('http://localhost:3000/api/entitlements', { method: 'GET' }),
    );

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toMatchObject({
      detail: { code: 'not_authenticated' },
    });
  });
});

describe('entitlements proxy transport failure', () => {
  it('does not leak internal hostnames when every backend is unreachable', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(
      new Error(`connect ECONNREFUSED 10.4.2.9:8000 via ${BACKEND_HOSTS[1]}`),
    );

    const GET = await loadGetHandler();
    const response = await GET(
      new Request('http://localhost:3000/api/entitlements', { method: 'GET' }),
    );

    expect(response.status).toBe(502);
    const body = await response.text();
    for (const secret of [
      ...BACKEND_HOSTS,
      '10.4.2.9',
      'ECONNREFUSED',
      '8000',
    ]) {
      expect(body).not.toContain(secret);
    }
  });

  it('falls through to the next backend before giving up', async () => {
    process.env.NEXT_PUBLIC_API_URL = BACKEND_HOSTS[1];
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockRejectedValueOnce(new Error('first backend refused'))
      .mockResolvedValueOnce(snapshotResponse());

    const GET = await loadGetHandler();
    const response = await GET(
      new Request('http://localhost:3000/api/entitlements', { method: 'GET' }),
    );

    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(response.status).toBe(200);
  });
});
