import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const INTERNAL = 'http://internal-broker-7.cluster.svc:8000';
const PUBLIC = 'http://public-backend.fixture:8000';

type AuthGet = (
  req: Request,
  context: { params: Promise<{ path: string[] }> },
) => Promise<Response>;
type EntitlementsGet = (req: Request) => Promise<Response>;

async function loadProxies(): Promise<{
  authGet: AuthGet;
  entitlementsGet: EntitlementsGet;
}> {
  vi.resetModules();
  const auth = await import('../app/api/v1/auth/[...path]/route');
  const entitlements = await import('../app/api/entitlements/route');
  return {
    authGet: auth.GET,
    entitlementsGet: entitlements.GET,
  };
}

const authRequest = () =>
  new Request('http://localhost:3000/api/v1/auth/config', {
    method: 'GET',
    headers: { cookie: 'daemon_refresh=cookie-value' },
  });

const authContext = { params: Promise.resolve({ path: ['config'] }) };

const entitlementsRequest = () =>
  new Request('http://localhost:3000/api/entitlements', { method: 'GET' });

beforeEach(() => {
  delete process.env.DAEMON_INTERNAL_API_URL;
  delete process.env.NEXT_PUBLIC_API_URL;
});

afterEach(() => {
  delete process.env.DAEMON_INTERNAL_API_URL;
  delete process.env.NEXT_PUBLIC_API_URL;
  vi.restoreAllMocks();
});

describe('bootstrap proxy authority', () => {
  it('sends auth and entitlements to the same selected internal host', async () => {
    process.env.DAEMON_INTERNAL_API_URL = INTERNAL;
    process.env.NEXT_PUBLIC_API_URL = PUBLIC;
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 200 }));

    const { authGet, entitlementsGet } = await loadProxies();
    await authGet(authRequest(), authContext);
    await entitlementsGet(entitlementsRequest());

    const urls = fetchSpy.mock.calls.map(([url]) => String(url));
    expect(urls).toEqual([
      `${INTERNAL}/v1/auth/config`,
      `${INTERNAL}/users/me/entitlements`,
    ]);
  });

  it('sends both to the public base when the internal override is empty', async () => {
    process.env.DAEMON_INTERNAL_API_URL = '';
    process.env.NEXT_PUBLIC_API_URL = `${PUBLIC}/daemon/`;
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 200 }));

    const { authGet, entitlementsGet } = await loadProxies();
    await authGet(authRequest(), authContext);
    await entitlementsGet(entitlementsRequest());

    const urls = fetchSpy.mock.calls.map(([url]) => String(url));
    expect(urls).toEqual([
      `${PUBLIC}/daemon/v1/auth/config`,
      `${PUBLIC}/daemon/users/me/entitlements`,
    ]);
  });

  it('refuses an invalid selection in both proxies without any fetch', async () => {
    process.env.DAEMON_INTERNAL_API_URL = 'http://backend:8000?replay=1';
    process.env.NEXT_PUBLIC_API_URL = PUBLIC;
    const fetchSpy = vi.spyOn(globalThis, 'fetch');

    const { authGet, entitlementsGet } = await loadProxies();
    for (const response of [
      await authGet(authRequest(), authContext),
      await entitlementsGet(entitlementsRequest()),
    ]) {
      expect(response.status).toBe(502);
      expect(response.headers.get('cache-control')).toBe('no-store');
      const body = await response.text();
      for (const leak of [INTERNAL, PUBLIC, 'backend:8000', 'ECONNREFUSED']) {
        expect(body).not.toContain(leak);
      }
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('never replays a failed request against another candidate', async () => {
    process.env.DAEMON_INTERNAL_API_URL = INTERNAL;
    process.env.NEXT_PUBLIC_API_URL = PUBLIC;
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockRejectedValue(
        new Error(`connect ECONNREFUSED 10.4.2.9:8000 via ${INTERNAL}`),
      );

    const { entitlementsGet } = await loadProxies();
    const response = await entitlementsGet(entitlementsRequest());

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(response.status).toBe(502);
    expect(response.headers.get('cache-control')).toBe('no-store');
    const body = await response.text();
    for (const leak of [INTERNAL, PUBLIC, '10.4.2.9', 'ECONNREFUSED', '8000']) {
      expect(body).not.toContain(leak);
    }
  });
});

describe('bootstrap proxy transport', () => {
  it.each(['POST', 'DELETE'])(
    'does not replay an uncertain %s cookie writer',
    async (method) => {
      process.env.DAEMON_INTERNAL_API_URL = INTERNAL;
      process.env.NEXT_PUBLIC_API_URL = PUBLIC;
      const fetchSpy = vi
        .spyOn(globalThis, 'fetch')
        .mockRejectedValue(new Error('Response lost after possible commit'));
      vi.resetModules();
      const route = await import('../app/api/v1/auth/[...path]/route');
      const handler = method === 'POST' ? route.POST : route.DELETE;
      const response = await handler(
        new Request(
          `https://app.fixture/api/v1/auth/${method === 'POST' ? 'refresh' : 'devices/fixture'}`,
          {
            method,
            headers: {
              cookie: 'synthetic-cookie',
              'Content-Type': 'application/json',
            },
            ...(method === 'POST' ? { body: '{}' } : {}),
          },
        ),
        {
          params: Promise.resolve({
            path: method === 'POST' ? ['refresh'] : ['devices', 'fixture'],
          }),
        },
      );
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      expect(response.status).toBe(502);
      expect(response.headers.get('cache-control')).toBe('no-store');
      expect(await response.json()).toEqual({
        error: 'Could not reach the authentication service.',
      });
    },
  );

  it('preserves separate cookie headers and cache clearing', async () => {
    const headers = new Headers({
      'Clear-Site-Data': '"cache"',
      'Cache-Control': 'no-store',
    });
    headers.append(
      'Set-Cookie',
      'first=fictional; Path=/; Secure; HttpOnly; SameSite=Strict',
    );
    headers.append(
      'Set-Cookie',
      'second=fictional; Path=/; Secure; HttpOnly; SameSite=Strict',
    );
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{}', { headers }),
    );
    const { authGet } = await loadProxies();
    const response = await authGet(authRequest(), authContext);
    expect(response.headers.getSetCookie()).toEqual(headers.getSetCookie());
    expect(response.headers.getSetCookie()).toHaveLength(2);
    expect(response.headers.get('clear-site-data')).toBe('"cache"');
  });

  it('bypasses the HTTP cache and refuses redirects in both proxies', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 200 }));

    const { authGet, entitlementsGet } = await loadProxies();
    await authGet(authRequest(), authContext);
    await entitlementsGet(entitlementsRequest());

    for (const [, init] of fetchSpy.mock.calls) {
      expect(init?.cache).toBe('no-store');
      expect(init?.redirect).toBe('error');
    }
  });

  it('refuses a redirected credential-bearing request opaquely', async () => {
    // ``redirect: 'error'`` surfaces as a fetch rejection.
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(
      new TypeError('unexpected redirect to http://internal-broker-7:8000'),
    );

    const { authGet } = await loadProxies();
    const response = await authGet(authRequest(), authContext);

    expect(response.status).toBe(502);
    expect(response.headers.get('cache-control')).toBe('no-store');
    const body = await response.text();
    expect(body).not.toContain('internal-broker-7');
    expect(body).not.toContain('unexpected redirect');
  });
});
