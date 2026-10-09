import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { POST } from '../app/api/chat/route';
import { useActiveTaskFollower } from '../hooks/useActiveTaskFollower';
import type { Conversation } from '../hooks/useConversationHistory';
import {
  useStopGeneration,
  type StopOutcome,
} from '../hooks/useStopGeneration';
import {
  getDaemonDataEvents,
  getDaemonMessageText,
  getDaemonTaskId,
  isRequestBound,
  normalizeDaemonMessage,
  type DaemonMessage,
} from '../lib/chatMessages';

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

function frame(eventType: string, data: Record<string, unknown>): string {
  return `event: ${eventType}\ndata: ${JSON.stringify({ data })}`;
}

function sse(frames: string[], headers: Record<string, string> = {}) {
  return new Response(`${frames.join('\n\n')}\n\n`, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream', ...headers },
  });
}

function chatRequest(body: Record<string, unknown>) {
  return new Request('http://test/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      messages: [{ role: 'user', content: 'hello' }],
      ...body,
    }),
  });
}

/** Rebuild a UI message from stream chunks, the way the AI SDK appends parts. */
function assemble(chunks: UIMessageChunk[]): DaemonMessage {
  const parts: DaemonMessage['parts'] = [];
  const open = new Map<string, { type: 'text'; text: string }>();
  for (const chunk of chunks) {
    if (chunk.type === 'text-start') {
      const part = { type: 'text' as const, text: '' };
      open.set(chunk.id as string, part);
      parts.push(part);
    } else if (chunk.type === 'text-delta') {
      open.get(chunk.id as string)!.text += chunk.delta as string;
    } else if (chunk.type === 'data-event') {
      parts.push({ type: 'data-event', data: chunk.data } as never);
    }
  }
  return { id: 'm', role: 'assistant', parts } as DaemonMessage;
}

