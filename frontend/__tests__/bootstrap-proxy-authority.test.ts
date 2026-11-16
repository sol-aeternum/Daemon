import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const INTERNAL = 'http://internal-broker-7.cluster.svc:8000';
const PUBLIC = 'http://public-backend.fixture:8000';
const PUBLIC_RUNTIME = 'http://localhost:8000';

type FrontendService = {
  environment: Record<string, string>;
  buildArgs: Record<string, string>;
};

/**
 * Locate the checked-in Compose file without relying on ``import.meta.url``,
 * which the jsdom test environment rewrites to a non-file URL.
 */
function composePath(): string {
  for (const candidate of [
    resolve(process.cwd(), 'docker-compose.yml'),
    resolve(process.cwd(), '..', 'docker-compose.yml'),
  ]) {
    if (existsSync(candidate)) return candidate;
  }
  throw new Error(
    'docker-compose.yml not found from the test working directory',
  );
}

/**
 * Read only the checked-in Compose source for the ``frontend`` service.
 *
 * The parser is deliberately narrow: this repository uses the list form of
 * ``environment:`` and mapping ``args:`` under ``build:``. It never renders
 * Compose, so no ``.env`` value or interpreter is involved.
 */
function readFrontendService(composeText?: string): FrontendService {
  const text = composeText ?? readFileSync(composePath(), 'utf8');
  const lines = text.split(/\r?\n/);
  const start = lines.indexOf('  frontend:');
  if (start < 0) throw new Error('docker-compose.yml has no frontend service');

  const environment: Record<string, string> = {};
  const buildArgs: Record<string, string> = {};
  let section: 'build' | 'args' | 'environment' | undefined;

  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^ {0,2}\S/.test(line)) break; // next top-level key or service
    if (/^ {4}\S/.test(line)) {
      section = undefined;
      if (/^ {4}build:\s*$/.test(line)) section = 'build';
      else if (/^ {4}environment:\s*$/.test(line)) section = 'environment';
      continue;
    }
    if (/^ {6}args:\s*$/.test(line)) {
      section = 'args';
      continue;
    }
    if (section === 'args') {
      const arg = /^ {8}([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$/.exec(line);
      if (arg) buildArgs[arg[1]] = arg[2];
      else if (/^ {6}\S/.test(line) || line.trim() === '') section = 'build';
      continue;
    }
    if (section === 'environment') {
      const entry = /^ {6}- (.+)$/.exec(line);
      if (!entry) continue;
      const separator = entry[1].indexOf('=');
      environment[separator < 0 ? entry[1] : entry[1].slice(0, separator)] =
        separator < 0 ? '' : entry[1].slice(separator + 1);
    }
  }

  return { environment, buildArgs };
}

/** Interpolate the only Compose form this file uses: ``${NAME:-default}``. */
function interpolateEntry(
  name: string,
  host: Record<string, string | undefined>,
  service: FrontendService = readFrontendService(),
): string {
  const raw = service.environment[name];
  if (raw === undefined) throw new Error(`frontend environment has no ${name}`);
  return raw.replace(
    /\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}/g,
    (_match, key: string, _operator: string | undefined, fallback?: string) => {
      const value = host[key];
      if (fallback !== undefined && (value === undefined || value === '')) {
        return fallback;
      }
      return value ?? '';
    },
  );
}

function injectedEnvironment(internal: string | undefined) {
  const service = readFrontendService();
  return {
    service,
    internal: interpolateEntry(
      'DAEMON_INTERNAL_API_URL',
      {
        DAEMON_INTERNAL_API_URL: internal,
      },
      service,
    ),
    publicUrl: interpolateEntry('NEXT_PUBLIC_API_URL', {}, service),
  };
}

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

