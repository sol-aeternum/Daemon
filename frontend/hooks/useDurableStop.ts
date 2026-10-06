'use client';

import { useCallback, useEffect, useRef, type RefObject } from 'react';

import { getDaemonTaskId, type DaemonMessage } from '../lib/chatMessages';
import type { TaskCancelOutcome } from './useConversationHistory';
import type { StopOutcome } from './useStopGeneration';

/** Delays before each server lookup after a Stop that preceded the task id. */
export const STOP_LOOKUP_DELAYS_MS = [0, 500, 1500];

type UseDurableStopOptions = {
  messages: DaemonMessage[];
  /** The open conversation's running task id known from the server, if any. */
  activeTaskId: string | null;
  isLoading: boolean;
  /**
   * The response stream had started. A durable task's first frame names it,
   * so a started stream with no task id is request-bound chat, where the
   * abort itself is the cancellation.
   */
  streamStarted: boolean;
  /** Idempotency key of the submission in flight. */
  submissionKeyRef: RefObject<string | null>;
  cancelTask: (taskId: string) => Promise<TaskCancelOutcome>;
  /** ``null``: no task for the key; ``undefined``: unknown. */
  taskIdForKey: (key: string) => Promise<string | null | undefined>;
  lookupDelaysMs?: number[];
};

/**
 * Stop for durable chat: cancel the task explicitly (closing the app only
 * detaches) and report what the server says. A Stop pressed before the task
 * id arrived is resolved when the id arrives, or by asking the server whether
 * this submission's key created a task. A missing task id is never taken as
 * proof that nothing was accepted.
 *
 * Returns ``undefined`` when nothing durable could be in flight, so the
 * caller treats the abort itself as the cancellation (request-bound chat).
 */
export function useDurableStop({
  messages,
  activeTaskId,
  isLoading,
  streamStarted,
  submissionKeyRef,
  cancelTask,
  taskIdForKey,
  lookupDelaysMs = STOP_LOOKUP_DELAYS_MS,
}: UseDurableStopOptions): () => Promise<StopOutcome> | void {
  const pendingRef = useRef<((outcome: StopOutcome) => void) | null>(null);
  const latestTaskId = getDaemonTaskId(messages[messages.length - 1]);

  const cancelActiveTask = useCallback((): Promise<StopOutcome> | void => {
    const taskId = latestTaskId ?? activeTaskId;
    if (taskId) return cancelTask(taskId);
    if (!isLoading || !submissionKeyRef.current || streamStarted) return;
    return new Promise<StopOutcome>((resolve) => {
      pendingRef.current = resolve;
    });
  }, [
    activeTaskId,
    cancelTask,
    isLoading,
    latestTaskId,
    streamStarted,
    submissionKeyRef,
  ]);

  // The task id arrived after Stop: cancel that task.
  useEffect(() => {
    const resolve = pendingRef.current;
    if (!resolve || !latestTaskId) return;
    pendingRef.current = null;
    void cancelTask(latestTaskId).then(resolve);
  }, [cancelTask, latestTaskId]);

  // The stream ended without naming a task: ask the server.
  useEffect(() => {
    if (isLoading || !pendingRef.current) return;
    const resolve = pendingRef.current;
    pendingRef.current = null;
    const key = submissionKeyRef.current;
    void (async () => {
      // Acceptance can commit a moment after the client aborted, so look
      // again briefly before concluding the server never accepted it.
      for (const delayMs of lookupDelaysMs) {
        if (delayMs) await new Promise((wait) => setTimeout(wait, delayMs));
        const taskId = key ? await taskIdForKey(key) : undefined;
        if (taskId) {
          resolve(await cancelTask(taskId));
          return;
        }
        if (taskId === undefined) {
          resolve('unconfirmed');
          return;
        }
      }
      // Not found yet: acceptance may still be committing, so this is never
      // reported as a successful stop.
      resolve('unconfirmed');
    })();
  }, [cancelTask, isLoading, lookupDelaysMs, submissionKeyRef, taskIdForKey]);

  return cancelActiveTask;
}
