import type { ChatEvent } from '@/lib/events';
import type { DaemonMessage } from '@/lib/chatMessages';
import { daemonClientIp } from '../_lib/clientIp';

const API_URLS = [
  process.env.DAEMON_INTERNAL_API_URL,
  process.env.NEXT_PUBLIC_API_URL,
  'http://backend:8000',
  'http://localhost:8000',
].filter((url): url is string => Boolean(url));

function buildProxyHeaders(req: Request): Headers {
  const headers = new Headers();
  headers.set('Content-Type', 'application/json');

  const authHeader = req.headers.get('authorization');
  if (authHeader) {
    headers.set('Authorization', authHeader);
  }

  const cookie = req.headers.get('cookie');
  if (cookie) headers.set('Cookie', cookie);

  const origin = req.headers.get('origin');
  if (origin) headers.set('Origin', origin);

  const referer = req.headers.get('referer');
  if (referer) headers.set('Referer', referer);

  const secFetchSite = req.headers.get('sec-fetch-site');
  if (secFetchSite) headers.set('Sec-Fetch-Site', secFetchSite);

  const host = req.headers.get('host');
  if (host) headers.set('Host', host);

  const xForwardedHost = req.headers.get('x-forwarded-host');
  if (xForwardedHost) headers.set('X-Forwarded-Host', xForwardedHost);

  const xForwardedProto = req.headers.get('x-forwarded-proto');
  if (xForwardedProto) headers.set('X-Forwarded-Proto', xForwardedProto);

  // Use the same trusted-proxy derivation as the auth bridge so
  // browser-controlled forwarding headers cannot bypass the IP quota.
  const clientIp = daemonClientIp(req);
  if (clientIp) headers.set('X-Daemon-Client-IP', clientIp);

  return headers;
}

function extractTextContent(content: unknown): string {
  if (typeof content === 'string') {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (
          part &&
          typeof part === 'object' &&
          'type' in part &&
          (part as { type?: unknown }).type === 'text' &&
          'text' in part &&
          typeof (part as { text?: unknown }).text === 'string'
        ) {
          return (part as { text: string }).text;
        }
        return '';
      })
      .filter(Boolean)
      .join('\n')
      .trim();
  }
  return '';
}

