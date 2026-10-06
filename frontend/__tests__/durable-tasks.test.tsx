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
  getDaemonMessageText,
  getDaemonTaskId,
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

describe('persisted task outcomes', () => {
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
