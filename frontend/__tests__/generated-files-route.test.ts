import { afterEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from '../app/generated-files/[...path]/route';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('generated-file proxy (#484 review)', () => {
  it("never lets the browser keep the owner's file", async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response('a,b', {
            status: 200,
            headers: { 'content-type': 'text/csv' },
          }),
      ),
    );
    const response = await GET(
      new NextRequest('https://daemon.test/generated-files/report.csv', {
        headers: { authorization: 'Bearer fixture' },
      }),
      { params: Promise.resolve({ path: ['report.csv'] }) },
    );
    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('private, no-store');
  });
});
