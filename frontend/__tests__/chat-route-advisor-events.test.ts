import { beforeEach, describe, expect, it, vi } from 'vitest';

import { POST } from '../app/api/chat/route';

type UIMessageChunk = Record<string, unknown> & { type: string };

async function readUIMessageChunks(response: Response) {
  const body = await response.text();
  return body
    .split('\n')
    .filter((line) => line.startsWith('data: '))
    .map((line) => line.slice('data: '.length))
    .filter((payload) => payload !== '[DONE]')
    .map((payload) => JSON.parse(payload) as UIMessageChunk);
}

function buildSseResponse(frames: string[]): Response {
  return new Response(`${frames.join('\n\n')}\n\n`, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

function encodeFrame(eventType: string, data: Record<string, unknown>): string {
  return `event: ${eventType}\ndata: ${JSON.stringify(data)}`;
}

describe('chat route advisor event bridge', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('bootstraps advisor-only data events without sending nested text to the main reply stream', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        buildSseResponse([
          encodeFrame('advisor_start', {
            id: 'evt_advisor_start_1',
            request_id: 'req_1',
            data: {
              advisor_id: 'advisor_coding_1',
              domain: 'coding',
              difficulty: 'high',
              model: 'openrouter/anthropic/claude-opus-4.7',
              tool_call_id: 'call_consult_advisor',
            },
          }),
          encodeFrame('advisor_text_delta', {
            id: 'evt_advisor_text_1',
            request_id: 'req_1',
            data: {
              advisor_id: 'advisor_coding_1',
              text: 'Nested advisor text.',
            },
          }),
          encodeFrame('tool_call', {
            id: 'evt_tool_call_1',
            request_id: 'req_1',
            data: {
              advisor_id: 'advisor_coding_1',
              name: 'get_time',
              arguments: { format: 'iso' },
              tool_call_id: 'call_nested_1',
            },
          }),
          encodeFrame('tool_result', {
            id: 'evt_tool_result_1',
            request_id: 'req_1',
            data: {
              advisor_id: 'advisor_coding_1',
              name: 'get_time',
              result: { time: '2026-04-19T12:34:56Z' },
              tool_call_id: 'call_nested_1',
            },
          }),
          encodeFrame('advisor_end', {
            id: 'evt_advisor_end_1',
            request_id: 'req_1',
            data: {
              advisor_id: 'advisor_coding_1',
              status: 'completed',
              tokens_in: 21,
              tokens_out: 13,
            },
          }),
          encodeFrame('final', {
            id: 'evt_final_1',
            request_id: 'req_1',
            data: {
              text: 'Main reply.',
            },
          }),
        ]),
      ),
    );

    const response = await POST(
      new Request('http://test/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: [
            { role: 'user', content: 'Should we consult an advisor?' },
          ],
          id: 'conv_1',
        }),
      }),
    );

    expect(response.headers.get('x-vercel-ai-ui-message-stream')).toBe('v1');
    const writes = await readUIMessageChunks(response);
    const textParts = writes
      .filter((part) => part.type === 'text-delta')
      .map((part) => part.delta);
    const dataEvents = writes
      .filter((part) => part.type === 'data-event')
      .map((part) => part.data as Record<string, unknown>);

    expect(textParts).toEqual(['Main reply.']);
    expect(textParts).not.toContain('Nested advisor text.');
    expect(writes.map((part) => part.type)).toEqual(
      expect.arrayContaining([
        'data-event',
        'text-start',
        'text-delta',
        'text-end',
        'finish',
      ]),
    );

    expect(dataEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: 'advisor_start',
          advisor_id: 'advisor_coding_1',
          tool_call_id: 'call_consult_advisor',
        }),
        expect.objectContaining({
          type: 'advisor_text_delta',
          advisor_id: 'advisor_coding_1',
          content: 'Nested advisor text.',
        }),
        expect.objectContaining({
          type: 'tool_call',
          advisor_id: 'advisor_coding_1',
          tool_call_id: 'call_nested_1',
        }),
        expect.objectContaining({
          type: 'tool_result',
          advisor_id: 'advisor_coding_1',
          tool_call_id: 'call_nested_1',
        }),
        expect.objectContaining({
          type: 'advisor_end',
          advisor_id: 'advisor_coding_1',
          tokens_in: 21,
          tokens_out: 13,
        }),
      ]),
    );
  });

  it('keeps non-advisor streams backward compatible', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        buildSseResponse([
          encodeFrame('token', {
            id: 'evt_token_1',
            request_id: 'req_plain',
            data: { text: 'Top-level reply.' },
          }),
          encodeFrame('tool_call', {
            id: 'evt_tool_call_plain_1',
            request_id: 'req_plain',
            data: {
              name: 'web_search',
              arguments: { query: 'daemon' },
            },
          }),
          encodeFrame('tool_result', {
            id: 'evt_tool_result_plain_1',
            request_id: 'req_plain',
            data: {
              name: 'web_search',
              result: { hits: 3 },
            },
          }),
        ]),
      ),
    );

    const response = await POST(
      new Request('http://test/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: [{ role: 'user', content: 'Search for daemon' }],
          id: 'conv_plain',
        }),
      }),
    );

    const writes = await readUIMessageChunks(response);
    const textParts = writes
      .filter((part) => part.type === 'text-delta')
      .map((part) => part.delta);
    const dataEvents = writes
      .filter((part) => part.type === 'data-event')
      .map((part) => part.data as Record<string, unknown>);

    expect(textParts).toEqual(['Top-level reply.']);
    expect(
      dataEvents.some((event) => String(event.type).startsWith('advisor_')),
    ).toBe(false);
    expect(dataEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ type: 'tool_call', name: 'web_search' }),
        expect.objectContaining({ type: 'tool_result', name: 'web_search' }),
      ]),
    );
  });

  it('creates an assistant message from a data-only council stream', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        buildSseResponse([
          encodeFrame('council_interview', {
            id: 'evt_council_interview_1',
            request_id: 'req_council',
            data: {
              roster: { architect: 'model-a' },
              presets: ['lean'],
              rounds_options: [1, 2],
              audit_default: false,
            },
          }),
        ]),
      ),
    );

    const response = await POST(
      new Request('http://test/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: [
            {
              id: 'user_council',
              role: 'user',
              parts: [{ type: 'text', text: '/council' }],
            },
          ],
          id: 'conv_council',
        }),
      }),
    );

    const chunks = await readUIMessageChunks(response);
    expect(chunks.map((chunk) => chunk.type)).toEqual(['data-event', 'finish']);
    expect(chunks[0]?.data).toEqual(
      expect.objectContaining({
        type: 'council_interview',
        request_id: 'req_council',
        presets: ['lean'],
      }),
    );
  });

  it('passes req.signal to the backend fetch so abort propagates upstream', async () => {
    let capturedInit: RequestInit | undefined;

    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => {
          capturedInit = init;
          return Promise.resolve(
            new Response('event: token\ndata: {"data":{"text":"hi"}}\n\n', {
              status: 200,
              headers: { 'Content-Type': 'text/event-stream' },
            }),
          );
        }),
    );

    const controller = new AbortController();
    const request = new Request('http://test/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: [
          {
            id: 'user_1',
            role: 'user',
            parts: [{ type: 'text', text: 'hi' }],
          },
        ],
        id: 'conv_signal',
      }),
    });
    // Replace `req.signal` so we can observe the route forwarding the
    // browser-side signal into the backend fetch.
    Object.defineProperty(request, 'signal', {
      configurable: true,
      get: () => controller.signal,
    });

    controller.abort();

    await POST(request);

    // The route must forward the signal it received into the backend call
    // so the FastAPI connection is torn down when the user clicks Stop.
    expect(capturedInit?.signal).toBe(controller.signal);
    expect(capturedInit?.signal?.aborted).toBe(true);
    const forwardedBody = JSON.parse(String(capturedInit?.body));
    expect(forwardedBody.message).toBe('hi');
    expect(forwardedBody.messages).toEqual([{ role: 'user', content: 'hi' }]);
  });
});

