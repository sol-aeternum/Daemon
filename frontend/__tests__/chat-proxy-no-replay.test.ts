import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  interpolateEntry,
  readFrontendService,
} from './helpers/composeFrontendService';

/**
 * Mock-transport regression for #725: the `/api/chat` proxy must make exactly
 * one authority attempt per submission. A rejected/thrown fetch has an
 * unknown outcome — the POST may already have been accepted — so no candidate
 * in the configured list may be tried afterwards, whether or not the request
 * carried an idempotency key or suggestion ID.
 *
 * Candidate URLs are derived from the checked-in `docker-compose.yml`
 * `frontend` service (source-only parsing, Compose is never rendered), so the
 * duplicate `[internal, public, backend, localhost]` shape stays tied to the
 * real defaults. The real loopback/native-fetch transport test lives in
 * `chat-proxy-transport.test.ts` and is not run here.
 */

const OPAQUE_ERROR =
  'The chat service did not return a usable response. The request outcome is unknown, so it was not resent automatically.';
const SUCCESS_SSE = 'event: token\ndata: {"data":{"text":"fabricated"}}\n\n';

type ProbeCall = { url: string; init: RequestInit };
type Chunk = {
  type: string;
  data?: Record<string, unknown>;
  delta?: string;
  errorText?: string;
};

const savedEnvironment = {
  internal: process.env.DAEMON_INTERNAL_API_URL,
  publicUrl: process.env.NEXT_PUBLIC_API_URL,
};

function restoreEnvironment() {
  if (savedEnvironment.internal === undefined) {
    delete process.env.DAEMON_INTERNAL_API_URL;
  } else {
    process.env.DAEMON_INTERNAL_API_URL = savedEnvironment.internal;
  }
  if (savedEnvironment.publicUrl === undefined) {
    delete process.env.NEXT_PUBLIC_API_URL;
  } else {
    process.env.NEXT_PUBLIC_API_URL = savedEnvironment.publicUrl;
  }
}

/**
 * The route reads its candidates once at module evaluation, so each test sets
 * a synthetic environment, resets the module registry, then imports it.
 */
async function loadPost(candidates: {
  internal?: string;
  publicUrl?: string;
}): Promise<(req: Request) => Promise<Response>> {
  vi.resetModules();
  if (candidates.internal === undefined) {
    delete process.env.DAEMON_INTERNAL_API_URL;
  } else {
    process.env.DAEMON_INTERNAL_API_URL = candidates.internal;
  }
  if (candidates.publicUrl === undefined) {
    delete process.env.NEXT_PUBLIC_API_URL;
  } else {
    process.env.NEXT_PUBLIC_API_URL = candidates.publicUrl;
  }
  const route = await import('../app/api/chat/route');
  return route.POST;
}

/** The checked-in default upstream values, read from Compose source only. */
function composeDefaults() {
  const service = readFrontendService();
  return {
    internal: interpolateEntry('DAEMON_INTERNAL_API_URL', {}, service),
    publicUrl: interpolateEntry('NEXT_PUBLIC_API_URL', {}, service),
  };
}

function chatRequest(
  body: Record<string, unknown> = {
    messages: [{ role: 'user', content: 'hello' }],
  },
  options: { headers?: Record<string, string>; signal?: AbortSignal } = {},
): Request {
  return new Request('http://test/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options.headers },
    body: JSON.stringify(body),
    ...(options.signal ? { signal: options.signal } : {}),
  });
}

/**
 * Fake transport that records every attempted URL and counts "acceptance"
 * before the first attempt's outcome becomes unknown. A second attempt would
 * return a successful SSE stream, so a replay shows up as a fabricated answer.
 */
function installProbe({
  firstError,
  failFirst = true,
  unreachable = [],
}: {
  firstError?: Error;
  failFirst?: boolean;
  unreachable?: string[];
} = {}) {
  const calls: ProbeCall[] = [];
  const accepted: string[] = [];
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input);
      calls.push({ url, init: init ?? {} });
      if (init?.signal?.aborted) {
        throw new DOMException('The operation was aborted.', 'AbortError');
      }
      if (!/^https?:\/\//.test(url)) {
        throw new TypeError('Failed to parse URL');
      }
      if (unreachable.includes(url)) {
        throw new TypeError('synthetic unavailable candidate');
      }
      accepted.push(url);
      // The request reached the wire before its outcome became unknown. A
      // replay would be accepted again and receive this successful stream.
      if (failFirst && calls.length === 1) {
        throw firstError ?? new TypeError('fetch failed');
      }
      return new Response(SUCCESS_SSE, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      });
    },
  );
  vi.stubGlobal('fetch', fetcher);
  return { calls, accepted, fetcher };
}

async function readChunks(response: Response): Promise<Chunk[]> {
  expect(response.headers.get('x-vercel-ai-ui-message-stream')).toBe('v1');
  const text = await response.text();
  return text
    .split('\n')
    .filter((line) => line.startsWith('data: ') && line !== 'data: [DONE]')
    .map((line) => JSON.parse(line.slice(6)) as Chunk);
}

