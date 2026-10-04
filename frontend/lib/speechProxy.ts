import { NextResponse } from 'next/server';
import { markdownToSpeechText } from './markdownSpeech';
import { MAX_TTS_TEXT_CODE_POINTS } from './constants';
import {
  validateSpeechCapabilities,
  type SpeechCapabilities,
} from './speechCapabilities';
import {
  isStrictSpeechStreamContentType,
  SPEECH_STREAM_CONTENT_TYPE_VERSIONED,
} from './speechStreamProtocol';

const MAX_BODY = 32768;
const MAX_CALL_MS = 180_000;
const IDLE_MS = 15_000;
const DRAIN_MS = 10_000;

class ProxyFailure extends Error {
  constructor(
    readonly code: string,
    readonly status = 503,
    readonly cookies?: Headers,
  ) {
    super(code);
  }
}

export function speechProxyHeaders(req: Request): Headers {
  const headers = new Headers({
    'Content-Type': 'application/json',
    'Accept-Encoding': 'identity',
  });
  for (const name of [
    'authorization',
    'cookie',
    'origin',
    'referer',
    'sec-fetch-site',
    'host',
    'x-forwarded-host',
    'x-forwarded-proto',
  ]) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

function copyCookies(source: Headers, target: Headers): void {
  for (const cookie of source.getSetCookie())
    target.append('Set-Cookie', cookie);
}

class Operation {
  readonly controller = new AbortController();
  readonly started = Date.now();
  private timer: ReturnType<typeof setTimeout>;
  private reason: ProxyFailure | null = null;
  private readonly onAbort: () => void;

  constructor(private readonly req: Request) {
    this.onAbort = () => this.fail(new ProxyFailure('speech_cancelled', 499));
    req.signal.addEventListener('abort', this.onAbort, { once: true });
    this.timer = setTimeout(
      () => this.fail(new ProxyFailure('speech_timeout', 504)),
      MAX_CALL_MS,
    );
    if (req.signal.aborted) this.onAbort();
  }
  fail(error: ProxyFailure): void {
    this.reason ??= error;
    this.controller.abort();
  }
  limit(seconds: number): void {
    clearTimeout(this.timer);
    const remaining =
      this.started + Math.min(seconds * 1000, MAX_CALL_MS) - Date.now();
    if (remaining <= 0) this.fail(new ProxyFailure('speech_timeout', 504));
    else
      this.timer = setTimeout(
        () => this.fail(new ProxyFailure('speech_timeout', 504)),
        remaining,
      );
  }
  async wait<T>(promise: Promise<T>, idle = IDLE_MS): Promise<T> {
    if (this.reason) throw this.reason;
    let abort!: () => void;
    let timer!: ReturnType<typeof setTimeout>;
    const interrupted = new Promise<never>((_, reject) => {
      abort = () =>
        reject(this.reason ?? new ProxyFailure('speech_cancelled', 499));
      this.controller.signal.addEventListener('abort', abort, { once: true });
      timer = setTimeout(
        () => this.fail(new ProxyFailure('speech_stream_idle_timeout', 504)),
        idle,
      );
    });
    try {
      return await Promise.race([promise, interrupted]);
    } finally {
      clearTimeout(timer);
      this.controller.signal.removeEventListener('abort', abort);
    }
  }
  dispose(): void {
    clearTimeout(this.timer);
    this.req.signal.removeEventListener('abort', this.onAbort);
  }
}

async function boundedJson(
  body: ReadableStream<Uint8Array> | null,
  operation: Operation,
): Promise<unknown> {
  if (!body) throw new ProxyFailure('speech_invalid_request', 422);
  const reader = body.getReader();
  let size = 0;
  const chunks: Uint8Array[] = [];
  try {
    for (;;) {
      const { done, value } = await operation.wait(reader.read());
      if (done) break;
      size += value.length;
      if (size > MAX_BODY)
        throw new ProxyFailure('speech_request_too_large', 413);
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.length;
    }
    try {
      return JSON.parse(
        new TextDecoder('utf-8', { fatal: true }).decode(bytes),
      );
    } catch {
      throw new ProxyFailure('speech_invalid_request', 422);
    }
  } finally {
    void reader.cancel().catch(() => {});
  }
}

function errorResponse(
  error: unknown,
  cookies?: Headers,
  retryAfter?: string | null,
): NextResponse {
  const failure =
    error instanceof ProxyFailure
      ? error
      : new ProxyFailure('speech_unavailable');
  const headers = new Headers({
    'Cache-Control': 'private, no-store, no-transform',
  });
  if (failure.cookies) copyCookies(failure.cookies, headers);
  if (cookies && cookies !== failure.cookies) copyCookies(cookies, headers);
  if (retryAfter && /^\d{1,6}$/.test(retryAfter))
    headers.set('Retry-After', retryAfter);
  return NextResponse.json(
    { detail: { code: failure.code } },
    { status: failure.status, headers },
  );
}

async function target(
  req: Request,
  operation: Operation,
): Promise<{
  url: string;
  capabilities: SpeechCapabilities;
  headers: Headers;
}> {
  const urls = Array.from(
    new Set(
      [
        process.env.DAEMON_INTERNAL_API_URL,
        process.env.NEXT_PUBLIC_API_URL,
        'http://backend:8000',
        'http://localhost:8000',
      ].filter((url): url is string => Boolean(url)),
    ),
  );
  for (const url of urls) {
    let response: Response;
    try {
      response = await operation.wait(
        fetch(`${url.replace(/\/+$/, '')}/tts/capabilities`, {
          headers: speechProxyHeaders(req),
          signal: operation.controller.signal,
          redirect: 'error',
          cache: 'no-store',
          credentials: 'include',
        }),
      );
    } catch (error) {
      if (operation.controller.signal.aborted) throw error;
      continue; // Safe GET only. A synthesis POST is never replayed here.
    }
    if (response.status === 401 || response.status === 403) {
      void response.body?.cancel().catch(() => {});
      throw new ProxyFailure(
        'speech_authorization_lost',
        response.status,
        response.headers,
      );
    }
    if (!response.ok) {
      void response.body?.cancel().catch(() => {});
      continue;
    }
    if (
      !/^application\/json(?:\s*;|\s*$)/i.test(
        response.headers.get('content-type') ?? '',
      )
    ) {
      void response.body?.cancel().catch(() => {});
      throw new ProxyFailure('speech_protocol_error');
    }
    const value = await boundedJson(response.body, operation);
    let capabilities: SpeechCapabilities;
    try {
      capabilities = validateSpeechCapabilities(value);
    } catch {
      throw new ProxyFailure('speech_protocol_error');
    }
    return {
      url: url.replace(/\/+$/, ''),
      capabilities,
      headers: response.headers,
    };
  }
  throw new ProxyFailure('speech_unavailable');
}

export async function proxySpeechCapabilities(req: Request): Promise<Response> {
  const operation = new Operation(req);
  try {
    const selected = await target(req, operation);
    const headers = new Headers({
      'Cache-Control': 'private, no-store, no-transform',
    });
    copyCookies(selected.headers, headers);
    return NextResponse.json(selected.capabilities, { headers });
  } catch (error) {
    return errorResponse(error);
  } finally {
    operation.dispose();
  }
}

export async function proxySpeechStream(req: Request): Promise<Response> {
  const operation = new Operation(req);
  let transferred = false;
  let selectedCookies: Headers | undefined;
  try {
    const length = req.headers.get('content-length');
    if (length && /^\d+$/.test(length) && Number(length) > MAX_BODY) {
      throw new ProxyFailure('speech_request_too_large', 413);
    }
    const value = await boundedJson(req.body, operation);
    if (!value || typeof value !== 'object' || Array.isArray(value))
      throw new ProxyFailure('speech_invalid_request', 422);
    const body = value as Record<string, unknown>;
    if (typeof body.text !== 'string')
      throw new ProxyFailure('text_required', 422);
    if (Array.from(body.text).length > MAX_TTS_TEXT_CODE_POINTS)
      throw new ProxyFailure('text_too_long', 413);
    if (
      body.format !== undefined &&
      body.format !== null &&
      body.format !== 'mp3'
    ) {
      throw new ProxyFailure('speech_stream_unsupported', 422);
    }
    const selected = await target(req, operation);
    selectedCookies = selected.headers;
    operation.limit(selected.capabilities.limits.deadline_seconds);
    const { voice, model, speed, format, cache } = body;
    const upstream = await operation.wait(
      fetch(`${selected.url}/tts/stream/v1`, {
        method: 'POST',
        headers: speechProxyHeaders(req),
        signal: operation.controller.signal,
        redirect: 'error',
        cache: 'no-store',
        credentials: 'include',
        body: JSON.stringify({
          text: markdownToSpeechText(body.text),
          voice,
          model,
          speed,
          format,
          cache,
        }),
      }),
    );
    if (!upstream.ok) {
      void upstream.body?.cancel().catch(() => {});
      const status = [400, 401, 403, 413, 422, 429, 503, 504].includes(
        upstream.status,
      )
        ? upstream.status
        : 503;
      const code =
        (
          {
            401: 'speech_authorization_lost',
            403: 'speech_authorization_lost',
            429: 'speech_busy',
            504: 'speech_timeout',
            413: 'speech_output_limit',
            422: 'speech_invalid_request',
            400: 'speech_invalid_request',
          } as Record<number, string>
        )[status] ?? 'speech_unavailable';
      return errorResponse(
        new ProxyFailure(code, status),
        upstream.headers,
        upstream.headers.get('retry-after'),
      );
    }
    if (
      !upstream.body ||
      !isStrictSpeechStreamContentType(upstream.headers.get('content-type')) ||
      !['identity', null].includes(upstream.headers.get('content-encoding'))
    ) {
      operation.fail(new ProxyFailure('speech_protocol_error'));
      void upstream.body?.cancel().catch(() => {});
      throw new ProxyFailure('speech_protocol_error');
    }
    const reader = upstream.body.getReader();
    let drain: ReturnType<typeof setTimeout> | undefined;
    let wire = 0,
      ended = false;
    let streamController:
      | ReadableStreamDefaultController<Uint8Array>
      | undefined;
    const retire = () => {
      if (ended) return;
      ended = true;
      clearTimeout(drain);
      operation.controller.signal.removeEventListener('abort', aborted);
      void reader.cancel().catch(() => {});
      operation.dispose();
    };
    const aborted = () => {
      streamController?.error(new ProxyFailure('speech_cancelled'));
      retire();
    };
    const stream = new ReadableStream<Uint8Array>(
      {
        start(controller) {
          streamController = controller;
          operation.controller.signal.addEventListener('abort', aborted, {
            once: true,
          });
          if (operation.controller.signal.aborted) aborted();
        },
        async pull(controller) {
          clearTimeout(drain);
          if (ended) return;
          try {
            const { value, done } = await operation.wait(reader.read());
            if (ended) return;
            if (done) {
              controller.close();
              retire();
              return;
            }
            wire += value.length;
            if (wire > selected.capabilities.limits.max_wire_bytes)
              throw new ProxyFailure('speech_protocol_error');
            controller.enqueue(value);
            drain = setTimeout(
              () =>
                operation.fail(new ProxyFailure('speech_backpressure_timeout')),
              DRAIN_MS,
            );
          } catch (error) {
            if (!ended) {
              controller.error(
                error instanceof ProxyFailure
                  ? error
                  : new ProxyFailure('speech_unavailable'),
              );
              retire();
              operation.fail(new ProxyFailure('speech_unavailable'));
            }
          }
        },
        cancel() {
          operation.fail(new ProxyFailure('speech_cancelled', 499));
          retire();
        },
      },
      { highWaterMark: 1, size: (chunk) => chunk.byteLength },
    );
    const headers = new Headers({
      'Content-Type': SPEECH_STREAM_CONTENT_TYPE_VERSIONED,
      'Cache-Control': 'private, no-store, no-transform',
      'X-Accel-Buffering': 'no',
      'Content-Encoding': 'identity',
    });
    copyCookies(selected.headers, headers);
    copyCookies(upstream.headers, headers);
    transferred = true;
    return new Response(stream, { headers });
  } catch (error) {
    return errorResponse(error, selectedCookies);
  } finally {
    if (!transferred) {
      operation.controller.abort();
      operation.dispose();
    }
  }
}
