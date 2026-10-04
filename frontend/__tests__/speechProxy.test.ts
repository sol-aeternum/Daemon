import { afterEach, describe, expect, it, vi } from 'vitest';
import { proxySpeechCapabilities, proxySpeechStream } from '../lib/speechProxy';

export const caps = {
  ready: true,
  provider: 'kokoro',
  model: 'fictional-model',
  voices: ['daemon-default'],
  formats: ['mp3', 'opus', 'wav'],
  streams: [],
  speed_min: 0.5,
  speed_max: 2,
  limits: {
    max_text_code_points: 3000,
    max_request_bytes: 32768,
    max_audio_bytes: 16_000_000,
    max_source_seconds: 300,
    max_padding_seconds: 0.15,
    max_wire_bytes: 20_000_000,
    max_audio_frames: 16384,
    max_heartbeats: 32,
    max_audio_frame_payload: 65536,
    max_control_bytes: 4096,
    queue_bytes: 262144,
    queue_frames: 64,
    heartbeat_seconds: 5,
    idle_seconds: 15,
    backpressure_seconds: 10,
    deadline_seconds: 125,
  },
};
const type = 'application/vnd.daemon.speech-stream;version=1';
function req(text: unknown = '**Hello** `a * b`.', signal?: AbortSignal) {
  return new Request('http://localhost:3000/api/tts/stream/v1', {
    method: 'POST',
    headers: {
      Authorization: 'Bearer fictional',
      Cookie: 'fictional=value',
      Origin: 'http://localhost:3000',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      text,
      voice: 'daemon-default',
      speed: 1.25,
      format: 'mp3',
      cache: false,
    }),
    signal,
  });
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('progressive speech proxy', () => {
  it('validates capability GET, forwarding private cookies without any POST', async () => {
    const fetch = vi.fn(async () =>
      Response.json(caps, {
        headers: { 'Set-Cookie': 'fictional=refreshed; HttpOnly' },
      }),
    );
    vi.stubGlobal('fetch', fetch);
    const response = await proxySpeechCapabilities(req());
    expect(await response.json()).toEqual(caps);
    expect(response.headers.get('cache-control')).toContain('no-store');
    expect(response.headers.get('set-cookie')).toContain('refreshed');
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('selects via safe GET then forwards exactly one POST, Markdown and auth, streaming unchanged bytes', async () => {
    const bytes = new Uint8Array([68, 83, 80, 49, 1, 2, 3]);
    const fetch = vi.fn(async (_url, init) =>
      init.method === 'POST'
        ? new Response(bytes, {
            headers: {
              'Content-Type': type,
              'Set-Cookie': 'fixture=post; HttpOnly',
            },
          })
        : Response.json(caps),
    );
    vi.stubGlobal('fetch', fetch);
    const response = await proxySpeechStream(req());
    expect(response.status).toBe(200);
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(bytes);
    expect(response.headers.get('set-cookie')).toContain('fixture=post');
    const calls = fetch.mock.calls as unknown as [string, RequestInit][];
    expect(calls).toHaveLength(2);
    expect(calls[1][0]).toContain('/tts/stream/v1');
    expect(JSON.parse(calls[1][1].body as string)).toEqual({
      text: 'Hello a * b.',
      voice: 'daemon-default',
      speed: 1.25,
      format: 'mp3',
      cache: false,
    });
    expect(calls[1][1].redirect).toBe('error');
    expect(new Headers(calls[1][1].headers).get('authorization')).toBe(
      'Bearer fictional',
    );
    expect(new Headers(calls[1][1].headers).get('cookie')).toBe(
      'fictional=value',
    );
    expect(response.headers.get('content-length')).toBeNull();
    expect(response.headers.get('content-encoding')).toBe('identity');
  });

  it.each(['**x**'.repeat(601), '😀'.repeat(3001), 'x'.repeat(33000)])(
    'rejects raw text/body bounds before cleanup or network',
    async (text) => {
      const fetch = vi.fn();
      vi.stubGlobal('fetch', fetch);
      expect((await proxySpeechStream(req(text))).status).toBe(413);
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it('accepts 3000 Unicode code points and never converts a non-MP3 request', async () => {
    const fetch = vi.fn(async (_url, init) =>
      init.method === 'POST'
        ? new Response('fixture', { headers: { 'Content-Type': type } })
        : Response.json(caps),
    );
    vi.stubGlobal('fetch', fetch);
    const response = await proxySpeechStream(req('😀'.repeat(3000)));
    expect(response.status).toBe(200);
    await response.body?.cancel();
    const wav = new Request('http://test', {
      method: 'POST',
      body: JSON.stringify({ text: 'fictional', format: 'wav' }),
    });
    expect((await proxySpeechStream(wav)).status).toBe(422);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('does not replay an ambiguously started POST or leak error details', async () => {
    const fetch = vi.fn(async (_url, init) => {
      if (init.method === 'POST')
        throw new Error('secret.internal:credentials');
      return Response.json(caps);
    });
    vi.stubGlobal('fetch', fetch);
    const response = await proxySpeechStream(req());
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain('secret');
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('does not bypass capability authentication and propagates expired cookies', async () => {
    const fetch = vi.fn(
      async () =>
        new Response('secret', {
          status: 401,
          headers: { 'Set-Cookie': 'fixture=; Max-Age=0' },
        }),
    );
    vi.stubGlobal('fetch', fetch);
    const response = await proxySpeechStream(req());
    expect(response.status).toBe(401);
    expect(response.headers.get('set-cookie')).toContain('Max-Age=0');
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('rejects an untrusted successful capability response rather than POSTing anywhere', async () => {
    const fetch = vi.fn(async () =>
      Response.json({
        ...caps,
        limits: { ...caps.limits, max_audio_bytes: 99_000_000 },
      }),
    );
    vi.stubGlobal('fetch', fetch);
    expect((await proxySpeechStream(req())).status).toBe(503);
    expect(fetch).toHaveBeenCalledOnce();
  });

  it('rejects successful wrong-version response without any fallback POST', async () => {
    const fetch = vi.fn(async (_url, init) =>
      init.method === 'POST'
        ? new Response('fixture', { headers: { 'Content-Type': 'audio/mpeg' } })
        : Response.json(caps),
    );
    vi.stubGlobal('fetch', fetch);
    expect((await proxySpeechStream(req())).status).toBe(503);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('preserves rate refusal and Retry-After with one POST', async () => {
    const fetch = vi.fn(async (_url, init) =>
      init.method === 'POST'
        ? new Response('private', {
            status: 429,
            headers: { 'Retry-After': '5' },
          })
        : Response.json(caps),
    );
    vi.stubGlobal('fetch', fetch);
    const response = await proxySpeechStream(req());
    expect(response.status).toBe(429);
    expect(response.headers.get('retry-after')).toBe('5');
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('bounds eager upstream reads and propagates downstream cancellation', async () => {
    let reads = 0;
    const cancel = vi.fn();
    let signal: AbortSignal | undefined;
    const fetch = vi.fn(async (_url, init) => {
      if (init.method !== 'POST') return Response.json(caps);
      signal = init.signal;
      return new Response(
        new ReadableStream(
          {
            pull(controller) {
              reads++;
              controller.enqueue(new Uint8Array(4096));
            },
            cancel,
          },
          { highWaterMark: 0 },
        ),
        { headers: { 'Content-Type': type } },
      );
    });
    vi.stubGlobal('fetch', fetch);
    const response = await proxySpeechStream(req());
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(reads).toBeLessThanOrEqual(1);
    await response.body?.cancel();
    expect(signal?.aborted).toBe(true);
    expect(cancel).toHaveBeenCalled();
  });

  it('request cancellation interrupts a reader that ignores cancellation', async () => {
    const abort = new AbortController();
    const fetch = vi.fn(async (_url, init) =>
      init.method === 'POST'
        ? new Response(
            new ReadableStream({
              pull() {
                return new Promise(() => {});
              },
            }),
            { headers: { 'Content-Type': type } },
          )
        : Response.json(caps),
    );
    vi.stubGlobal('fetch', fetch);
    const response = await proxySpeechStream(req('fictional', abort.signal));
    const read = response.body!.getReader().read();
    abort.abort();
    await expect(read).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('whole-call deadline interrupts a silent header wait with no replay', async () => {
    vi.useFakeTimers();
    const fetch = vi.fn(async (_url, init) =>
      init.method === 'POST'
        ? new Promise<Response>(() => {})
        : Response.json({
            ...caps,
            limits: { ...caps.limits, deadline_seconds: 1 },
          }),
    );
    vi.stubGlobal('fetch', fetch);
    const result = proxySpeechStream(req());
    await vi.advanceTimersByTimeAsync(1001);
    expect((await result).status).toBe(504);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
