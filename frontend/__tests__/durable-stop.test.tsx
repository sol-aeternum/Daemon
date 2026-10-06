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

function withTask(taskId: string): DaemonMessage {
  return {
    id: 'a',
    role: 'assistant',
    parts: [{ type: 'data-event', data: { type: 'task', task_id: taskId } }],
  } as unknown as DaemonMessage;
}

type Props = {
  messages: DaemonMessage[];
  isLoading: boolean;
  activeTaskId: string | null;
};

function setup(
  initial: Props,
  {
    cancelTask = vi.fn().mockResolvedValue('cancelled'),
    taskIdForKey = vi.fn().mockResolvedValue(null),
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
        taskIdForKey,
        lookupDelaysMs: [0, 10, 10],
      }),
    { initialProps: initial },
  );
  return { hook, cancelTask, taskIdForKey };
}

describe('Stop for durable chat', () => {
  it('cancels a known task and reports the server outcome', async () => {
    const { hook, cancelTask } = setup(
      { messages: [user, withTask('t1')], isLoading: true, activeTaskId: null },
      { cancelTask: vi.fn().mockResolvedValue('finished') },
    );
    await expect(hook.result.current()).resolves.toBe('finished');
    expect(cancelTask).toHaveBeenCalledWith('t1');
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
    // Acceptance commits a moment after the client aborted: the first
    // lookup misses, a retry finds it.
    const taskIdForKey = vi
      .fn()
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce('committed-late');
    const { hook, cancelTask } = setup(
      { messages: [user], isLoading: true, activeTaskId: null },
      { taskIdForKey },
    );
    const outcome = hook.result.current() as Promise<StopOutcome>;
    await act(async () =>
      hook.rerender({ messages: [user], isLoading: false, activeTaskId: null }),
    );
    await expect(outcome).resolves.toBe('cancelled');
    expect(taskIdForKey).toHaveBeenCalledWith('submission-key');
    expect(cancelTask).toHaveBeenCalledWith('committed-late');
  });

  it('reports a stop as unconfirmed when the server cannot be asked', async () => {
    const { hook, cancelTask } = setup(
      { messages: [user], isLoading: true, activeTaskId: null },
      { taskIdForKey: vi.fn().mockResolvedValue(undefined) },
    );
    const outcome = hook.result.current() as Promise<StopOutcome>;
    await act(async () =>
      hook.rerender({ messages: [user], isLoading: false, activeTaskId: null }),
    );
    await expect(outcome).resolves.toBe('unconfirmed');
    expect(cancelTask).not.toHaveBeenCalled();
  });

  it('concludes nothing was accepted only after repeated lookups', async () => {
    const { hook, taskIdForKey } = setup({
      messages: [user],
      isLoading: true,
      activeTaskId: null,
    });
    const outcome = hook.result.current() as Promise<StopOutcome>;
    await act(async () =>
      hook.rerender({ messages: [user], isLoading: false, activeTaskId: null }),
    );
    await expect(outcome).resolves.toBe('cancelled');
    expect(taskIdForKey).toHaveBeenCalledTimes(3);
  });

  it('leaves a request-bound abort to the caller when nothing durable is in flight', () => {
    const { hook } = setup(
      { messages: [user], isLoading: false, activeTaskId: null },
      { key: null },
    );
    expect(hook.result.current()).toBeUndefined();
  });
});