export async function POST(req: Request) {
  const { messages, id, model, attachments, metadata, provider } =
    await req.json();

  const { createUIMessageStream, createUIMessageStreamResponse } =
    await import('ai');

  const normalizedMessages = (messages || []).map((m: any) => ({
    role: m.role,
    content: extractTextContent(m.content ?? m.parts),
  }));

  const lastUserMessage = [...normalizedMessages]
    .reverse()
    .find((m) => m.role === 'user');
  const lastUserText = extractTextContent(lastUserMessage?.content);

  const proxyHeaders = buildProxyHeaders(req);

  let backendRes: Response | null = null;
  let lastError: Error | null = null;

  for (const apiUrl of API_URLS) {
    try {
      backendRes = await fetch(`${apiUrl}/chat`, {
        method: 'POST',
        headers: proxyHeaders,
        credentials: 'include',
        // Propagate the browser-side abort: when the user clicks Stop, the
        // Next.js request stream closes, `req.signal` fires, and the backend
        // SSE connection is torn down with it.
        signal: req.signal,
        body: JSON.stringify({
          message: lastUserText,
          conversation_id: id || null,
          messages: normalizedMessages,
          model: model || 'auto',
          provider: provider || null,
          attachments: Array.isArray(attachments) ? attachments : [],
          metadata: metadata && typeof metadata === 'object' ? metadata : null,
        }),
      });
      break;
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
    }
  }

  const responseHeaders = new Headers();
  if (backendRes) {
    backendRes.headers.forEach((value, key) => {
      if (key.toLowerCase() === 'set-cookie') {
        responseHeaders.append('Set-Cookie', value);
      }
    });
  }

  const stream = createUIMessageStream<DaemonMessage>({
    execute: async ({ writer }) => {
      const textPartId = 'assistant-text';
      let textPartStarted = false;
      let streamFailed = false;

      const writeText = (delta: string) => {
        if (!textPartStarted) {
          textPartStarted = true;
          writer.write({ type: 'text-start', id: textPartId });
        }
        writer.write({ type: 'text-delta', id: textPartId, delta });
      };

      const writeData = (events: ChatEvent[]) => {
        for (const event of events) {
          writer.write({ type: 'data-event', data: event });
        }
      };

      try {
        if (!backendRes) {
          writeText(
            `Backend error (network): ${lastError?.message || 'unknown error'}.`,
          );
          return;
        }

        if (backendRes.status === 429) {
          // Backend per-user/per-session/per-IP rate limit fired
          // (issue #38). Surface the typed event so the chat UI can
          // show a retryable error with the correct backoff instead
          // of treating throttling as a successful assistant reply.
          const retryAfterRaw = backendRes.headers.get('retry-after');
          const retryAfter = Number.parseInt(retryAfterRaw ?? '', 10);
          writeData([
            {
              type: 'rate_limited',
              scope: 'user',
              retry_after_seconds: Number.isFinite(retryAfter)
                ? Math.max(1, retryAfter)
                : 60,
            },
          ]);
          writeText(
            `You are sending messages too quickly. Please wait a moment before trying again.`,
          );
          return;
        }

        if (!backendRes.ok || !backendRes.body) {
          writeText(
            `Backend error (${backendRes.status}): unable to stream response.`,
          );
          return;
        }

        const reader = backendRes.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let sawToken = false;

        // Stop-button abort: when the browser-side `req.signal` fires (the user
        // clicked Stop), release the backend reader so the connection drops and
        // emit an abort chunk. Without this hook the read loop keeps draining
        // backend tokens until the stream closes naturally, which means FastAPI
        // keeps executing the request even though the UI has already stopped.
        const onAbort = () => {
          try {
            reader.cancel().catch(() => undefined);
          } catch {
            // reader may already be released; ignore.
          }
        };

        if (req.signal.aborted) {
          onAbort();
          return;
        }
        req.signal.addEventListener('abort', onAbort, { once: true });

        try {
          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            while (true) {
              const sepIdx = buffer.indexOf('\n\n');
              if (sepIdx === -1) break;

              const frame = buffer.slice(0, sepIdx);
              buffer = buffer.slice(sepIdx + 2);

              const lines = frame.split('\n');
              let eventType = 'message';
              let dataText = '';

              for (const line of lines) {
                if (line.startsWith('event:')) {
                  eventType = line.slice(6).trim();
                } else if (line.startsWith('data:')) {
                  dataText += line.slice(5).trim();
                }
              }

              if (!dataText) continue;

              let payload: any;
              try {
                payload = JSON.parse(dataText);
              } catch {
                continue;
              }

              if (eventType === 'token') {
                const delta =
                  payload?.data?.text ??
                  payload?.data?.delta ??
                  payload?.text ??
                  payload?.delta;
                if (typeof delta === 'string' && delta.length > 0) {
                  sawToken = true;
                  writeText(delta);
                }
              } else if (eventType === 'thinking') {
                const content = payload?.data?.content ?? payload?.content;
                if (typeof content === 'string' && content.length > 0) {
                  writeData([
                    {
                      type: 'thinking',
                      content: content,
                      id: payload?.id ?? payload?.data?.id,
                      request_id:
                        payload?.request_id ?? payload?.data?.request_id,
                    },
                  ]);
                }
              } else if (eventType === 'routing') {
                const modelId = payload?.data?.model;
                if (typeof modelId === 'string' && modelId.length > 0) {
                  writeData([
                    {
                      type: 'routing',
                      model: modelId,
                      tier: payload?.data?.tier,
                      reason: payload?.data?.reason,
                      id: payload?.id ?? payload?.data?.id,
                      request_id:
                        payload?.request_id ?? payload?.data?.request_id,
                    },
                  ]);
                }
              } else if (eventType === 'conversation') {
                const conversationId =
                  payload?.data?.conversation_id || payload?.conversation_id;
                if (conversationId) {
                  writeData([
                    {
                      type: 'conversation',
                      conversation_id: conversationId,
                    },
                  ]);
                }
              } else if (eventType === 'tool_call') {
                const data =
                  payload?.data && typeof payload.data === 'object'
                    ? payload.data
                    : {};
                writeData([
                  {
                    ...data,
                    type: 'tool_call',
                    name: payload?.data?.name || '',
                    arguments: payload?.data?.arguments || {},
                    id: payload?.id ?? payload?.data?.id,
                    request_id:
                      payload?.request_id ?? payload?.data?.request_id,
                  },
                ]);
              } else if (eventType === 'tool_result') {
                const data =
                  payload?.data && typeof payload.data === 'object'
                    ? payload.data
                    : {};
                writeData([
                  {
                    ...data,
                    type: 'tool_result',
                    name: payload?.data?.name || '',
                    result: payload?.data?.result || '',
                    id: payload?.id ?? payload?.data?.id,
                    request_id:
                      payload?.request_id ?? payload?.data?.request_id,
                  },
                ]);
              } else if (
                eventType === 'advisor_start' ||
                eventType === 'advisor_text_delta' ||
                eventType === 'advisor_text_done' ||
                eventType === 'advisor_error' ||
                eventType === 'advisor_end'
              ) {
                const data =
                  payload?.data && typeof payload.data === 'object'
                    ? payload.data
                    : {};
                const content =
                  payload?.data?.content ??
                  payload?.data?.text ??
                  payload?.content ??
                  payload?.text;
                writeData([
                  {
                    ...data,
                    type: eventType,
                    ...(typeof content === 'string' ? { content } : {}),
                    id: payload?.id ?? payload?.data?.id,
                    request_id:
                      payload?.request_id ?? payload?.data?.request_id,
                  } as ChatEvent,
                ]);
              } else if (eventType === 'video_generating') {
                const requestId =
                  payload?.data?.request_id ?? payload?.request_id;
                const estimatedSeconds =
                  payload?.data?.estimated_seconds ??
                  payload?.estimated_seconds;
                if (requestId) {
                  writeData([
                    {
                      type: 'video_generating',
                      request_id: requestId,
                      estimated_seconds:
                        typeof estimatedSeconds === 'number'
                          ? estimatedSeconds
                          : 0,
                      id: payload?.id ?? payload?.data?.id,
                    },
                  ]);
                }
              } else if (eventType === 'video_complete') {
                const requestId =
                  payload?.data?.request_id ?? payload?.request_id;
                const url = payload?.data?.url ?? payload?.url;
                if (requestId && url) {
                  writeData([
                    {
                      type: 'video_complete',
                      request_id: requestId,
                      url: url,
                      duration: payload?.data?.duration ?? payload?.duration,
                      resolution:
                        payload?.data?.resolution ?? payload?.resolution,
                      id: payload?.id ?? payload?.data?.id,
                    },
                  ]);
                }
              } else if (eventType === 'video_failed') {
                const requestId =
                  payload?.data?.request_id ?? payload?.request_id;
                const error = payload?.data?.error ?? payload?.error;
                if (requestId) {
                  writeData([
                    {
                      type: 'video_failed',
                      request_id: requestId,
                      error: error || 'Video generation failed',
                      refunded:
                        payload?.data?.refunded ?? payload?.refunded ?? false,
                      id: payload?.id ?? payload?.data?.id,
                    },
                  ]);
                }
              } else if (eventType === 'council_interview') {
                writeData([
                  {
                    type: 'council_interview',
                    ...payload?.data,
                    id: payload?.id ?? payload?.data?.id,
                    request_id:
                      payload?.request_id ?? payload?.data?.request_id,
                  },
                ]);
              } else if (eventType === 'council_progress') {
                writeData([
                  {
                    type: 'council_progress',
                    ...payload?.data,
                    id: payload?.id ?? payload?.data?.id,
                    request_id:
                      payload?.request_id ?? payload?.data?.request_id,
                  },
                ]);
              } else if (eventType === 'council_output') {
                writeData([
                  {
                    type: 'council_output',
                    ...payload?.data,
                    id: payload?.id ?? payload?.data?.id,
                    request_id:
                      payload?.request_id ?? payload?.data?.request_id,
                  },
                ]);
              } else if (eventType === 'council_done') {
                writeData([
                  {
                    type: 'council_done',
                    ...payload?.data,
                    id: payload?.id ?? payload?.data?.id,
                    request_id:
                      payload?.request_id ?? payload?.data?.request_id,
                  },
                ]);
              } else if (eventType === 'council_error') {
                writeData([
                  {
                    type: 'council_error',
                    ...payload?.data,
                    id: payload?.id ?? payload?.data?.id,
                    request_id:
                      payload?.request_id ?? payload?.data?.request_id,
                  },
                ]);
              } else if (eventType === 'final' && !sawToken) {
                const content =
                  payload?.data?.text ??
                  payload?.data?.message?.content ??
                  payload?.text ??
                  payload?.message?.content;
                if (typeof content === 'string' && content.length > 0) {
                  writeText(content);
                }
              }
            }
          }
        } finally {
          req.signal.removeEventListener('abort', onAbort);
        }
      } catch {
        if (!req.signal.aborted) {
          streamFailed = true;
        }
      } finally {
        if (textPartStarted) {
          writer.write({ type: 'text-end', id: textPartId });
        }
        if (req.signal.aborted) {
          writer.write({ type: 'abort', reason: 'request-aborted' });
        } else if (streamFailed) {
          writer.write({
            type: 'error',
            errorText: 'Backend stream ended unexpectedly.',
          });
        } else {
          writer.write({ type: 'finish', finishReason: 'stop' });
        }
      }
    },
  });

  return createUIMessageStreamResponse({
    headers: responseHeaders,
    stream,
  });
}
