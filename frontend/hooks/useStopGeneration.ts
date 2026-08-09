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
};

export function useStopGeneration({
  messages,
  stop,
  archiveEvents,
}: UseStopGenerationOptions) {
  const [stoppedMessageIds, setStoppedMessageIds] = useState<Set<string>>(
    () => new Set(),
  );

  const stopGeneration = useCallback(() => {
    const latestMessage = messages[messages.length - 1];
    const latestMessageId = latestMessage?.id;
    if (latestMessage?.role === 'assistant' && latestMessageId) {
      setStoppedMessageIds((current) => {
        const next = new Set(current);
        next.add(latestMessageId);
        return next;
      });
      archiveEvents(latestMessageId);
    }

    stop();
  }, [archiveEvents, messages, stop]);

  const resetStoppedMessages = useCallback(
    () => setStoppedMessageIds(new Set()),
    [],
  );

  return { stoppedMessageIds, stopGeneration, resetStoppedMessages };
}