function errorTextOf(parts: Chunk[]): string | undefined {
  return parts.find((part) => part.type === 'error')?.errorText;
}

afterEach(() => {
  restoreEnvironment();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetModules();
});

describe('chat proxy single-attempt authority', () => {
  it('does not replay an unkeyed POST over the Compose-duplicated candidate list', async () => {
    const defaults = composeDefaults();
    // Checked-in Compose pins these defaults; the route appends the same two
    // values as built-ins, giving [internal, public, internal, public].
    expect(defaults.internal).toBe('http://backend:8000');
    expect(defaults.publicUrl).toBe('http://localhost:8000');

    // A faulty loop would fail at container-local localhost, then deliver to
    // the same backend again. Do not let a fake localhost success hide that.
    const probe = installProbe({
      unreachable: [`${defaults.publicUrl}/chat`],
    });
    const POST = await loadPost(defaults);
    const parts = await readChunks(await POST(chatRequest()));

    expect(probe.calls.map((call) => call.url)).toEqual([
      `${defaults.internal}/chat`,
    ]);
    expect(probe.accepted).toEqual([`${defaults.internal}/chat`]);
    expect(probe.calls[0].init.redirect).toBe('error');
    expect(errorTextOf(parts)).toBe(OPAQUE_ERROR);
    expect(parts.some((part) => part.type === 'text-delta')).toBe(false);
  });

  it('does not try the public candidate when the internal authority fails', async () => {
    const internal = 'http://internal-chat.fixture:8000';
    const publicUrl = 'http://public-chat.fixture:8000';
    const probe = installProbe();
    const POST = await loadPost({ internal, publicUrl });
    const parts = await readChunks(await POST(chatRequest()));

    expect(probe.calls.map((call) => call.url)).toEqual([`${internal}/chat`]);
    expect(probe.accepted).toEqual([`${internal}/chat`]);
    expect(errorTextOf(parts)).toBe(OPAQUE_ERROR);
  });

  it('does not replay an unkeyed POST after a connection-like TypeError', async () => {
    const internal = 'http://internal-chat.fixture:8000';
    const publicUrl = 'http://public-chat.fixture:8000';
    const probe = installProbe({
      firstError: new TypeError(
        `connect ECONNREFUSED 10.4.2.9:8000 via ${internal}`,
      ),
    });
    const POST = await loadPost({ internal, publicUrl });
    const parts = await readChunks(await POST(chatRequest()));

    expect(probe.calls).toHaveLength(1);
    expect(probe.accepted).toEqual([`${internal}/chat`]);
    expect(errorTextOf(parts)).toBe(OPAQUE_ERROR);
    // Nothing from the transport error's internal host reaches the browser.
    expect(JSON.stringify(parts)).not.toContain('10.4.2.9');
  });

  it('does not replay an already-aborted request', async () => {
    const internal = 'http://internal-chat.fixture:8000';
    const publicUrl = 'http://public-chat.fixture:8000';
    const controller = new AbortController();
    controller.abort();
    const probe = installProbe();
    const POST = await loadPost({ internal, publicUrl });
    const parts = await readChunks(
      await POST(chatRequest(undefined, { signal: controller.signal })),
    );

    expect(probe.calls.map((call) => call.url)).toEqual([`${internal}/chat`]);
    expect(probe.accepted).toEqual([]);
    // An already-aborted request keeps its existing abort/finish shape; it
    // must not be promoted into a retry or a fabricated answer.
    expect(parts.some((part) => part.type === 'text-delta')).toBe(false);
    expect(errorTextOf(parts)).toBeUndefined();
    expect(parts).toContainEqual({ type: 'abort', reason: 'request-aborted' });
  });

  it.each([
    {
      name: 'whitespace-only internal value',
      internal: '   ',
      publicUrl: 'http://public-chat.fixture:8000',
    },
    {
      name: 'malformed first truthy value',
      internal: 'not a url',
      publicUrl: 'http://public-chat.fixture:8000',
    },
  ])(
    'does not fall through a $name whose transport attempt fails',
    async ({ internal, publicUrl }) => {
      const probe = installProbe({
        firstError: new TypeError('Failed to parse URL'),
      });
      const POST = await loadPost({ internal, publicUrl });
      const parts = await readChunks(await POST(chatRequest()));

      expect(probe.calls.map((call) => call.url)).toEqual([`${internal}/chat`]);
      expect(probe.accepted).toEqual([]);
      expect(errorTextOf(parts)).toBe(OPAQUE_ERROR);
    },
  );

  it('does not forward an invalid idempotency key and does not replay', async () => {
    const internal = 'http://internal-chat.fixture:8000';
    const publicUrl = 'http://public-chat.fixture:8000';
    const probe = installProbe();
    const POST = await loadPost({ internal, publicUrl });
    const parts = await readChunks(
      await POST(
        chatRequest({
          messages: [{ role: 'user', content: 'hello' }],
          idempotency_key: 'invalid key !!',
        }),
      ),
    );

    expect(
      new Headers(probe.calls[0].init.headers).get('Idempotency-Key'),
    ).toBeNull();
    expect(probe.calls).toHaveLength(1);
    expect(probe.accepted).toEqual([`${internal}/chat`]);
    expect(errorTextOf(parts)).toBe(OPAQUE_ERROR);
  });

  it('forwards a valid idempotency key and still stays single-attempt', async () => {
    const internal = 'http://internal-chat.fixture:8000';
    const publicUrl = 'http://public-chat.fixture:8000';
    const probe = installProbe();
    const POST = await loadPost({ internal, publicUrl });
    const parts = await readChunks(
      await POST(
        chatRequest({
          messages: [{ role: 'user', content: 'hello' }],
          idempotency_key: 'valid-key_1:2',
        }),
      ),
    );

    expect(
      new Headers(probe.calls[0].init.headers).get('Idempotency-Key'),
    ).toBe('valid-key_1:2');
    expect(probe.calls).toHaveLength(1);
    expect(probe.accepted).toEqual([`${internal}/chat`]);
    expect(errorTextOf(parts)).toBe(OPAQUE_ERROR);
  });

  it('sends a suggestion with null messages, keeps it single-attempt', async () => {
    const internal = 'http://internal-chat.fixture:8000';
    const publicUrl = 'http://public-chat.fixture:8000';
    const prompt = 'Draft the next milestone.';
    const probe = installProbe();
    const POST = await loadPost({ internal, publicUrl });
    const parts = await readChunks(
      await POST(
        chatRequest({
          id: null,
          model: 'auto',
          suggestion_id: 'opaque-candidate',
          messages: [{ role: 'user', parts: [{ type: 'text', text: prompt }] }],
        }),
      ),
    );

    expect(probe.calls).toHaveLength(1);
    expect(JSON.parse(String(probe.calls[0].init.body))).toMatchObject({
      suggestion_id: 'opaque-candidate',
      conversation_id: null,
      message: prompt,
      messages: null,
      attachments: [],
      metadata: null,
    });
    expect(errorTextOf(parts)).toBe(OPAQUE_ERROR);
  });

  it('forwards credentials, headers, signal, client features and body once', async () => {
    const internal = 'http://internal-chat.fixture:8000';
    const publicUrl = 'http://public-chat.fixture:8000';
    const probe = installProbe();
    const POST = await loadPost({ internal, publicUrl });
    const controller = new AbortController();
    const request = chatRequest(
      {
        messages: [{ role: 'user', content: 'hello' }],
        id: 'conv_1',
        client_features: ['task-cancel', 'unknown-feature'],
      },
      {
        headers: {
          authorization: 'Bearer synthetic',
          cookie: 'daemon_refresh=synthetic',
          origin: 'http://test',
        },
        signal: controller.signal,
      },
    );
    await readChunks(await POST(request));

    expect(probe.calls).toHaveLength(1);
    const init = probe.calls[0].init;
    expect(init.credentials).toBe('include');
    expect(init.redirect).toBe('error');
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect(init.signal).toBe(request.signal);
    expect(init.signal?.aborted).toBe(false);
    const forwarded = new Headers(init.headers);
    expect(forwarded.get('Authorization')).toBe('Bearer synthetic');
    expect(forwarded.get('Cookie')).toBe('daemon_refresh=synthetic');
    expect(forwarded.get('Origin')).toBe('http://test');
    expect(forwarded.get('X-Daemon-Client-Features')).toBe('task-cancel');
    expect(JSON.parse(String(init.body))).toMatchObject({
      message: 'hello',
      conversation_id: 'conv_1',
      messages: [{ role: 'user', content: 'hello' }],
    });
  });

  it('rejects an invalid suggestion locally without contacting any backend', async () => {
    const internal = 'http://internal-chat.fixture:8000';
    const probe = installProbe();
    const POST = await loadPost({ internal });
    const response = await POST(
      chatRequest({
        id: 'existing-conversation',
        suggestion_id: 'opaque-candidate',
        messages: [{ role: 'user', content: 'mixed content' }],
      }),
    );

    expect(response.status).toBe(400);
    expect(probe.calls).toEqual([]);
  });

  it('streams a first response normally instead of fabricating one after a failure', async () => {
    const internal = 'http://internal-chat.fixture:8000';
    const probe = installProbe({ failFirst: false });
    const POST = await loadPost({ internal });
    const parts = await readChunks(await POST(chatRequest()));

    expect(
      parts
        .filter((part) => part.type === 'text-delta')
        .map((part) => part.delta),
    ).toEqual(['fabricated']);
    expect(parts.some((part) => part.type === 'error')).toBe(false);
    expect(probe.calls).toHaveLength(1);
  });

  it('preserves an explicit backend refusal without another attempt', async () => {
    const internal = 'http://internal-chat.fixture:8000';
    const detail = {
      code: 'history_unavailable',
      task_id: null,
      retry_safe: false,
    };
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail }), {
        status: 503,
        headers: {
          'Content-Type': 'application/json',
          'Cache-Control': 'no-store',
        },
      }),
    );
    vi.stubGlobal('fetch', fetcher);
    const POST = await loadPost({ internal });
    const response = await POST(chatRequest());

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ detail });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
