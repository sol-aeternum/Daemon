import { describe, expect, it, vi, afterEach } from 'vitest';

import { POST } from '../app/api/v1/auth/[...path]/route';

describe('auth proxy forwarded header handling', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('drops user-controlled forwarded client IP headers', async () => {
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
    expect(headers.get('X-Forwarded-Host')).toBe('localhost:3000');
    expect(headers.get('X-Forwarded-Proto')).toBe('https');
    expect(headers.get('Authorization')).toBe('Bearer daemon-token');
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

    const response = await POST(req, {
      params: Promise.resolve({ path: ['devices'] }),
    });

    expect(response.status).toBe(200);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/v1\/auth\/devices\?include_revoked=true&limit=5$/);
  });
});
