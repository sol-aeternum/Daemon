import { describe, expect, it, vi } from 'vitest';

import {
  attachmentsForRetry,
  reconcileSubmission,
  shouldDetachStream,
} from '../lib/durableRecovery';
import type { DaemonMessage } from '../lib/chatMessages';

const file = { id: 'f1', name: 'a.txt', text_content: 'secret' };

describe('attachmentsForRetry', () => {
  it("re-sends a turn's files only in the conversation they were sent to", () => {
    const lastTurn = { conversationId: 'conv-a', attachments: [file] };
    expect(attachmentsForRetry(lastTurn, 'conv-a')).toEqual([file]);
    expect(attachmentsForRetry(lastTurn, 'conv-b')).toEqual([]);
    expect(attachmentsForRetry(lastTurn, null)).toEqual([]);
    expect(attachmentsForRetry(null, 'conv-a')).toEqual([]);
  });

  it('keeps an unnamed new chat retrying its own files', () => {
    const lastTurn = { conversationId: null, attachments: [file] };
    expect(attachmentsForRetry(lastTurn, null)).toEqual([file]);
    expect(attachmentsForRetry(lastTurn, 'conv-b')).toEqual([]);
  });
});

function deps(
  task: { id: string; conversationId: string; status: string } | null,
  ids: Array<string | null>,
) {
  // ids[0]: the open conversation when reconciliation starts; ids[1]: after
  // the by-key lookup returns.
  let call = 0;
  return {
    currentId: vi.fn(() => ids[Math.min(call++, ids.length - 1)]),
    taskForKey: vi.fn(async () => task),
    settle: vi.fn(),
    record: vi.fn(),
    promote: vi.fn(),
    open: vi.fn(),
    showSaved: vi.fn(async () => {}),
    lookupDelaysMs: [0, 0, 0],
  };
}

describe('reconcileSubmission', () => {
  it('settles the key of a task that already finished', async () => {
    for (const status of [
      'completed',
      'failed',
      'cancelled',
      'needs_attention',
    ]) {
      const d = deps({ id: 't', conversationId: 'conv-a', status }, ['conv-a']);
      await reconcileSubmission('k1', d);
      expect(d.settle).toHaveBeenCalledWith('k1');
      expect(d.showSaved).toHaveBeenCalledWith('t');
    }
  });

  it('keeps the key of a task that is still running', async () => {
    const d = deps({ id: 't', conversationId: 'conv-a', status: 'running' }, [
      'conv-a',
    ]);
    await reconcileSubmission('k1', d);
    expect(d.settle).not.toHaveBeenCalled();
    expect(d.record).toHaveBeenCalledWith('k1', 't');
    expect(d.showSaved).toHaveBeenCalledWith('t');
  });

  it('records the task before promoting a new chat (#479 review)', async () => {
    const d = deps({ id: 't', conversationId: 'conv-new', status: 'running' }, [
      null,
    ]);
    await reconcileSubmission('k1', d);
    // Recording clears the composer the submission was sent from, which is
    // looked up by the conversation it belongs to before promotion.
    expect(d.record.mock.invocationCallOrder[0]).toBeLessThan(
      d.promote.mock.invocationCallOrder[0],
    );
  });

  it("opens an unnamed new chat's task while the user is still there", async () => {
    const d = deps({ id: 't', conversationId: 'conv-new', status: 'running' }, [
      null,
      null,
    ]);
    await reconcileSubmission('k1', d);
    expect(d.open).toHaveBeenCalledWith('conv-new');
  });

  it('never undoes navigation that happened during the lookup', async () => {
    const d = deps({ id: 't', conversationId: 'conv-a', status: 'completed' }, [
      null,
      'conv-b',
    ]);
    await reconcileSubmission('k1', d);
    expect(d.open).not.toHaveBeenCalled();
    expect(d.showSaved).not.toHaveBeenCalled();
    expect(d.settle).toHaveBeenCalledWith('k1');
  });

  it("never moves the user to a stale submission's conversation", async () => {
    const d = deps({ id: 't', conversationId: 'conv-a', status: 'running' }, [
      'conv-b',
      'conv-b',
    ]);
    await reconcileSubmission('k1', d);
    expect(d.open).not.toHaveBeenCalled();
    expect(d.showSaved).not.toHaveBeenCalled();
  });

  it('does nothing without a key or an accepted task', async () => {
    const d = deps(null, ['conv-a']);
    await reconcileSubmission(null, d);
    expect(d.taskForKey).not.toHaveBeenCalled();
    await reconcileSubmission('k1', d);
    expect(d.settle).not.toHaveBeenCalled();
    expect(d.open).not.toHaveBeenCalled();
  });
});

it('looks again while acceptance may still be committing', async () => {
  const d = deps(null, [null, null]);
  d.taskForKey.mockResolvedValueOnce(null).mockResolvedValueOnce({
    id: 't',
    conversationId: 'conv-new',
    status: 'running',
  });
  await reconcileSubmission('k1', d);
  expect(d.taskForKey).toHaveBeenCalledTimes(2);
  // Review of #467: the pending key now belongs to the recovered chat.
  expect(d.promote).toHaveBeenCalledWith('k1', 'conv-new');
  expect(d.open).toHaveBeenCalledWith('conv-new');
});

it('stops looking when the lookup itself fails', async () => {
  const d = deps(null, [null]);
  d.taskForKey.mockResolvedValueOnce(undefined as never);
  await reconcileSubmission('k1', d);
  expect(d.taskForKey).toHaveBeenCalledTimes(1);
  expect(d.settle).not.toHaveBeenCalled();
});

describe('shouldDetachStream', () => {
  const durable = {
    id: 'm',
    role: 'assistant',
    parts: [{ type: 'data-event', data: { type: 'task', task_id: 't' } }],
  } as unknown as DaemonMessage;
  const requestBound = {
    id: 'm',
    role: 'assistant',
    parts: [{ type: 'data-event', data: { type: 'request_bound' } }],
  } as unknown as DaemonMessage;

  it('detaches a durable stream when another conversation is opened', () => {
    // Review of #467: Stop in B must never cancel A's task.
    expect(shouldDetachStream(true, durable, 'conv-a', 'conv-b')).toBe(true);
    expect(shouldDetachStream(true, durable, 'conv-a', null)).toBe(true);
  });

  it('keeps streaming in its own conversation, and never aborts request-bound turns', () => {
    expect(shouldDetachStream(true, durable, 'conv-a', 'conv-a')).toBe(false);
    expect(shouldDetachStream(false, durable, 'conv-a', 'conv-b')).toBe(false);
    expect(shouldDetachStream(true, requestBound, 'conv-a', 'conv-b')).toBe(
      false,
    );
  });
});
