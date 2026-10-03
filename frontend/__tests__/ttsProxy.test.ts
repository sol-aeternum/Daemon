import { afterEach, expect, it, vi } from 'vitest';
import { POST } from '../app/api/tts/route';

afterEach(() => vi.unstubAllGlobals());

function request(text: unknown, signal?: AbortSignal) {
  return new Request('http://localhost:3000/api/tts', {
    method: 'POST',
    headers: {
      Authorization: 'Bearer fictional',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      text,
      voice: 'daemon-default',
      speed: 1,
      format: 'mp3',
      cache: true,
    }),
    signal,
  });
}

it('forwards readable Markdown content with existing auth, cancellation and options', async () => {
  const fetch = vi.fn(async () =>
    Response.json({ audio_path: '/generated-audio/fixture.mp3' }),
  );
  vi.stubGlobal('fetch', fetch);
  const controller = new AbortController();
  const req = request(
    '**Hello** with `a * b`.\n\n```\nliteral_*_code\n```',
    controller.signal,
  );
  const response = await POST(req);
  expect(response.status).toBe(200);
  const init = (fetch.mock.calls as unknown as [string, RequestInit][])[0][1];
  expect(JSON.parse(init.body as string)).toEqual({
    text: 'Hello with a * b.\nliteral_*_code',
    voice: 'daemon-default',
    speed: 1,
    format: 'mp3',
    cache: true,
  });
  expect(new Headers(init.headers).get('Authorization')).toBe(
    'Bearer fictional',
  );
  expect(init.signal).toBe(req.signal);
});

it('refuses oversized raw Markdown before parsing even if cleanup would shorten it', async () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  const response = await POST(request('**x**'.repeat(601)));
  expect(response.status).toBe(413);
  expect(await response.json()).toEqual({ detail: { code: 'text_too_long' } });
  expect(fetch).not.toHaveBeenCalled();
});

it('counts raw Unicode code points, not UTF-16 units', async () => {
  const fetch = vi.fn(async () =>
    Response.json({ audio_path: '/generated-audio/fixture.mp3' }),
  );
  vi.stubGlobal('fetch', fetch);
  expect((await POST(request('😀'.repeat(3000)))).status).toBe(200);
  expect((await POST(request('😀'.repeat(3001)))).status).toBe(413);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('preserves backend refusals rather than bypassing authentication', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      Response.json({ detail: { code: 'unauthorized' } }, { status: 401 }),
    ),
  );
  const response = await POST(request('**Hello**'));
  expect(response.status).toBe(401);
  expect(await response.json()).toEqual({ detail: { code: 'unauthorized' } });
});
