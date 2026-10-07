import { describe, expect, it, vi } from 'vitest';

import {
  attachmentsForRetry,
  reconcileSubmission,
} from '../lib/durableRecovery';

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
  expect(d.open).toHaveBeenCalledWith('conv-new');
});

it('stops looking when the lookup itself fails', async () => {
  const d = deps(null, [null]);
  d.taskForKey.mockResolvedValueOnce(undefined as never);
  await reconcileSubmission('k1', d);
  expect(d.taskForKey).toHaveBeenCalledTimes(1);
  expect(d.settle).not.toHaveBeenCalled();
});
