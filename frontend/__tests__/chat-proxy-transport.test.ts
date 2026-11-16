// @vitest-environment node
import {
  createServer,
  type IncomingMessage,
  type Server,
  type ServerResponse,
} from 'node:http';
import type { Socket } from 'node:net';

import { afterEach, describe, expect, it, vi } from 'vitest';

/**
 * Real-transport regression for #725, run against the actual `/api/chat` route
 * handler with Node's native `fetch` and owned `node:http` loopback listeners.
 * The AI SDK is not mocked.
 *
 * Scope: this exercises the route handler plus one native HTTP hop. It does
 * NOT run Docker/Compose, does not use a production Next.js server and is not
 * deployment or production acceptance. Only 127.0.0.1:port-0 listeners owned
 * by this file exist; a wrapper rejects any other contact before the network.
 *
 * The first owned authority consumes the request body, records acceptance and
 * destroys the socket before sending headers, so the POST's outcome is
 * unknown. A second attempt would be answered with a successful SSE stream;
 * no test here may observe that fabricated success.
 */

const OPAQUE_ERROR =
  'The chat service did not return a usable response. The request outcome is unknown, so it was not resent automatically.';
const SUCCESS_SSE = 'event: token\ndata: {"data":{"text":"fabricated"}}\n\n';

type Recorded = { hits: number; accepted: number; bodies: string[] };
type Responder = (
  req: IncomingMessage,
  res: ServerResponse,
  record: Recorded,
) => void;

type OwnedServer = {
  server: Server;
  url: string;
  record: Recorded;
  close: () => Promise<void>;
};

type Chunk = {
  type: string;
  data?: Record<string, unknown>;
  delta?: string;
  errorText?: string;
};

async function startServer(respond: Responder): Promise<OwnedServer> {
  const record: Recorded = { hits: 0, accepted: 0, bodies: [] };
  const sockets = new Set<Socket>();
  const server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => chunks.push(chunk));
    req.on('end', () => {
      record.hits += 1;
      record.bodies.push(Buffer.concat(chunks).toString('utf8'));
      respond(req, res, record);
    });
  });
  server.on('connection', (socket) => {
    sockets.add(socket);
    socket.once('close', () => sockets.delete(socket));
  });

  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve());
  });

  const address = server.address();
  if (!address || typeof address === 'string') {
    throw new Error('owned listener has no loopback port');
  }

  return {
    server,
    url: `http://127.0.0.1:${address.port}`,
    record,
    close: async () => {
      for (const socket of sockets) socket.destroy();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    },
  };
}

/** Consume the body, record acceptance, then drop the socket before headers. */
const destroyBeforeHeaders: Responder = (_req, res, record) => {
  record.accepted += 1;
  if (record.accepted === 1) {
    res.socket?.destroy();
  } else {
    // A duplicate dispatch would be accepted again and appear successful.
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end(SUCCESS_SSE);
  }
};

/** Count-only authority that would answer successfully if it were ever hit. */
const successfulAuthority: Responder = (_req, res, record) => {
  record.accepted += 1;
  res.writeHead(200, { 'Content-Type': 'text/event-stream' });
  res.end(SUCCESS_SSE);
};

function redirectAuthority(location: string, status: number): Responder {
  return (_req, res) => {
    res.writeHead(status, { Location: `${location}/chat` });
    res.end();
  };
}

type FetchGate = { attempts: number; blocked: string[] };

/**
 * Wrap the real fetch so only the owned loopback origins reach the network.
 * The route's built-in `http://backend:8000` / `http://localhost:8000`
 * candidates are rejected before any connection is attempted.
 */
function gateFetch(owned: OwnedServer[]): FetchGate {
  const allowed = new Set(owned.map((server) => new URL(server.url).origin));
  const gate: FetchGate = { attempts: 0, blocked: [] };
  const realFetch = globalThis.fetch;

  vi.stubGlobal(
    'fetch',
    (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      gate.attempts += 1;
      let url: URL;
      try {
        url = new URL(String(input));
      } catch {
        // An unparseable absolute URL cannot identify a network authority.
        // Exercise Node's real rejection instead of manufacturing this error.
        return realFetch.call(globalThis, input, init);
      }
      if (
        url.protocol !== 'http:' ||
        url.hostname !== '127.0.0.1' ||
        !allowed.has(url.origin)
      ) {
        gate.blocked.push(String(input));
        return Promise.reject(new TypeError('blocked non-owned contact'));
      }
      return realFetch.call(globalThis, input, init);
    },
  );
  return gate;
}

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

async function loadPost(
  internal: string,
  publicUrl: string,
): Promise<(req: Request) => Promise<Response>> {
  vi.resetModules();
  process.env.DAEMON_INTERNAL_API_URL = internal;
  process.env.NEXT_PUBLIC_API_URL = publicUrl;
  const route = await import('../app/api/chat/route');
  return route.POST;
}

function chatRequest(
  body?: Record<string, unknown>,
  options: { signal?: AbortSignal; headers?: Record<string, string> } = {},
): Request {
  return new Request('http://test/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...options.headers },
    body: JSON.stringify(
      body ?? {
        messages: [{ role: 'user', content: 'hello' }],
      },
    ),
    ...(options.signal ? { signal: options.signal } : {}),
  });
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
  vi.resetModules();
});

