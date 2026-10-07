'use client';

import { useCallback, useEffect, useRef, type RefObject } from 'react';

import {
  getDaemonTaskId,
  isRequestBound,
  TERMINAL_TASK_STATUSES,
  type DaemonMessage,
} from '../lib/chatMessages';
import type { KeyedTask, TaskCancelOutcome } from './useConversationHistory';
import type { StopOutcome } from './useStopGeneration';

/** Delays before each server lookup after a Stop that preceded the task id. */
export const STOP_LOOKUP_DELAYS_MS = [0, 500, 1500];
/** How often a cancelling task is re-read, and for how long at most. */
export const CANCEL_POLL_MS = 1000;
export const CANCEL_POLL_LIMIT_MS = 120_000;

type UseDurableStopOptions = {
  messages: DaemonMessage[];
  /** The open conversation's running task id known from the server, if any. */
  activeTaskId: string | null;
  isLoading: boolean;
  /** Idempotency key of the submission in flight. */
  submissionKeyRef: RefObject<string | null>;
  cancelTask: (taskId: string) => Promise<TaskCancelOutcome>;
  /** ``null``: no task for the key (yet); ``undefined``: unknown. */
  taskForKey: (key: string) => Promise<KeyedTask | null | undefined>;
  /** A task's status, ``undefined`` when it cannot be read. */
  taskStatus: (taskId: string) => Promise<string | undefined>;
  lookupDelaysMs?: number[];
  pollMs?: number;
  pollLimitMs?: number;
};

const sleep = (ms: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Stop for durable chat: cancel the task explicitly (closing the app only
 * detaches) and report what the server says, only once it is true:
 *
 * - a cancellation the server accepted while the task still runs stays
 *   pending until the task is terminal;
 * - a Stop pressed before the task id arrived is resolved when the id
 *   arrives, or by asking the server whether this submission's key created a
 *   task. A missing id is never taken as proof that nothing was accepted.
 *
 * Returns ``undefined`` only for request-bound chat (the backend answered
 * without a durable task), where aborting the request is the cancellation,
 * or when nothing was in flight.
 */
export function useDurableStop({
  messages,
  activeTaskId,
  isLoading,
  submissionKeyRef,
  cancelTask,
  taskForKey,
  taskStatus,
  lookupDelaysMs = STOP_LOOKUP_DELAYS_MS,
  pollMs = CANCEL_POLL_MS,
  pollLimitMs = CANCEL_POLL_LIMIT_MS,
}: UseDurableStopOptions): () => Promise<StopOutcome> | void {
  const pendingRef = useRef<((outcome: StopOutcome) => void) | null>(null);
  const latest = messages[messages.length - 1];
  const latestTaskId = getDaemonTaskId(latest);
  const requestBound = isRequestBound(latest);

  const cancelUntilSettled = useCallback(
    async (taskId: string): Promise<StopOutcome> => {
      const outcome = await cancelTask(taskId);
      if (outcome !== 'cancelling') return outcome;
      const giveUpAt = Date.now() + pollLimitMs;
      while (Date.now() < giveUpAt) {
        await sleep(pollMs);
        const status = await taskStatus(taskId);
        if (status && TERMINAL_TASK_STATUSES.has(status)) {
          return status === 'cancelled' ? 'cancelled' : 'finished';
        }
      }
      return 'unconfirmed';
    },
    [cancelTask, pollLimitMs, pollMs, taskStatus],
  );

  const cancelActiveTask = useCallback((): Promise<StopOutcome> | void => {
    const taskId = latestTaskId ?? activeTaskId;
    if (taskId) return cancelUntilSettled(taskId);
    if (requestBound || !isLoading || !submissionKeyRef.current) return;
    return new Promise<StopOutcome>((resolve) => {
      pendingRef.current = resolve;
    });
  }, [
    activeTaskId,
    cancelUntilSettled,
    isLoading,
    latestTaskId,
    requestBound,
    submissionKeyRef,
  ]);

  // The task id (or the request-bound marker) arrived after Stop.
  useEffect(() => {
    const resolve = pendingRef.current;
    if (!resolve) return;
    if (latestTaskId) {
      pendingRef.current = null;
      void cancelUntilSettled(latestTaskId).then(resolve);
    } else if (requestBound) {
      pendingRef.current = null;
      resolve('cancelled'); // the abort itself cancelled request-bound chat
    }
  }, [cancelUntilSettled, latestTaskId, requestBound]);

  // The stream ended without naming a task: ask the server.
  useEffect(() => {
    if (isLoading || !pendingRef.current) return;
    const resolve = pendingRef.current;
    pendingRef.current = null;
    const key = submissionKeyRef.current;
    void (async () => {
      // Acceptance can commit a moment after the client aborted, so look
      // again briefly; not finding it is still never reported as stopped.
      for (const delayMs of lookupDelaysMs) {
        if (delayMs) await sleep(delayMs);
        const task = key ? await taskForKey(key) : undefined;
        if (task) {
          resolve(await cancelUntilSettled(task.id));
          return;
        }
        if (task === undefined) break;
      }
      resolve('unconfirmed');
    })();
  }, [
    cancelUntilSettled,
    isLoading,
    lookupDelaysMs,
    submissionKeyRef,
    taskForKey,
  ]);

  return cancelActiveTask;
}
