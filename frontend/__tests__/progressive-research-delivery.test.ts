import { readUIMessageStream, type UIMessageChunk } from 'ai';
import { afterEach, describe, expect, inject, it, vi } from 'vitest';

import { POST } from '../app/api/chat/route';
import {
  getDaemonDataEvents,
  getDaemonMessageText,
  getDaemonTaskId,
  type DaemonMessage,
} from '../lib/chatMessages';

// Real Python runner -> daemon -> completion/executor -> AttemptSink -> observer
// feeds the actual Next bridge and AI SDK client reducer. No servers, sockets,
// database, Redis instance, provider transport or stored golden SSE transcript.
// Global setup freshly produces acknowledged and uncertain transcripts before worker contention,
// bounded by the same 10s setup deadline. Missing setup cannot fall back to a cache.

async function bridge(frames: string[], taskId: string) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(frames.join(''), {
        headers: {
          'Content-Type': 'text/event-stream',
          'X-Daemon-Task-Id': taskId,
        },
      }),
    ),
  );
  const response = await POST(
    new Request('http://test/api/chat', {
      method: 'POST',
      body: JSON.stringify({
        messages: [
          { role: 'user', content: 'Briefly research the launch date' },
        ],
        idempotency_key: 'same-progressive-submission',
        client_features: ['task-reset', 'task-cancel'],
      }),
    }),
  );
  const chunks = (await response.text())
    .split('\n')
    .filter((line) => line.startsWith('data: ') && line !== 'data: [DONE]')
    .map((line) => JSON.parse(line.slice(6)) as UIMessageChunk);
  let message: DaemonMessage | undefined;
  const errors: unknown[] = [];
  for await (const current of readUIMessageStream<DaemonMessage>({
    stream: new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(chunk);
        controller.close();
      },
    }),
    terminateOnError: false,
    onError: (error) => errors.push(error),
  })) {
    message = current;
  }
  expect(message).toBeDefined();
  return { message: message!, chunks, errors };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('progressive research backend-to-client delivery', () => {
  const transcripts = inject('progressiveResearch');

  it('keeps a complete early section active, then reopens the same ordered answer without duplicating it', async () => {
    const { completed: transcript } = transcripts;
    const early = await bridge(transcript.early_frames, transcript.task_id);
    expect(getDaemonMessageText(early.message)).toBe(transcript.prefix);
    expect(getDaemonTaskId(early.message)).toBe(transcript.task_id);
    expect(early.chunks.some((chunk) => chunk.type === 'finish')).toBe(false);
    expect(early.errors).toHaveLength(1); // Detached, not falsely completed.

    const reopened = await bridge(
      transcript.reconnect_frames,
      transcript.task_id,
    );
    expect(getDaemonTaskId(reopened.message)).toBe(transcript.task_id);
    expect(getDaemonMessageText(reopened.message)).toBe(transcript.final);
    expect(transcript.final.split(transcript.prefix)).toHaveLength(2);
    expect(transcript.final).toContain('Correction:');
    expect(reopened.errors).toEqual([]);
    expect(
      reopened.chunks.filter((chunk) => chunk.type === 'finish'),
    ).toHaveLength(1);
    expect(
      getDaemonDataEvents([reopened.message]).some(
        (event) => event.type === 'task' && event.status === 'completed',
      ),
    ).toBe(true);
    // Reopening consumed saved backend output, not another research execution.
    expect(transcript.dispatches).toBe(3);
    expect(transcript.reads).toBe(2);
  });

  it('retains useful text and actual existing terminal outcomes without converting source limits into success claims', async () => {
    const limited = transcripts.source_incomplete;
    const answer = await bridge(limited.reconnect_frames, limited.task_id);
    expect(getDaemonMessageText(answer.message)).toBe(limited.final);
    expect(limited.final).toContain('could not be read');
    expect(limited.final).toContain('remains unverified');
    // Existing completed means the answer was committed, not every source read succeeded.
    expect(
      getDaemonDataEvents([answer.message]).some(
        (event) => event.type === 'tool_result' && event.outcome === 'failed',
      ),
    ).toBe(true);

    for (const status of ['failed', 'cancelled'] as const) {
      const terminal = transcripts[status];
      expect(terminal.status).toBe(status);
      const result = await bridge(terminal.frames, terminal.task_id);
      expect(getDaemonMessageText(result.message)).toContain(terminal.final);
      expect(
        getDaemonDataEvents([result.message]).some(
          (event) => event.type === 'task' && event.status === status,
        ),
      ).toBe(true);
      expect(
        getDaemonDataEvents([result.message]).some(
          (event) => event.type === 'task' && event.status === 'completed',
        ),
      ).toBe(false);
    }
  });

  it('keeps uncertain writes and in-write cancellation nonterminal without claiming a failed or cancelled task', async () => {
    for (const key of [
      'uncertain_write',
      'uncertain_cancel',
      'uncertain_periodic',
    ] as const) {
      const transcript = transcripts[key];
      expect(transcript.status).toMatch(
        /^task_(observation|transition)_indeterminate$/,
      );
      const result = await bridge(transcript.frames, transcript.task_id);
      expect(getDaemonMessageText(result.message)).toBe(transcript.final);
      expect(result.errors).toHaveLength(1); // Deliberately detached, not a terminal ACK.
      expect(result.chunks.some((chunk) => chunk.type === 'finish')).toBe(
        false,
      );
      const tasks = getDaemonDataEvents([result.message]).filter(
        (event) => event.type === 'task',
      );
      expect(tasks.some((event) => event.status === 'running')).toBe(true);
      expect(
        tasks.some((event) =>
          ['completed', 'failed', 'cancelled'].includes(event.status ?? ''),
        ),
      ).toBe(false);
    }
  });
});
