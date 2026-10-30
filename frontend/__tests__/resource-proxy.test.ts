import { afterEach, expect, it, vi } from 'vitest';
import { resourceProxy } from '@/lib/resourceProxy';
import {
  createRuntimeCaching,
  fetchPrivateUncached,
} from '@/lib/pwaRuntimeCaching';
import {
  isPrivateArtifactRequest,
  shouldUseGeneralRuntimeCache,
} from '@/lib/pwaCaching';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
const id = '11111111-1111-4111-8111-111111111111';
it('uses exactly one existing backend origin and selected bearer/range headers', async () => {
  vi.stubEnv('DAEMON_INTERNAL_API_URL', 'http://owned.test/base');
  const mock = vi
    .fn()
    .mockResolvedValue(
      new Response('ok', { headers: { ETag: '"pin"', 'Set-Cookie': 'bad' } }),
    );
  vi.stubGlobal('fetch', mock);
  const response = await resourceProxy(
    new Request('http://ui.test/api/resources?limit=20', {
      headers: {
        Authorization: 'Bearer fictional',
        Cookie: 'no',
        Range: 'bytes=0-1',
        'X-Forwarded-Host': 'evil',
      },
    }),
    `/resources/${id}/versions/${id}/content`,
  );
  expect(mock).toHaveBeenCalledOnce();
  expect(mock.mock.calls[0][0]).toBe(
    `http://owned.test/base/resources/${id}/versions/${id}/content?limit=20`,
  );
  const options = mock.mock.calls[0][1];
  expect(options.redirect).toBe('error');
  expect(options.cache).toBe('no-store');
  expect([...options.headers.keys()].sort()).toEqual([
    'authorization',
    'range',
  ]);
  expect(response.headers.get('set-cookie')).toBeNull();
  expect(response.headers.get('cache-control')).toBe('private, no-store');
});
it('does not fan out or accept path/redirect escape', async () => {
  const mock = vi.fn().mockRejectedValue(new Error('upstream'));
  vi.stubGlobal('fetch', mock);
  expect(
    (
      await resourceProxy(
        new Request('http://ui.test/api/resources'),
        '/resources/../../admin',
      )
    ).status,
  ).toBe(404);
  expect(mock).not.toHaveBeenCalled();
  expect(
    (
      await resourceProxy(
        new Request('http://ui.test/api/resources'),
        '/resources',
      )
    ).status,
  ).toBe(503);
  expect(mock).toHaveBeenCalledOnce();
});
it.each([
  '/resources',
  '/resources/a.png',
  '/%72esources/a.js',
  '/resources/a.woff2?token=no',
  '/api/resources/a.png',
  '/tasks/no/inputs',
])('never caches resource-shaped private paths %s', (path) => {
  const url = new URL(path, 'https://app.test');
  if (!path.startsWith('/api/') && !path.startsWith('/tasks/'))
    expect(isPrivateArtifactRequest(url)).toBe(true);
  expect(shouldUseGeneralRuntimeCache(url, true, 'https://app.test')).toBe(
    false,
  );
  const rules = createRuntimeCaching(
    'https://app.test',
    'https://backend.test',
  );
  const first = rules.find(
    (rule) =>
      typeof rule.matcher === 'function' &&
      rule.matcher({
        url,
        sameOrigin: true,
        request: new Request(url),
        event: {} as ExtendableEvent,
      }),
  );
  expect(first?.handler).toBe(fetchPrivateUncached);
});
