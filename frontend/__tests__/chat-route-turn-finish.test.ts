import { beforeEach, describe, expect, it, vi } from 'vitest';
import { POST } from '../app/api/chat/route';

type Chunk = {
  type: string;
  data?: Record<string, unknown>;
  delta?: string;
  errorText?: string;
  finishReason?: string;
  messageMetadata?: Record<string, unknown>;
};

const INTERNAL_HOST = 'http://daemon-backend-7.internal:8000';

const complete = {
  version: 1,
  outcome: 'complete',
  reason: 'provider_stop',
  provider: { state: 'confirmed', reason: 'stop', provenance: 'provider_wire' },
  limits: [],
};
const providerLength = {
  version: 1,
  outcome: 'incomplete',
  reason: 'provider_length',
  provider: {
    state: 'confirmed',
    reason: 'length',
    provenance: 'provider_wire',
  },
  limits: [{ kind: 'output', binding: true, value: 4096, unit: 'tokens' }],
};
const cancelled = {
  version: 1,
  outcome: 'cancelled',
  reason: 'cancelled',
  provider: { state: 'missing' },
  limits: [],
};
const evidenceMissing = {
  version: 1,
  outcome: 'unknown',
  reason: 'finish_evidence_missing',
  provider: { state: 'missing' },
  limits: [],
};

function chatRequest() {
  return new Request('http://test/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      messages: [{ role: 'user', content: 'hello' }],
      id: 'conv_finish',
    }),
  });
}

function frame(type: string, data: Record<string, unknown>) {
  return `event: ${type}\ndata: ${JSON.stringify(data)}`;
}

function taskSse(
  frames: string[],
  headers: Record<string, string> = {},
): Response {
  return new Response(`${frames.join('\n\n')}\n\n`, {
    headers: {
      'Content-Type': 'text/event-stream',
      'X-Daemon-Task-Id': 'task-1',
      ...headers,
    },
  });
}

async function chunks(response: Response): Promise<Chunk[]> {
  return (await response.text())
    .split('\n')
    .filter((line) => line.startsWith('data: ') && line !== 'data: [DONE]')
    .map((line) => JSON.parse(line.slice(6)) as Chunk);
}

function textDeltas(writes: Chunk[]): string[] {
  return writes
    .filter((part) => part.type === 'text-delta')
    .map((part) => part.delta ?? '');
}

beforeEach(() => {
  vi.restoreAllMocks();
  process.env.DAEMON_INTERNAL_API_URL = INTERNAL_HOST;
});

