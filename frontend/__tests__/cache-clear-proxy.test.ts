import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => vi.restoreAllMocks());

describe('cache-only auth proxy response', () => {
  it('transports exact cache directive without changing status or cookies', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, {
        status: 204,
        headers: { 'Clear-Site-Data': '"cache"', 'Cache-Control': 'no-store' },
      }),
    );
    const { POST } = await import('../app/api/v1/auth/[...path]/route');
    const response = await POST(
      new Request('http://localhost:3000/api/v1/auth/cache/clear', {
        method: 'POST',
        headers: {
          Origin: 'http://localhost:3000',
          Cookie: 'fictional=retained',
        },
      }),
      { params: Promise.resolve({ path: ['cache', 'clear'] }) },
    );
    expect(response.status).toBe(204);
    expect(await response.text()).toBe('');
    expect(response.headers.get('Clear-Site-Data')).toBe('"cache"');
    expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(response.headers.get('Set-Cookie')).toBeNull();
    const [, init] = fetchMock.mock.calls[0];
    expect(new Headers(init?.headers).get('Origin')).toBe(
      'http://localhost:3000',
    );
    expect(new Headers(init?.headers).get('Cookie')).toBe('fictional=retained');
    expect(new Headers(init?.headers).get('Authorization')).toBeNull();
  });

  it('does not manufacture clearing evidence for a rejected origin', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ detail: 'Cache clearing origin rejected' }),
        {
          status: 403,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );
    const { POST } = await import('../app/api/v1/auth/[...path]/route');
    const response = await POST(
      new Request('http://localhost:3000/api/v1/auth/cache/clear', {
        method: 'POST',
      }),
      { params: Promise.resolve({ path: ['cache', 'clear'] }) },
    );
    expect(response.status).toBe(403);
    expect(response.headers.get('Clear-Site-Data')).toBeNull();
    expect(await response.json()).toEqual({
      detail: 'Cache clearing origin rejected',
    });
  });
});