describe('durable chat route bridge', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('forwards the idempotency key and announces the task id', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        sse([frame('token', { text: 'Hi' })], { 'X-Daemon-Task-Id': 'task-1' }),
      );
    vi.stubGlobal('fetch', fetchMock);

    const response = await POST(
      chatRequest({ idempotency_key: 'key-123', id: 'conv-1' }),
    );
    const message = assemble(await readUIMessageChunks(response));

    const sent = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(sent.headers).get('Idempotency-Key')).toBe('key-123');
    expect(getDaemonTaskId(message)).toBe('task-1');
    expect(getDaemonMessageText(message)).toBe('Hi');
  });

  it('reports a durable stream that ends before the task did as a disconnect', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sse(
            [
              frame('task', { task_id: 'task-1', status: 'running' }),
              frame('token', { text: 'Partial' }),
            ],
            { 'X-Daemon-Task-Id': 'task-1' },
          ),
        ),
    );
    const chunks = await readUIMessageChunks(
      await POST(chatRequest({ id: 'conv-1' })),
    );
    // Not a normal finish: the client keeps the key and reconciles.
    expect(chunks.some((chunk) => chunk.type === 'finish')).toBe(false);
    expect(chunks.some((chunk) => chunk.type === 'error')).toBe(true);
  });

  it('finishes normally once the task reported a terminal status, also via a reset', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse(
          [
            frame('token', { text: 'Uncommitted text' }),
            frame('task', {
              task_id: 'task-1',
              status: 'cancelled',
              reset: true,
              content: 'Kept',
            }),
          ],
          { 'X-Daemon-Task-Id': 'task-1' },
        ),
      ),
    );
    const chunks = await readUIMessageChunks(
      await POST(chatRequest({ id: 'conv-1' })),
    );
    expect(chunks.some((chunk) => chunk.type === 'finish')).toBe(true);
    const message = assemble(chunks);
    const text = getDaemonMessageText(message);
    expect(text.startsWith('Kept')).toBe(true); // then the cancellation notice
    expect(text).not.toContain('Uncommitted');
    const statuses = getDaemonDataEvents([message])
      .filter((event) => event.type === 'task')
      .map((event) => (event as { status?: string }).status);
    expect(statuses).toContain('cancelled');
  });

  it('marks a refusal before acceptance, but not an ambiguous server error', async () => {
    const reply = (status: number, code: string) =>
      new Response(JSON.stringify({ detail: { code, message: 'no' } }), {
        status,
        headers: { 'Content-Type': 'application/json' },
      });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(reply(403, 'route_unavailable')),
    );
    let chunks = await readUIMessageChunks(
      await POST(chatRequest({ id: 'c' })),
    );
    const rejected = chunks.find(
      (chunk) =>
        chunk.type === 'data-event' &&
        (chunk.data as { type?: string }).type === 'request_rejected',
    );
    expect(rejected?.data).toMatchObject({
      status: 403,
      code: 'route_unavailable',
    });

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply(503, 'internal')));
    chunks = await readUIMessageChunks(await POST(chatRequest({ id: 'c' })));
    expect(
      chunks.some(
        (chunk) =>
          chunk.type === 'data-event' &&
          (chunk.data as { type?: string }).type === 'request_rejected',
      ),
    ).toBe(false);
  });

  it('forwards durable features only when the browser declared them', async () => {
    const fetchMock = vi.fn().mockResolvedValue(sse([]));
    vi.stubGlobal('fetch', fetchMock);
    // An older cached bundle sends no client_features: it must stay
    // request-bound even though this bridge supports durable tasks.
    await (await POST(chatRequest({}))).text();
    expect(
      new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers).has(
        'X-Daemon-Client-Features',
      ),
    ).toBe(false);
    await (
      await POST(
        chatRequest({
          client_features: ['task-cancel', 'task-reset', 'bogus'],
        }),
      )
    ).text();
    expect(
      new Headers((fetchMock.mock.calls[1][1] as RequestInit).headers).get(
        'X-Daemon-Client-Features',
      ),
    ).toBe('task-cancel, task-reset');
  });

  it('marks a turn the backend answered without a task as request-bound', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(sse([frame('token', { text: 'Hi' })])),
    );
    const message = assemble(
      await readUIMessageChunks(
        await POST(
          chatRequest({ client_features: ['task-cancel', 'task-reset'] }),
        ),
      ),
    );
    expect(isRequestBound(message)).toBe(true);
    expect(getDaemonTaskId(message)).toBeNull();
  });

  it('shows a cancellation made on another device in the live view', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sse(
            [
              frame('token', { text: 'Partial' }),
              frame('task', { task_id: 'task-1', status: 'cancelled' }),
              frame('done', { status: 'cancelled' }),
            ],
            { 'X-Daemon-Task-Id': 'task-1' },
          ),
        ),
    );
    const message = assemble(
      await readUIMessageChunks(await POST(chatRequest({}))),
    );
    expect(getDaemonMessageText(message)).toMatch(/^Partial\n\nStopped/);
  });

  it('drops events from an attempt that a reset replaced', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sse([
            frame('task', { task_id: 'task-1', status: 'running' }),
            frame('tool_call', { name: 'web_search', arguments: {} }),
            frame('task', { task_id: 'task-1', reset: true, content: 'New' }),
          ]),
        ),
    );
    const message = assemble(
      await readUIMessageChunks(await POST(chatRequest({}))),
    );
    const types = getDaemonDataEvents([message]).map((event) => event.type);
    expect(types).not.toContain('tool_call');
  });

  it('drops an invalid idempotency key rather than forwarding it', async () => {
    const fetchMock = vi.fn().mockResolvedValue(sse([]));
    vi.stubGlobal('fetch', fetchMock);
    await (await POST(chatRequest({ idempotency_key: 'bad key\n' }))).text();
    const sent = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(sent.headers).has('Idempotency-Key')).toBe(false);
  });

  it('replaces the shown text when a regenerated attempt resets the content', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse([
          frame('task', { task_id: 'task-1', status: 'running' }),
          frame('token', { text: 'The first attempt was long' }),
          frame('task', {
            task_id: 'task-1',
            reset: true,
            content: 'Short',
            content_generation: 2,
          }),
          frame('token', { text: '.' }),
        ]),
      ),
    );
    const response = await POST(chatRequest({ id: 'conv-1' }));
    const message = assemble(await readUIMessageChunks(response));
    expect(getDaemonMessageText(message)).toBe('Short.');
  });
});

