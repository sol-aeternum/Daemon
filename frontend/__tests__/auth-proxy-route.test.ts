import { describe, expect, it, vi, afterEach } from 'vitest';

async function loadPostHandler() {
  vi.resetModules();
  const route = await import('../app/api/v1/auth/[...path]/route');
  return route.POST;
}

describe('auth proxy forwarded header handling', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    delete process.env.DAEMON_TRUSTED_PROXY_IPS;
  });

  it('ignores spoofed forwarded headers and uses the immediate client IP', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const req = new Request('http://localhost:3000/api/v1/auth/refresh', {
      method: 'POST',
      headers: {
        cookie: 'daemon_refresh=cookie-value',
        origin: 'https://app.daemon.ai',
        referer: 'https://app.daemon.ai/setup',
        'sec-fetch-site': 'same-origin',
        host: 'localhost:3000',
        'x-forwarded-host': 'localhost:3000',
        'x-forwarded-proto': 'https',
        'x-forwarded-for': '203.0.113.5',
        'x-real-ip': '203.0.113.6',
        forwarded: 'for=203.0.113.7;proto=https',
        authorization: 'Bearer daemon-token',
        'content-type': 'application/json',
      },
      body: JSON.stringify({ refresh_token: 'abc' }),
    });

    const POST = await loadPostHandler();
    const response = await POST(req, {
      params: Promise.resolve({ path: ['refresh'] }),
    });

    expect(response.status).toBe(200);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    const headers = new Headers(init.headers);

    expect(headers.get('X-Forwarded-For')).toBeNull();
    expect(headers.get('X-Real-IP')).toBeNull();
    expect(headers.get('Forwarded')).toBeNull();
    expect(headers.get('X-Daemon-Client-IP')).toBe('203.0.113.6');
    expect(headers.get('X-Forwarded-Host')).toBe('localhost:3000');
    expect(headers.get('X-Forwarded-Proto')).toBe('https');
    expect(headers.get('Authorization')).toBe('Bearer daemon-token');
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('DAEMON_TRUSTED_PROXY_IPS is unset'),
    );
  });

  it('does not synthesize X-Daemon-Client-IP without an immediate client IP', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const req = new Request('http://localhost:3000/api/v1/auth/refresh', {
      method: 'POST',
      headers: {
        host: 'localhost:3000',
        'x-forwarded-host': 'localhost:3000',
        'x-forwarded-proto': 'https',
        'x-vercel-forwarded-for': '203.0.113.10',
        'cf-connecting-ip': '198.51.100.9',
        'x-forwarded-for': '203.0.113.11',
      },
    });

    const POST = await loadPostHandler();
    await POST(req, {
      params: Promise.resolve({ path: ['refresh'] }),
    });

    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get('X-Daemon-Client-IP')).toBeNull();
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('DAEMON_TRUSTED_PROXY_IPS is unset'),
    );
  });

  it('unwinds X-Forwarded-For from a trusted proxy to the closest untrusted hop', async () => {
    process.env.DAEMON_TRUSTED_PROXY_IPS = '10.0.0.12, 10.0.0.13';
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const req = new Request('http://localhost:3000/api/v1/auth/refresh', {
      method: 'POST',
      headers: {
        host: 'localhost:3000',
        'x-forwarded-host': 'localhost:3000',
        'x-forwarded-proto': 'https',
        'x-real-ip': '10.0.0.13',
        'x-forwarded-for': '198.51.100.9, 10.0.0.12',
        'x-vercel-forwarded-for': '203.0.113.10',
      },
    });

    const POST = await loadPostHandler();
    await POST(req, {
      params: Promise.resolve({ path: ['refresh'] }),
    });

    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get('X-Daemon-Client-IP')).toBe('198.51.100.9');
  });

  it('ignores spoofed platform IP headers from a non-trusted immediate IP', async () => {
    process.env.DAEMON_TRUSTED_PROXY_IPS = '10.0.0.12';
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const req = new Request('http://localhost:3000/api/v1/auth/refresh', {
      method: 'POST',
      headers: {
        host: 'localhost:3000',
        'x-forwarded-host': 'localhost:3000',
        'x-forwarded-proto': 'https',
        'x-real-ip': '198.51.100.44',
        'cf-connecting-ip': '198.51.100.9',
        'x-vercel-forwarded-for': '203.0.113.10',
        'x-forwarded-for': '203.0.113.11',
      },
    });

    const POST = await loadPostHandler();
    await POST(req, {
      params: Promise.resolve({ path: ['refresh'] }),
    });

    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get('X-Daemon-Client-IP')).toBe('198.51.100.44');
  });

  it('falls back to platform IP headers only from a trusted proxy', async () => {
    process.env.DAEMON_TRUSTED_PROXY_IPS = '10.0.0.12';
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const req = new Request('http://localhost:3000/api/v1/auth/refresh', {
      method: 'POST',
      headers: {
        host: 'localhost:3000',
        'x-forwarded-host': 'localhost:3000',
        'x-forwarded-proto': 'https',
        'x-real-ip': '10.0.0.12',
        'cf-connecting-ip': '198.51.100.9',
        'x-vercel-forwarded-for': '203.0.113.10',
      },
    });

    const POST = await loadPostHandler();
    await POST(req, {
      params: Promise.resolve({ path: ['refresh'] }),
    });

    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get('X-Daemon-Client-IP')).toBe('203.0.113.10');
  });

  it('does not synthesize X-Daemon-Client-IP for invalid or comma-list values', async () => {
    process.env.DAEMON_TRUSTED_PROXY_IPS = '10.0.0.12';
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const req = new Request('http://localhost:3000/api/v1/auth/refresh', {
      method: 'POST',
      headers: {
        host: 'localhost:3000',
        'x-forwarded-host': 'localhost:3000',
        'x-forwarded-proto': 'https',
        'x-real-ip': '10.0.0.12',
        'cf-connecting-ip': '198.51.100.9, 10.0.0.12',
        'x-vercel-forwarded-for': 'unknown',
        'x-forwarded-for': 'not-an-ip, 10.0.0.12',
      },
    });

    const POST = await loadPostHandler();
    await POST(req, {
      params: Promise.resolve({ path: ['refresh'] }),
    });

    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get('X-Daemon-Client-IP')).toBeNull();
  });

  it('preserves original query strings when forwarding to backend', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    const req = new Request(
      'http://localhost:3000/api/v1/auth/devices?include_revoked=true&limit=5',
      {
        method: 'GET',
        headers: {
          host: 'localhost:3000',
          'x-forwarded-host': 'localhost:3000',
          'x-forwarded-proto': 'https',
        },
      },
    );

    const POST = await loadPostHandler();
    const response = await POST(req, {
      params: Promise.resolve({ path: ['devices'] }),
    });

    expect(response.status).toBe(200);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/v1\/auth\/devices\?include_revoked=true&limit=5$/);
  });

  it('rejects decoded dot segments before proxying auth paths', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const POST = await loadPostHandler();

    const req = new Request('http://localhost:3000/api/v1/auth/../refresh', {
      method: 'POST',
      headers: {
        host: 'localhost:3000',
        'x-forwarded-host': 'localhost:3000',
        'x-forwarded-proto': 'https',
      },
    });

    const response = await POST(req, {
      params: Promise.resolve({ path: ['..', 'refresh'] }),
    });

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual({
      error: 'Invalid auth path',
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
