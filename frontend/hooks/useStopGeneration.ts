'use client';

import { useCallback, useState } from 'react';

type StoppableMessage = {
  id?: string;
  role: string;
};

type UseStopGenerationOptions = {
  messages: StoppableMessage[];
  stop: () => void;
  archiveEvents: (messageId: string) => void;
  /**
   * Conversation ID for the messages currently rendered. The set of stopped
   * message IDs is scoped per-conversation so that clearing the in-memory
   * set on `handleNewChat` does not silently erase `(stopped)` markers for
   * the conversation the user navigates back to later. When the
   * conversation ID changes (e.g. switchConversation), the hook drops the
   * markers for the previous conversation but the caller can still pull
   * them out via `stoppedMessageIds` for the active ID.
   */
  conversationId: string | null;
};

export function useStopGeneration({
  messages,
  stop,
  archiveEvents,
  conversationId,
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

  const assignConversationId = useCallback((nextConversationId: string) => {
    setAssignedConversationId(nextConversationId);
    setStoppedByConversation((current) => {
      const pendingStoppedIds = current[NEW_CHAT_KEY];
      if (!pendingStoppedIds || pendingStoppedIds.size === 0) return current;

      const nextStoppedIds = new Set(current[nextConversationId] ?? EMPTY_SET);
      for (const messageId of pendingStoppedIds) {
        nextStoppedIds.add(messageId);
      }

      const next = { ...current, [nextConversationId]: nextStoppedIds };
      delete next[NEW_CHAT_KEY];
      return next;
    });
  }, []);

  const clearAssignedConversationId = useCallback(() => {
    setAssignedConversationId(null);
  }, []);

  const stopGeneration = useCallback(() => {
    const latestMessage = messages[messages.length - 1];
    const latestMessageId = latestMessage?.id;
    if (latestMessage?.role === 'assistant' && latestMessageId) {
      setStoppedByConversation((current) => {
        const previous = current[activeKey] ?? EMPTY_SET;
        const next = new Set(previous);
        next.add(latestMessageId);
        return { ...current, [activeKey]: next };
      });
      archiveEvents(latestMessageId);
    }

    stop();
  }, [activeKey, archiveEvents, messages, stop]);

  return {
    stoppedMessageIds,
    stopGeneration,
    assignConversationId,
    clearAssignedConversationId,
  };
}

const NEW_CHAT_KEY = '__new__';
const EMPTY_SET: Set<string> = new Set();