describe('chat bridge terminal evidence (#759)', () => {
  it('maps a confirmed provider stop to the SDK stop reason', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', { data: { task_id: 'task-1', status: 'running' } }),
          frame('token', { data: { text: 'Finished answer' } }),
          frame('final', {
            data: { text: 'Finished answer', turn_finish: complete },
          }),
          frame('done', {
            data: { status: 'completed', turn_finish: complete },
          }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    expect(textDeltas(writes)).toEqual(['Finished answer']);
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('stop');
    expect(finish?.messageMetadata).toEqual({
      turn_finish: complete,
      terminal_status: 'complete',
    });
    expect(writes.some((part) => part.type === 'error')).toBe(false);
  });

  it('maps a provider output limit to length, not stop', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', { data: { task_id: 'task-1', status: 'running' } }),
          frame('token', { data: { text: 'Partial' } }),
          frame('final', {
            data: {
              text: 'Partial',
              finish_reason: 'length',
              turn_finish: providerLength,
            },
          }),
          frame('done', {
            data: {
              status: 'error',
              reason: 'provider_length',
              turn_finish: providerLength,
            },
          }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    expect(textDeltas(writes)).toEqual(['Partial']);
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('length');
    expect(finish?.messageMetadata?.turn_finish).toEqual(providerLength);
  });

  it('keeps a partial answer and never claims completion when the stream errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            [
              frame('token', { data: { text: 'Partial' } }),
              frame('error', { data: { message: 'stream broke' } }),
            ].join('\n\n') + '\n\n',
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
        ),
    );
    const writes = await chunks(await POST(chatRequest()));
    expect(textDeltas(writes)).toEqual(['Partial']);
    expect(writes.some((part) => part.type === 'finish')).toBe(false);
    expect(writes.find((part) => part.type === 'error')?.errorText).toBe(
      'stream broke',
    );
  });

  it('treats EOF before the durable done frame as an interrupted stream', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          taskSse([
            frame('task', { data: { task_id: 'task-1', status: 'running' } }),
            frame('token', { data: { text: 'Partial' } }),
          ]),
        ),
    );
    const writes = await chunks(await POST(chatRequest()));
    expect(textDeltas(writes)).toEqual(['Partial']);
    expect(writes.some((part) => part.type === 'finish')).toBe(false);
    expect(writes.find((part) => part.type === 'error')?.errorText).toMatch(
      /still being worked on/,
    );
  });

  it('reads done evidence after a task-terminal error instead of ending early', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', {
            data: {
              task_id: 'task-1',
              status: 'needs_attention',
              terminal_code: 'inference_dispatch_unresolved',
            },
          }),
          frame('token', { data: { text: 'Partial' } }),
          frame('error', {
            data: {
              code: 'inference_dispatch_unresolved',
              message: 'The provider may still be working.',
              retryable: false,
            },
          }),
          frame('done', {
            data: {
              status: 'error',
              reason: 'inference_dispatch_unresolved',
              turn_finish: evidenceMissing,
            },
          }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    expect(textDeltas(writes)).toEqual(['Partial']);
    // A structured, known non-success that reached its authoritative done
    // frame is a rendered notice, not an SDK transport error.
    expect(writes.some((part) => part.type === 'message-metadata')).toBe(false);
    expect(writes.some((part) => part.type === 'error')).toBe(false);
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('other');
    expect(finish?.messageMetadata).toEqual({
      turn_finish: evidenceMissing,
      terminal_status: 'error',
      terminal_reason: 'inference_dispatch_unresolved',
    });
  });

  it('lets an authoritative done envelope overtake a provisional final', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', { data: { task_id: 'task-1', status: 'running' } }),
          frame('token', { data: { text: 'Answer' } }),
          frame('final', {
            data: {
              text: 'Answer',
              finish_reason: 'length',
              turn_finish: providerLength,
            },
          }),
          frame('done', {
            data: { status: 'completed', turn_finish: complete },
          }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('stop');
    expect(finish?.messageMetadata?.turn_finish).toEqual(complete);
  });

  it('does not revive provisional evidence after a reset and legacy done', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', { data: { task_id: 'task-1', status: 'running' } }),
          frame('final', { data: { text: 'Old', turn_finish: complete } }),
          frame('task', {
            data: { task_id: 'task-1', status: 'running', reset: true },
          }),
          frame('done', { data: { status: 'error' } }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('other');
    expect(finish?.messageMetadata).toEqual({ terminal_status: 'error' });
  });

  it('keeps a completed legacy done terminal for bookkeeping but not a stop', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          taskSse([
            frame('task', { data: { task_id: 'task-1', status: 'running' } }),
            frame('token', { data: { text: 'Legacy answer' } }),
            frame('done', { data: { status: 'completed' } }),
          ]),
        ),
    );
    const writes = await chunks(await POST(chatRequest()));
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('other');
    expect(finish?.messageMetadata).toEqual({ terminal_status: 'complete' });
  });

  it('reports a cancelled turn with its envelope and no success reason', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', { data: { task_id: 'task-1', status: 'running' } }),
          frame('token', { data: { text: 'Partial' } }),
          frame('done', {
            data: {
              status: 'cancelled',
              reason: 'cancelled',
              turn_finish: cancelled,
            },
          }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('other');
    expect(finish?.messageMetadata).toEqual({
      turn_finish: cancelled,
      terminal_status: 'cancelled',
      terminal_reason: 'cancelled',
    });
  });

  it('forwards authoritative task snapshot evidence to the live view', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', {
            data: {
              task_id: 'task-1',
              status: 'failed',
              turn_finish: providerLength,
              terminal_code: 'provider_length',
            },
          }),
          frame('done', {
            data: {
              status: 'error',
              reason: 'provider_length',
              turn_finish: providerLength,
            },
          }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    const task = writes
      .filter(
        (part) => part.type === 'data-event' && part.data?.type === 'task',
      )
      .at(-1);
    expect(task?.data).toMatchObject({
      type: 'task',
      task_id: 'task-1',
      status: 'failed',
      turn_finish: providerLength,
      terminal_code: 'provider_length',
    });
  });

  it('never turns a final-only EOF into a stop', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          [
            frame('token', { data: { text: 'Partial' } }),
            frame('final', {
              data: { text: 'Partial', turn_finish: complete },
            }),
          ].join('\n\n') + '\n\n',
          { headers: { 'Content-Type': 'text/event-stream' } },
        ),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    // The chosen final is provisional text only; no done means no evidence.
    expect(textDeltas(writes)).toEqual(['Partial']);
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('other');
    expect(finish?.messageMetadata).toBeUndefined();
  });

  it('ignores a generation-replaced done after a newer terminal snapshot', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', {
            data: {
              task_id: 'task-1',
              status: 'needs_attention',
              terminal_code: 'inference_dispatch_unresolved',
              content_generation: 2,
              turn_finish: evidenceMissing,
            },
          }),
          frame('done', {
            data: {
              status: 'completed',
              content_generation: 1,
              turn_finish: complete,
            },
          }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('other');
    expect(finish?.messageMetadata).toEqual({
      turn_finish: evidenceMissing,
      terminal_status: 'error',
      terminal_reason: 'inference_dispatch_unresolved',
    });
  });

  it('keeps same-generation snapshot evidence when done has none', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', {
            data: {
              task_id: 'task-1',
              status: 'completed',
              turn_finish: complete,
            },
          }),
          frame('done', { data: { status: 'completed' } }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('stop');
    expect(finish?.messageMetadata?.turn_finish).toEqual(complete);
  });

  it('prefers the same-generation snapshot envelope over done', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', {
            data: {
              task_id: 'task-1',
              status: 'failed',
              terminal_code: 'internal_error',
              turn_finish: evidenceMissing,
            },
          }),
          frame('done', {
            data: {
              status: 'error',
              reason: 'internal_error',
              turn_finish: providerLength,
            },
          }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('other');
    expect(finish?.messageMetadata).toEqual({
      turn_finish: evidenceMissing,
      terminal_status: 'error',
      terminal_reason: 'internal_error',
    });
  });

  it('uses done evidence when the terminal snapshot committed none', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', {
            data: { task_id: 'task-1', status: 'completed' },
          }),
          frame('done', {
            data: { status: 'completed', turn_finish: complete },
          }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('stop');
    expect(finish?.messageMetadata?.turn_finish).toEqual(complete);
  });

  it('never reports stop when done and the terminal snapshot disagree', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', {
            data: {
              task_id: 'task-1',
              status: 'failed',
              terminal_code: 'internal_error',
              turn_finish: evidenceMissing,
            },
          }),
          frame('done', {
            data: { status: 'completed', turn_finish: complete },
          }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('other');
    expect(finish?.finishReason).not.toBe('stop');
    expect(finish?.messageMetadata).toEqual({
      terminal_status: 'error',
      terminal_reason: 'internal_error',
    });
  });

  it('rejects stop evidence enclosed in an error status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        taskSse([
          frame('task', {
            data: {
              task_id: 'task-1',
              status: 'failed',
              turn_finish: complete,
            },
          }),
          frame('done', {
            data: { status: 'error', turn_finish: complete },
          }),
        ]),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    const finish = writes.find((part) => part.type === 'finish');
    expect(finish?.finishReason).toBe('other');
    expect(finish?.messageMetadata?.turn_finish).toBeUndefined();
  });

  it('attaches non-success metadata to a generic error without a stop', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          [
            frame('token', { data: { text: 'Partial' } }),
            frame('error', {
              data: {
                message: 'Provider failed.',
                turn_finish: evidenceMissing,
              },
            }),
          ].join('\n\n') + '\n\n',
          { headers: { 'Content-Type': 'text/event-stream' } },
        ),
      ),
    );
    const writes = await chunks(await POST(chatRequest()));
    const metadata = writes.find((part) => part.type === 'message-metadata');
    expect(metadata?.messageMetadata).toEqual({
      turn_finish: evidenceMissing,
      terminal_status: 'error',
    });
    expect(writes.some((part) => part.type === 'finish')).toBe(false);
    expect(writes.find((part) => part.type === 'error')?.errorText).toBe(
      'Provider failed.',
    );
  });
});