describe('chat proxy native transport single-attempt', () => {
  it('keeps one attempt when internal and public name the same authority', async () => {
    const first = await startServer(destroyBeforeHeaders);
    const second = await startServer(successfulAuthority);
    try {
      const gate = gateFetch([first, second]);
      const POST = await loadPost(first.url, first.url);
      const parts = await readChunks(await POST(chatRequest()));

      expect(first.record.hits).toBe(1);
      expect(first.record.accepted).toBe(1);
      expect(first.record.bodies).toHaveLength(1);
      expect(second.record.hits).toBe(0);
      expect(second.record.accepted).toBe(0);
      expect(gate.attempts).toBe(1);
      expect(gate.blocked).toEqual([]);
      expect(errorTextOf(parts)).toBe(OPAQUE_ERROR);
      expect(parts.some((part) => part.type === 'text-delta')).toBe(false);
    } finally {
      await Promise.all([first.close(), second.close()]);
    }
  });

  it('never contacts the distinct second authority after the first accepts', async () => {
    const first = await startServer(destroyBeforeHeaders);
    const second = await startServer(successfulAuthority);
    try {
      const gate = gateFetch([first, second]);
      const POST = await loadPost(first.url, second.url);
      const parts = await readChunks(await POST(chatRequest()));

      expect(first.record.hits).toBe(1);
      expect(first.record.accepted).toBe(1);
      expect(second.record.hits).toBe(0);
      expect(gate.attempts).toBe(1);
      expect(gate.blocked).toEqual([]);
      expect(errorTextOf(parts)).toBe(OPAQUE_ERROR);
    } finally {
      await Promise.all([first.close(), second.close()]);
    }
  });

  it.each([
    {
      status: 307,
      body: undefined as Record<string, unknown> | undefined,
      label: 'unkeyed',
    },
    {
      status: 308,
      body: {
        messages: [{ role: 'user', content: 'hello' }],
        idempotency_key: 'keyed-redirect',
      },
      label: 'keyed',
    },
    {
      status: 307,
      body: {
        id: null,
        model: 'auto',
        suggestion_id: 'opaque-candidate',
        messages: [{ role: 'user', content: 'suggestion prompt' }],
      },
      label: 'suggestion',
    },
  ])(
    'refuses a native $status redirect ($label) instead of following it',
    async ({ body, status }) => {
      const second = await startServer(successfulAuthority);
      // Held listeners stay up for the whole test; the redirect only exists on
      // the first authority, so a followed redirect would hit `second`.
      const redirecting = await startServer(
        redirectAuthority(second.url, status),
      );
      try {
        const gate = gateFetch([second, redirecting]);
        const POST = await loadPost(redirecting.url, second.url);
        const parts = await readChunks(await POST(chatRequest(body)));

        expect(redirecting.record.hits).toBe(1);
        expect(second.record.hits).toBe(0);
        expect(second.record.accepted).toBe(0);
        expect(gate.attempts).toBe(1);
        expect(gate.blocked).toEqual([]);
        expect(errorTextOf(parts)).toBe(OPAQUE_ERROR);
        expect(parts.some((part) => part.type === 'text-delta')).toBe(false);
      } finally {
        await Promise.all([second.close(), redirecting.close()]);
      }
    },
  );

  it('fails before the wire for a whitespace-only first value without hitting an authority', async () => {
    const first = await startServer(destroyBeforeHeaders);
    const second = await startServer(successfulAuthority);
    try {
      const gate = gateFetch([first, second]);
      const POST = await loadPost('   ', second.url);
      const parts = await readChunks(await POST(chatRequest()));

      expect(first.record.hits).toBe(0);
      expect(second.record.hits).toBe(0);
      expect(gate.attempts).toBe(1);
      expect(errorTextOf(parts)).toBe(OPAQUE_ERROR);
    } finally {
      await Promise.all([first.close(), second.close()]);
    }
  });

  it('does not advance an already-aborted request to a listener', async () => {
    const first = await startServer(destroyBeforeHeaders);
    const second = await startServer(successfulAuthority);
    try {
      const gate = gateFetch([first, second]);
      const controller = new AbortController();
      controller.abort();
      const POST = await loadPost(first.url, second.url);
      const parts = await readChunks(
        await POST(chatRequest(undefined, { signal: controller.signal })),
      );

      expect(first.record.hits).toBe(0);
      expect(first.record.accepted).toBe(0);
      expect(second.record.hits).toBe(0);
      expect(gate.attempts).toBe(1);
      // Existing abort handling (not an unknown-outcome error) is preserved,
      // but no listener received the aborted POST.
      expect(parts.some((part) => part.type === 'text-delta')).toBe(false);
      expect(errorTextOf(parts)).toBeUndefined();
      expect(parts).toContainEqual({
        type: 'abort',
        reason: 'request-aborted',
      });
    } finally {
      await Promise.all([first.close(), second.close()]);
    }
  });
});