describe('chat route rate-limit bridge', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    delete process.env.DAEMON_TRUSTED_PROXY_IPS;
  });

  it('forwards the validated client IP with the backend chat request', async () => {
    process.env.DAEMON_TRUSTED_PROXY_IPS = '10.0.0.1';
    let capturedInit: RequestInit | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (_url: string, init?: RequestInit) => {
        capturedInit = init;
        return buildSseResponse([
          encodeFrame('final', { data: { text: 'ok' } }),
        ]);
      }),
    );

    const response = await POST(
      new Request('http://test/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Real-IP': '10.0.0.1',
          'X-Forwarded-For': '198.51.100.9, 10.0.0.1',
        },
        body: JSON.stringify({
          messages: [{ role: 'user', content: 'hello' }],
          id: 'conv_ip',
        }),
      }),
    );
    await response.text();

    const forwardedHeaders = new Headers(capturedInit?.headers);
    expect(forwardedHeaders.get('X-Daemon-Client-IP')).toBe('198.51.100.9');
  });

  it('emits a typed rate_limited event with the backend retry delay', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'rate_limited' }), {
          status: 429,
          headers: {
            'Content-Type': 'application/json',
            'Retry-After': '17',
          },
        }),
      ),
    );

    const response = await POST(
      new Request('http://test/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: [{ role: 'user', content: 'hello' }],
          id: 'conv_429',
        }),
      }),
    );
    const writes = await readUIMessageChunks(response);
    const dataEvents = writes
      .filter((part) => part.type === 'data-event')
      .map((part) => part.data as Record<string, unknown>);

    expect(dataEvents).toContainEqual({
      type: 'rate_limited',
      scope: 'user',
      retry_after_seconds: 17,
    });
    expect(
      writes.some(
        (part) =>
          part.type === 'text-delta' &&
          String(part.delta).includes('sending messages too quickly'),
      ),
    ).toBe(true);
  });
});