describe('compose-injected bootstrap authority', () => {
  it('pins the checked-in injection and leaves PUBLIC byte-identical', () => {
    const { service, internal, publicUrl } = injectedEnvironment(undefined);

    expect(service.environment.DAEMON_INTERNAL_API_URL).toBe(
      '${DAEMON_INTERNAL_API_URL:-http://backend:8000}',
    );
    expect(service.buildArgs.NEXT_PUBLIC_API_URL).toBe(
      '${NEXT_PUBLIC_API_URL:-http://localhost:8000}',
    );
    expect(service.environment.NEXT_PUBLIC_API_URL).toBe(PUBLIC_RUNTIME);
    expect(internal).toBe('http://backend:8000');
    expect(publicUrl).toBe(PUBLIC_RUNTIME);
  });

  it.each([undefined, ''])(
    'dispatches both proxies to the backend service when the host value is %s',
    async (hostValue) => {
      const { internal, publicUrl } = injectedEnvironment(hostValue);
      expect(internal).toBe('http://backend:8000');
      process.env.DAEMON_INTERNAL_API_URL = internal;
      process.env.NEXT_PUBLIC_API_URL = publicUrl;
      const fetchSpy = vi
        .spyOn(globalThis, 'fetch')
        .mockResolvedValue(new Response('{}', { status: 200 }));

      const { authGet, entitlementsGet } = await loadProxies();
      await authGet(authRequest(), authContext);
      await entitlementsGet(entitlementsRequest());

      expect(fetchSpy.mock.calls.map(([url]) => String(url))).toEqual([
        'http://backend:8000/v1/auth/config',
        'http://backend:8000/users/me/entitlements',
      ]);
      expect(fetchSpy).toHaveBeenCalledTimes(2);
      for (const [, init] of fetchSpy.mock.calls) {
        expect(init?.cache).toBe('no-store');
        expect(init?.redirect).toBe('error');
      }
    },
  );

  it('passes an explicit override and path prefix through unchanged', async () => {
    const override = 'https://broker.internal:8443/daemon';
    const { internal, publicUrl } = injectedEnvironment(override);
    expect(internal).toBe(override);
    expect(publicUrl).toBe(PUBLIC_RUNTIME);
    process.env.DAEMON_INTERNAL_API_URL = internal;
    process.env.NEXT_PUBLIC_API_URL = publicUrl;
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 200 }));

    const { authGet, entitlementsGet } = await loadProxies();
    await authGet(authRequest(), authContext);
    await entitlementsGet(entitlementsRequest());

    expect(fetchSpy.mock.calls.map(([url]) => String(url))).toEqual([
      `${override}/v1/auth/config`,
      `${override}/users/me/entitlements`,
    ]);
    for (const [, init] of fetchSpy.mock.calls) {
      expect(init?.cache).toBe('no-store');
      expect(init?.redirect).toBe('error');
    }
  });

  it('refuses an invalid injected value in both proxies without fallback', async () => {
    const { internal, publicUrl } = injectedEnvironment('backend:8000');
    expect(internal).toBe('backend:8000');
    process.env.DAEMON_INTERNAL_API_URL = internal;
    process.env.NEXT_PUBLIC_API_URL = publicUrl;
    const fetchSpy = vi.spyOn(globalThis, 'fetch');

    const { authGet, entitlementsGet } = await loadProxies();
    for (const response of [
      await authGet(authRequest(), authContext),
      await entitlementsGet(entitlementsRequest()),
    ]) {
      expect(response.status).toBe(502);
      expect(response.headers.get('cache-control')).toBe('no-store');
      const body = await response.text();
      for (const leak of [internal, publicUrl, 'localhost:8000']) {
        expect(body).not.toContain(leak);
      }
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('pins the whitespace limitation: Compose passes it and selection falls through to PUBLIC', async () => {
    const { internal, publicUrl } = injectedEnvironment('   ');
    // Compose's ``:-`` only defaults unset or empty; whitespace stays injected.
    expect(internal).toBe('   ');
    expect(publicUrl).toBe(PUBLIC_RUNTIME);
    process.env.DAEMON_INTERNAL_API_URL = internal;
    process.env.NEXT_PUBLIC_API_URL = publicUrl;
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 200 }));

    const { authGet, entitlementsGet } = await loadProxies();
    await authGet(authRequest(), authContext);
    await entitlementsGet(entitlementsRequest());

    expect(fetchSpy.mock.calls.map(([url]) => String(url))).toEqual([
      `${PUBLIC_RUNTIME}/v1/auth/config`,
      `${PUBLIC_RUNTIME}/users/me/entitlements`,
    ]);
  });

  it('never replays a failed request in either proxy', async () => {
    const { internal, publicUrl } = injectedEnvironment(INTERNAL);
    expect(internal).toBe(INTERNAL);
    process.env.DAEMON_INTERNAL_API_URL = internal;
    process.env.NEXT_PUBLIC_API_URL = publicUrl;
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockRejectedValue(
        new Error(`connect ECONNREFUSED 10.4.2.9:8000 via ${INTERNAL}`),
      );

    const { authGet, entitlementsGet } = await loadProxies();
    const responses = [
      await authGet(authRequest(), authContext),
      await entitlementsGet(entitlementsRequest()),
    ];

    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(fetchSpy.mock.calls.map(([url]) => String(url))).toEqual([
      `${INTERNAL}/v1/auth/config`,
      `${INTERNAL}/users/me/entitlements`,
    ]);
    for (const response of responses) {
      expect(response.status).toBe(502);
      expect(response.headers.get('cache-control')).toBe('no-store');
      const body = await response.text();
      for (const leak of [INTERNAL, publicUrl, '10.4.2.9']) {
        expect(body).not.toContain(leak);
      }
    }
  });
});