describe('task resets', () => {
  it('keep the conversation identity and task status from before the reset', () => {
    // Review of #467: a fast regeneration must not hide the conversation the
    // backend created, or the page never adopts the new chat.
    const message = {
      id: 'm',
      role: 'assistant',
      parts: [
        {
          type: 'data-event',
          data: { type: 'conversation', conversation_id: 'conv-new' },
        },
        { type: 'data-event', data: { type: 'tool_call', name: 'web_search' } },
        { type: 'data-event', data: { type: 'task_reset', task_id: 't' } },
        { type: 'text', text: 'Regenerated' },
      ],
    } as unknown as DaemonMessage;
    const types = getDaemonDataEvents([message]).map((event) => event.type);
    expect(types).toContain('conversation');
    expect(types).not.toContain('tool_call'); // the replaced attempt's progress
  });
});

describe('live task outcomes', () => {
  it('shows a cancellation that came before the first token', () => {
    const message = {
      id: 'm',
      role: 'assistant',
      parts: [
        {
          type: 'data-event',
          data: { type: 'task', task_id: 'task-1', status: 'cancelled' },
        },
      ],
    } as unknown as DaemonMessage;
    expect(getDaemonMessageText(message)).toBe('Stopped.');
  });
});

describe('persisted task outcomes', () => {
  it('shows a cancel that won the completion race, with no reason code', () => {
    const message = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: 'A late answer',
      status: 'cancelled',
      metadata: { terminal_status: 'cancelled' },
    })!;
    expect(getDaemonMessageText(message)).toBe(
      'A late answer\n\nStopped before the answer was finished.',
    );
  });

  it('discloses an answer regenerated after an interruption', () => {
    const message = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: 'The full answer',
      status: 'complete',
      metadata: {
        terminal_status: 'complete',
        regenerated_after_interruption: 1,
      },
    })!;
    expect(getDaemonMessageText(message)).toBe(
      'The full answer\n\nThis answer was regenerated after an interruption.',
    );
  });

  it('keeps a stop visible on a partial answer from any device', () => {
    const message = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: 'Half an answer',
      status: 'cancelled',
      metadata: { terminal_reason: 'cancelled' },
    })!;
    expect(getDaemonMessageText(message)).toMatch(
      /^Half an answer\n\nStopped before the answer was finished\.$/,
    );
  });

  it('explains any task code, including ones without specific wording', () => {
    for (const code of ['extended_agents_exceeded', 'some_future_code']) {
      const message = normalizeDaemonMessage({
        id: 'a',
        role: 'assistant',
        content: '',
        status: 'error',
        metadata: { terminal_reason: code },
      })!;
      expect(getDaemonMessageText(message).length).toBeGreaterThan(10);
    }
  });

  it('shows an honest notice instead of an empty failed answer', () => {
    const message = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: '',
      status: 'error',
      metadata: { terminal_reason: 'interrupted' },
    })!;
    expect(getDaemonMessageText(message)).toMatch(/interrupted/);
  });

  it('keeps partial text and still warns about an uncertain effect', () => {
    const message = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: 'Sending the reminder now',
      status: 'error',
      metadata: { terminal_reason: 'uncertain_effect' },
    })!;
    const text = getDaemonMessageText(message);
    expect(text.startsWith('Sending the reminder now')).toBe(true);
    expect(text).toMatch(/may already have happened/);
  });

  it('leaves legacy free-text reasons and user messages alone', () => {
    const legacy = normalizeDaemonMessage({
      id: 'a',
      role: 'assistant',
      content: '',
      metadata: { terminal_reason: 'Client disconnected during streaming' },
    })!;
    expect(getDaemonMessageText(legacy)).toBe('');
    const user = normalizeDaemonMessage({
      id: 'u',
      role: 'user',
      content: 'hi',
      metadata: { terminal_reason: 'interrupted' },
    })!;
    expect(getDaemonMessageText(user)).toBe('hi');
  });
});

