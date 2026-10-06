import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { useDurableStop } from '../hooks/useDurableStop';
import type { DaemonMessage } from '../lib/chatMessages';
import type { StopOutcome } from '../hooks/useStopGeneration';

const user: DaemonMessage = {
  id: 'u',
  role: 'user',
  parts: [{ type: 'text', text: 'hello' }],
} as DaemonMessage;

function assistantWith(data: Record<string, unknown>): DaemonMessage {
  return {
    id: 'a',
    role: 'assistant',
    parts: [{ type: 'data-event', data }],
  } as unknown as DaemonMessage;
}

const withTask = (taskId: string) =>
  assistantWith({ type: 'task', task_id: taskId, status: 'accepted' });

type Props = {
  messages: DaemonMessage[];
  isLoading: boolean;
  activeTaskId: string | null;
};

function setup(
  initial: Props,
  {
    cancelTask = vi.fn().mockResolvedValue('cancelled'),
    taskForKey = vi.fn().mockResolvedValue(null),
    taskStatus = vi.fn().mockResolvedValue('running'),
    key = 'submission-key' as string | null,
  } = {},
) {
  const submissionKeyRef = { current: key };
  const hook = renderHook(
    (props: Props) =>
      useDurableStop({
        ...props,
        submissionKeyRef,
        cancelTask,
        taskForKey,
        taskStatus,
        lookupDelaysMs: [0, 10, 10],
        pollMs: 5,
        pollLimitMs: 200,
      }),
    { initialProps: initial },
  );
  return { hook, cancelTask, taskForKey, taskStatus };
}

const task = (id: string) => ({ id, conversationId: 'c', status: 'running' });

describe('Stop for durable chat', () => {
  it('cancels a known task and reports the server outcome', async () => {
    const { hook, cancelTask } = setup(
      { messages: [user, withTask('t1')], isLoading: true, activeTaskId: null },
      { cancelTask: vi.fn().mockResolvedValue('finished') },
    );
    await expect(hook.result.current()).resolves.toBe('finished');
    expect(cancelTask).toHaveBeenCalledWith('t1');
  });

  it('stays pending while an accepted cancellation is still stopping', async () => {
    // 200 with status "running": cancel requested, task not stopped yet.
    const taskStatus = vi
      .fn()
      .mockResolvedValueOnce('running')
      .mockResolvedValueOnce(undefined) // a failed read is not an outcome
      .mockResolvedValueOnce('cancelled');
    const { hook } = setup(
      { messages: [user, withTask('t1')], isLoading: true, activeTaskId: null },
      { cancelTask: vi.fn().mockResolvedValue('cancelling'), taskStatus },
    );
    await expect(hook.result.current()).resolves.toBe('cancelled');
    expect(taskStatus).toHaveBeenCalledTimes(3);
  });

  it('reports unconfirmed if a stopping task never reaches a terminal state', async () => {
    const { hook } = setup(
      { messages: [user, withTask('t1')], isLoading: true, activeTaskId: null },
      { cancelTask: vi.fn().mockResolvedValue('cancelling') },
    );
    await expect(hook.result.current()).resolves.toBe('unconfirmed');
  });

  it('uses the followed server task id when this client is not streaming', async () => {
    const { hook, cancelTask } = setup({
      messages: [user],
      isLoading: false,
      activeTaskId: 'followed',
    });
    await expect(hook.result.current()).resolves.toBe('cancelled');
    expect(cancelTask).toHaveBeenCalledWith('followed');
  });

  it('cancels the task whose id arrives after Stop', async () => {
    const { hook, cancelTask } = setup({
      messages: [user],
      isLoading: true,
      activeTaskId: null,
    });
    const outcome = hook.result.current() as Promise<StopOutcome>;
    hook.rerender({
      messages: [user, withTask('late')],
      isLoading: true,
      activeTaskId: null,
    });
    await expect(outcome).resolves.toBe('cancelled');
    expect(cancelTask).toHaveBeenCalledWith('late');
  });

  it('finds a task accepted after the abort and cancels it', async () => {
    const taskForKey = vi
      .fn()
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(task('committed-late'));
    const { hook, cancelTask } = setup(
      { messages: [user], isLoading: true, activeTaskId: null },
      { taskForKey },
    );
    const outcome = hook.result.current() as Promise<StopOutcome>;
    await act(async () =>
      hook.rerender({ messages: [user], isLoading: false, activeTaskId: null }),
    );
    await expect(outcome).resolves.toBe('cancelled');
    expect(taskForKey).toHaveBeenCalledWith('submission-key');
    expect(cancelTask).toHaveBeenCalledWith('committed-late');
  });

  it('never reports success when the server has not found the task yet', async () => {
    const { hook, taskForKey } = setup({
      messages: [user],
      isLoading: true,
      activeTaskId: null,
    });
    const outcome = hook.result.current() as Promise<StopOutcome>;
    await act(async () =>
      hook.rerender({ messages: [user], isLoading: false, activeTaskId: null }),
    );
    await expect(outcome).resolves.toBe('unconfirmed');
    expect(taskForKey).toHaveBeenCalledTimes(3);
  });

  it('treats a turn the backend marked request-bound as cancelled by the abort', async () => {
    const requestBound = assistantWith({ type: 'request_bound' });
    const started = setup({
      messages: [user, requestBound],
      isLoading: true,
      activeTaskId: null,
    });
    expect(started.hook.result.current()).toBeUndefined();

    // Stop pressed before the marker arrived: resolved by the marker.
    const early = setup({
      messages: [user],
      isLoading: true,
      activeTaskId: null,
    });
    const outcome = early.hook.result.current() as Promise<StopOutcome>;
    early.hook.rerender({
      messages: [user, requestBound],
      isLoading: true,
      activeTaskId: null,
    });
    await expect(outcome).resolves.toBe('cancelled');
    expect(early.cancelTask).not.toHaveBeenCalled();
  });

  it('leaves nothing to do when no submission is in flight', () => {
    const { hook } = setup(
      { messages: [user], isLoading: false, activeTaskId: null },
      { key: null },
    );
    expect(hook.result.current()).toBeUndefined();
  });
});
