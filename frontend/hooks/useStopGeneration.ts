'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

type StoppableMessage = {
  id?: string;
  role: string;
};

type UseStopGenerationOptions = {
  messages: StoppableMessage[];
  stop: () => void;
  archiveEvents: (messageId: string) => void;
  /**
   * Conversation ID for the messages currently rendered. Stopped message IDs
   * are scoped per conversation so navigating away does not erase the marker
   * shown when the user returns. A newly started stream can temporarily use
   * the ID-less key until `assignConversationId` receives the backend ID.
   */
  conversationId: string | null;
  /**
   * Runs before the client stream is detached. Durable tasks keep running
   * when a client disconnects, so Stop must also cancel them explicitly.
   */
  beforeStop?: () => void | Promise<StopOutcome>;
  /**
   * Called with the server's answer when ``beforeStop`` returns one. Only
   * ``cancelled`` marks the message stopped; until then it shows as stopping,
   * so the UI never claims a cancellation the server did not confirm.
   */
  onStopResolved?: (outcome: StopOutcome) => void;
};

/**
 * ``cancelled``: the server confirmed it. ``finished``: the work had already
 * ended on its own. ``unconfirmed``: no confirmation either way.
 */
export type StopOutcome = 'cancelled' | 'finished' | 'unconfirmed';

export function useStopGeneration({
  messages,
  stop,
  archiveEvents,
  conversationId,
  beforeStop,
  onStopResolved,
}: UseStopGenerationOptions) {
  // Scoped by conversation ID so New Chat / conversation switches do not
  // wipe markers for the conversation the user navigates back to.
  const [stoppedByConversation, setStoppedByConversation] = useState<
    Record<string, Set<string>>
  >(() => ({}));
  const [assignedConversationId, setAssignedConversationId] = useState<
    string | null
  >(null);
  const activeKey = conversationId ?? assignedConversationId ?? NEW_CHAT_KEY;
  const stoppedMessageIds = stoppedByConversation[activeKey] ?? EMPTY_SET;
  const [stoppingByConversation, setStoppingByConversation] = useState<
    Record<string, Set<string>>
  >(() => ({}));
  const stoppingMessageIds = stoppingByConversation[activeKey] ?? EMPTY_SET;

  // Latest key, so a Stop confirmed after the backend names a new chat lands
  // on that chat rather than on the provisional key.
  const activeKeyRef = useRef(activeKey);
  useEffect(() => {
    activeKeyRef.current = activeKey;
  }, [activeKey]);

  const assignConversationId = useCallback((nextConversationId: string) => {
    setAssignedConversationId(nextConversationId);
    const promote = (current: Record<string, Set<string>>) => {
      const pendingIds = current[NEW_CHAT_KEY];
      if (!pendingIds || pendingIds.size === 0) return current;

      const nextIds = new Set(current[nextConversationId] ?? EMPTY_SET);
      for (const messageId of pendingIds) {
        nextIds.add(messageId);
      }

      const next = { ...current, [nextConversationId]: nextIds };
      delete next[NEW_CHAT_KEY];
      return next;
    };
    setStoppedByConversation(promote);
    setStoppingByConversation(promote);
  }, []);

  const clearAssignedConversationId = useCallback(() => {
    setAssignedConversationId(null);
  }, []);

  const stopGeneration = useCallback(() => {
    const latestMessage = messages[messages.length - 1];
    const latestMessageId =
      latestMessage?.role === 'assistant' ? latestMessage.id : undefined;
    const stopKey = activeKey;
    const mark = (
      setter: typeof setStoppedByConversation,
      present: boolean,
    ) => {
      if (!latestMessageId) return;
      const key = stopKey === NEW_CHAT_KEY ? activeKeyRef.current : stopKey;
      setter((current) => {
        const next = new Set(current[key] ?? EMPTY_SET);
        if (present) next.add(latestMessageId);
        else next.delete(latestMessageId);
        return { ...current, [key]: next };
      });
    };
    if (latestMessageId) archiveEvents(latestMessageId);

    const confirmation = beforeStop?.();
    stop();
    if (!confirmation || typeof confirmation.then !== 'function') {
      // Request-bound streams: aborting the request is the cancellation.
      mark(setStoppedByConversation, true);
      return;
    }
    mark(setStoppingByConversation, true);
    void confirmation.then((outcome) => {
      mark(setStoppingByConversation, false);
      if (outcome === 'cancelled') mark(setStoppedByConversation, true);
      onStopResolved?.(outcome);
    });
  }, [activeKey, archiveEvents, beforeStop, messages, onStopResolved, stop]);

  return {
    stoppedMessageIds,
    stoppingMessageIds,
    stopGeneration,
    assignConversationId,
    clearAssignedConversationId,
  };
}

const NEW_CHAT_KEY = '__new__';
const EMPTY_SET: Set<string> = new Set();