function StopHarness({
  stop,
  beforeStop,
}: {
  stop: () => void;
  beforeStop: () => void;
}) {
  const { stopGeneration } = useStopGeneration({
    messages: [{ id: 'a', role: 'assistant' }],
    stop,
    archiveEvents: () => {},
    conversationId: 'conv-1',
    beforeStop,
  });
  return (
    <button type="button" onClick={stopGeneration}>
      Stop
    </button>
  );
}

function ConfirmingStopHarness({
  confirm,
  onStopResolved,
}: {
  confirm: Promise<StopOutcome>;
  onStopResolved: (outcome: StopOutcome) => void;
}) {
  const { stopGeneration, stoppedMessageIds, stoppingMessageIds } =
    useStopGeneration({
      messages: [{ id: 'a', role: 'assistant' }],
      stop: () => {},
      archiveEvents: () => {},
      conversationId: 'conv-1',
      beforeStop: () => confirm,
      onStopResolved,
    });
  return (
    <>
      <button type="button" onClick={stopGeneration}>
        Stop
      </button>
      {stoppedMessageIds.has('a') && <span>(stopped)</span>}
      {stoppingMessageIds.has('a') && <span>(stopping)</span>}
    </>
  );
}

describe('Stop confirmation', () => {
  it('shows stopping until the server answers, then stopped', async () => {
    let answer!: (outcome: StopOutcome) => void;
    const confirm = new Promise<StopOutcome>((resolve) => {
      answer = resolve;
    });
    const onStopResolved = vi.fn();
    render(
      <ConfirmingStopHarness
        confirm={confirm}
        onStopResolved={onStopResolved}
      />,
    );
    fireEvent.click(screen.getByText('Stop'));
    expect(screen.getByText('(stopping)')).toBeTruthy();
    expect(screen.queryByText('(stopped)')).toBeNull();
    await act(async () => answer('cancelled'));
    expect(screen.getByText('(stopped)')).toBeTruthy();
    expect(screen.queryByText('(stopping)')).toBeNull();
    expect(onStopResolved).toHaveBeenCalledWith('cancelled');
  });

  it.each(['finished', 'unconfirmed'] as const)(
    'never shows stopped when the outcome is %s',
    async (outcome) => {
      const onStopResolved = vi.fn();
      render(
        <ConfirmingStopHarness
          confirm={Promise.resolve(outcome)}
          onStopResolved={onStopResolved}
        />,
      );
      await act(async () => fireEvent.click(screen.getByText('Stop')));
      expect(screen.queryByText('(stopped)')).toBeNull();
      expect(screen.queryByText('(stopping)')).toBeNull();
      expect(onStopResolved).toHaveBeenCalledWith(outcome);
    },
  );
});

describe('Stop on a durable task', () => {
  it('cancels the task before detaching the stream', () => {
    const calls: string[] = [];
    render(
      <StopHarness
        stop={() => calls.push('stop')}
        beforeStop={() => calls.push('cancel')}
      />,
    );
    fireEvent.click(screen.getByText('Stop'));
    expect(calls).toEqual(['cancel', 'stop']);
  });
});

function conversation(active: boolean, content: string): Conversation {
  return {
    id: 'conv-1',
    title: 't',
    messages: [
      { id: 'u', role: 'user', parts: [{ type: 'text', text: 'hello' }] },
      {
        id: 'a',
        role: 'assistant',
        status: active ? 'streaming' : 'complete',
        parts: [{ type: 'text', text: content }],
      },
    ] as DaemonMessage[],
    createdAt: '',
    updatedAt: '',
    pinned: false,
    title_locked: false,
    status: 'active',
    metadata: {},
    activeTask: active
      ? { id: 'task-1', status: 'running', content, cancelRequested: false }
      : null,
  };
}

function FollowerHarness(props: {
  conversation: Conversation | null;
  isStreaming: boolean;
  refresh: () => Promise<Conversation | null>;
  onUpdate: (conversation: Conversation) => void;
}) {
  useActiveTaskFollower({ ...props, pollMs: 100 });
  return null;
}

