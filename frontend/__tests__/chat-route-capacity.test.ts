import { beforeEach, describe, expect, it, vi } from 'vitest';
import { POST } from '../app/api/chat/route';

type Chunk = {
  type: string;
  data?: Record<string, unknown>;
  delta?: string;
  errorText?: string;
};

const INTERNAL_HOST = 'http://daemon-backend-7.internal:8000';

function chatRequest() {
  return new Request('http://test/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      messages: [{ role: 'user', content: 'hello' }],
      id: 'conv_capacity',
    }),
  });
}

function frame(type: string, data: Record<string, unknown>) {
  return `event: ${type}\ndata: ${JSON.stringify(data)}`;
}

function sse(frames: string[]): Response {
  return new Response(`${frames.join('\n\n')}\n\n`, {
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

async function chunks(): Promise<Chunk[]> {
  const response = await POST(chatRequest());
  expect(response.headers.get('x-vercel-ai-ui-message-stream')).toBe('v1');
  return (await response.text())
    .split('\n')
    .filter((line) => line.startsWith('data: ') && line !== 'data: [DONE]')
    .map((line) => JSON.parse(line.slice(6)) as Chunk);
}

beforeEach(() => {
  vi.restoreAllMocks();
  process.env.DAEMON_INTERNAL_API_URL = INTERNAL_HOST;
});

describe('chat bridge capacity and transport failures', () => {
  it('renders sanitized HTTP capacity code and message as an SDK7 error, not reply text', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: 'budget_exceeded',
              message: 'Monthly allowance used up.',
            },
          }),
          { status: 429, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    );
    const writes = await chunks();
    expect(writes.find((part) => part.type === 'error')?.errorText).toBe(
      'Monthly allowance used up. (code: budget_exceeded)',
    );
    expect(writes.some((part) => part.type === 'text-delta')).toBe(false);
    expect(writes.some((part) => part.type === 'rate_limited')).toBe(false);
  });

  it('keeps the typed throttle event and retry delay for backend rate limits', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'rate_limited' }), {
          status: 429,
          headers: {
            'Retry-After': '17',
            'X-Daemon-Rate-Limit-Scope': 'session_id',
          },
        }),
      ),
    );
    const writes = await chunks();
    expect(
      writes.find((part) => part.type === 'data-event')?.data,
    ).toMatchObject({
      type: 'rate_limited',
      scope: 'session',
      retry_after_seconds: 17,
    });
  });

  it('uses a generic status message for malformed HTTP bodies', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('<html>gateway</html>', {
          status: 502,
          headers: { 'Content-Type': 'text/html' },
        }),
      ),
    );
    expect(
      (await chunks()).find((part) => part.type === 'error')?.errorText,
    ).toBe('Backend error (502): unable to stream response.');
  });

  it('does not promote an unstructured backend error field into a user message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ detail: { error: `upstream ${INTERNAL_HOST}` } }),
          {
            status: 503,
          },
        ),
      ),
    );
    expect(
      (await chunks()).find((part) => part.type === 'error')?.errorText,
    ).toBe('Backend error (503): unable to stream response.');
  });

  it('never exposes internal topology on transport failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockRejectedValue(
          new Error(`connect ECONNREFUSED 10.4.2.9:8000 via ${INTERNAL_HOST}`),
        ),
    );
    const writes = await chunks();
    expect(writes.find((part) => part.type === 'error')?.errorText).toBe(
      'Could not reach the chat service. Please try again.',
    );
  });

  it('keeps a partial answer and emits an SDK7 error for a mid-stream capacity failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse([
          frame('token', { data: { text: 'Partial answer.' } }),
          frame('error', {
            data: { code: 'trial_exhausted', message: 'Trial spent.' },
          }),
        ]),
      ),
    );
    const writes = await chunks();
    expect(
      writes
        .filter((part) => part.type === 'text-delta')
        .map((part) => part.delta),
    ).toEqual(['Partial answer.']);
    expect(writes.find((part) => part.type === 'error')?.errorText).toBe(
      'Trial spent. (code: trial_exhausted)',
    );
  });

  it('does not fabricate reply text when an SSE error precedes any token', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse([
          frame('error', {
            data: { code: 'limit_exceeded', message: 'Limit reached.' },
          }),
        ]),
      ),
    );
    const writes = await chunks();
    expect(writes.some((part) => part.type === 'text-delta')).toBe(false);
    expect(writes.find((part) => part.type === 'error')?.errorText).toBe(
      'Limit reached. (code: limit_exceeded)',
    );
  });
});

describe('chat bridge routing', () => {
  it('forwards server route class without a retired tier', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse([
          frame('routing', {
            data: {
              model: 'approved/model',
              route_class: 'premium',
              tier: 'max',
            },
          }),
        ]),
      ),
    );
    const routing = (await chunks()).find(
      (part) => part.type === 'data-event',
    )?.data;
    expect(routing).toMatchObject({
      type: 'routing',
      model: 'approved/model',
      route_class: 'premium',
    });
    expect(routing).not.toHaveProperty('tier');
  });

  it('omits missing or blank route class rather than inventing one', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse([
          frame('routing', {
            data: { model: 'approved/model', route_class: '   ' },
          }),
        ]),
      ),
    );
    expect(
      (await chunks()).find((part) => part.type === 'data-event')?.data,
    ).not.toHaveProperty('route_class');
  });
});
