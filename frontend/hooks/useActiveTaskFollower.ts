'use client';

import { useEffect, useRef } from 'react';

import type { Conversation } from './useConversationHistory';

/** How often an open conversation re-reads server-owned work in progress. */
export const ACTIVE_TASK_POLL_MS = 2000;

type UseActiveTaskFollowerOptions = {
  /** The open conversation as last loaded from the server. */
  conversation: Conversation | null;
  /** True while this client is itself streaming a turn. */
  isStreaming: boolean;
  /** Re-reads the open conversation; resolves to the fresh copy. */
  refresh: () => Promise<Conversation | null>;
  /** Shows the refreshed messages (partial answer, then the saved result). */
  onUpdate: (conversation: Conversation) => void;
  pollMs?: number;
};

/**
 * Follows a durable task that is running for the open conversation without a
 * live stream on this client, for example after reopening the conversation on
 * another device. It re-reads the conversation until the task is no longer
 * active, so the saved partial answer, then the final result or an honest
 * failure state, appear without a reload.
 */
export function useActiveTaskFollower({
  conversation,
  isStreaming,
  refresh,
  onUpdate,
  pollMs = ACTIVE_TASK_POLL_MS,
}: UseActiveTaskFollowerOptions): void {
  const activeTaskId = conversation?.activeTask?.id ?? null;
  const conversationId = conversation?.id ?? null;
  const refreshRef = useRef(refresh);
  const onUpdateRef = useRef(onUpdate);
  useEffect(() => {
    refreshRef.current = refresh;
    onUpdateRef.current = onUpdate;
  });

  useEffect(() => {
    if (!activeTaskId || !conversationId || isStreaming) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      const fresh = await refreshRef.current().catch(() => null);
      if (cancelled) return;
      if (fresh && fresh.id === conversationId) {
        onUpdateRef.current(fresh);
        if (!fresh.activeTask) return; // finished: the saved state is shown
      }
      timer = setTimeout(tick, pollMs);
    };

    timer = setTimeout(tick, pollMs);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [activeTaskId, conversationId, isStreaming, pollMs]);
}