describe('following a task started on another device', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('shows progress, then the saved result, then stops polling', async () => {
    const refresh = vi
      .fn()
      .mockResolvedValueOnce(conversation(true, 'Partial'))
      .mockResolvedValueOnce(conversation(false, 'Final answer'));
    const shown: string[] = [];
    render(
      <FollowerHarness
        conversation={conversation(true, '')}
        isStreaming={false}
        refresh={refresh}
        onUpdate={(c) =>
          shown.push(getDaemonMessageText(c.messages[1] as DaemonMessage))
        }
      />,
    );
    for (let i = 0; i < 4; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(100);
      });
    }
    expect(shown).toEqual(['Partial', 'Final answer']);
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it('does not poll while this client is streaming or nothing is active', async () => {
    const refresh = vi.fn().mockResolvedValue(null);
    const { rerender } = render(
      <FollowerHarness
        conversation={conversation(true, '')}
        isStreaming
        refresh={refresh}
        onUpdate={() => {}}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    rerender(
      <FollowerHarness
        conversation={conversation(false, 'done')}
        isStreaming={false}
        refresh={refresh}
        onUpdate={() => {}}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(refresh).not.toHaveBeenCalled();
  });
});

describe('notices on partial failed answers', () => {
  it('keeps the partial text and says why it stopped, for any task code', () => {
    for (const code of ['interrupted', 'budget_exceeded', 'some_future_code']) {
      const text = getDaemonMessageText(
        normalizeDaemonMessage({
          id: 'a',
          role: 'assistant',
          content: 'Partial',
          status: 'error',
          metadata: { terminal_reason: code },
        })!,
      );
      expect(text.startsWith('Partial\n\n')).toBe(true);
      expect(text.length).toBeGreaterThan('Partial\n\n'.length + 10);
    }
  });
});

describe('generation frontier (#477)', () => {
  it('discloses an explicit regeneration count from the live reset frame', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse(
          [
            frame('task', {
              task_id: 'task-1',
              status: 'running',
              content_generation: 2,
            }),
            frame('token', { text: 'The first attempt was long' }),
            frame('task', {
              task_id: 'task-1',
              reset: true,
              content_generation: 2,
              regenerated_after_interruption: 1,
              event_seq: 10,
              content: 'Regenerated',
            }),
            frame('token', { text: '.', content_generation: 2 }),
            frame('task', {
              task_id: 'task-1',
              status: 'completed',
              content_generation: 2,
            }),
          ],
          { 'X-Daemon-Task-Id': 'task-1' },
        ),
      ),
    );
    const response = await POST(chatRequest({ id: 'conv-1' }));
    const chunks = await readUIMessageChunks(response);
    expect(chunks.some((chunk) => chunk.type === 'finish')).toBe(true);
    const message = assemble(chunks);
    // Only an explicit positive count is regeneration evidence; the notice
    // rides on the text written after the reset.
    expect(getDaemonMessageText(message)).toBe(
      'Regenerated.\n\nThis answer was regenerated after an interruption.',
    );
    // The reset marker itself is not attempt progress; it is on the raw
    // stream (it stops currentParts, so it is not re-collected later).
    expect(
      chunks.some(
        (chunk) =>
          chunk.type === 'data-event' &&
          (chunk.data as { type?: string }).type === 'task_reset',
      ),
    ).toBe(true);
  });

  it('does not call a same-generation correction (count 0 or absent) a regeneration', async () => {
    const stream = (resetMeta: Record<string, unknown>, content: string) =>
      sse(
        [
          frame('token', { text: 'First', content_generation: 1 }),
          frame('task', {
            task_id: 'task-1',
            reset: true,
            content,
            content_generation: 1,
            ...resetMeta,
          }),
          frame('task', {
            task_id: 'task-1',
            status: 'completed',
            content_generation: 1,
          }),
        ],
        { 'X-Daemon-Task-Id': 'task-1' },
      );

    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          stream({ regenerated_after_interruption: 0 }, 'Corrected'),
        ),
    );
    let message = assemble(
      await readUIMessageChunks(await POST(chatRequest({ id: 'conv-1' }))),
    );
    expect(getDaemonMessageText(message)).toBe('Corrected');

    // A bare reset (a deferred attempt or envelope switch) is not evidence.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream({}, 'Deferred')));
    message = assemble(
      await readUIMessageChunks(await POST(chatRequest({ id: 'conv-1' }))),
    );
    expect(getDaemonMessageText(message)).toBe('Deferred');
  });

  it('ignores stale-generation tokens, progress, terminal status and late resets', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse(
          [
            frame('task', {
              task_id: 'task-1',
              status: 'running',
              content_generation: 2,
            }),
            frame('token', { text: 'Current', content_generation: 2 }),
            // Late/duplicate progress from the replaced attempt:
            frame('token', { text: ' stale', content_generation: 1 }),
            frame('tool_call', {
              name: 'web_search',
              arguments: {},
              content_generation: 1,
            }),
            frame('task', {
              task_id: 'task-1',
              status: 'running',
              content_generation: 1,
            }),
            // Even terminal state and a reset from the old generation:
            frame('task', {
              task_id: 'task-1',
              status: 'completed',
              content_generation: 1,
            }),
            frame('task', {
              task_id: 'task-1',
              reset: true,
              content: 'Old attempt',
              content_generation: 1,
            }),
            // Back on the current generation:
            frame('token', { text: ' done', content_generation: 2 }),
            // A malformed tag (not a non-negative integer) is an untagged frame.
            frame('token', { text: ' legacy', content_generation: 1.5 }),
            frame('task', {
              task_id: 'task-1',
              status: 'completed',
              content_generation: 2,
            }),
          ],
          { 'X-Daemon-Task-Id': 'task-1' },
        ),
      ),
    );
    const response = await POST(chatRequest({ id: 'conv-1' }));
    const chunks = await readUIMessageChunks(response);
    // Stale text never mutates the shown answer.
    const message = assemble(chunks);
    expect(getDaemonMessageText(message)).toBe('Current done legacy');
    const dataEvents = getDaemonDataEvents([message]);
    const types = dataEvents.map((event) => event.type);
    expect(types).not.toContain('tool_call');
    expect(types).not.toContain('task_reset');
    const statuses = dataEvents
      .filter((event) => event.type === 'task')
      .map((event) => (event as { status?: string }).status);
    // 'accepted' is this bridge's announcement; stale generations contributed
    // nothing (running/completed are this bridge's current-generation frames).
    expect(statuses).toEqual(['accepted', 'running', 'completed']);
    // The only terminal settled the stream; there was no early finish.
    expect(chunks.some((chunk) => chunk.type === 'finish')).toBe(true);
    expect(chunks.some((chunk) => chunk.type === 'error')).toBe(false);
  });

  it('passes untagged legacy frames and forwards task-frame recovery metadata', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse(
          [
            frame('task', { task_id: 'task-1', status: 'running' }),
            frame('token', { text: 'Legacy' }),
            frame('tool_result', {
              name: 'web_fetch',
              result: 'body',
              tool_call_id: 'f1',
              outcome: 'succeeded',
            }),
            frame('task', {
              task_id: 'task-1',
              status: 'completed',
              event_seq: 12,
              content_generation: 1,
              lifecycle_kind: 'regenerate',
              lifecycle_epoch: 3,
              operation_id: 'op-9',
            }),
          ],
          { 'X-Daemon-Task-Id': 'task-1' },
        ),
      ),
    );
    const message = assemble(
      await readUIMessageChunks(await POST(chatRequest({ id: 'conv-1' }))),
    );
    expect(getDaemonMessageText(message)).toContain('Legacy');
    const events = getDaemonDataEvents([message]);
    const finalTask = events.find(
      (event) =>
        event.type === 'task' &&
        (event as { status?: string }).status === 'completed',
    );
    expect(finalTask).toMatchObject({
      event_seq: 12,
      content_generation: 1,
      lifecycle_kind: 'regenerate',
      lifecycle_epoch: 3,
      operation_id: 'op-9',
    });
    // The bounded outcome the backend recorded travels with the result.
    const toolResult = events.find((event) => event.type === 'tool_result');
    expect(toolResult).toMatchObject({
      outcome: 'succeeded',
      name: 'web_fetch',
    });
  });
});
