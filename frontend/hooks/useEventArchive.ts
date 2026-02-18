"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { ChatEvent, isChatEvent } from "../lib/events";

export type { ChatEvent };

interface ArchivedEventData {
  events: ChatEvent[];
  duration: number;
  requestId: string | null;
}

/**
 * useEventArchive - Manages event archival for chat messages
 * 
 * ## Invariants
 * 
 * 1. **request_id tracking**: The `request_id` field in events identifies which events belong to 
 *    the current request. This is used to filter events when archiving them.
 * 
 * 2. **Event key deduplication**: When there's no `request_id` (e.g., first message), we use 
 *    `lastArchivedEventKeysRef` to deduplicate events. This prevents the same event from being 
 *    included in both the current stream and archived messages.
 * 
 * 3. **Lifecycle**:
 *    - Events arrive via `data` from useChat
 *    - Tracked in `eventsRef` 
 *    - On `onFinish`, filtered and stored in `archivedEvents` keyed by message ID
 *    - Subsequent renders read from archive
 * 
 * 4. **Multi-message handling**: When `data` accumulates across multiple messages in one session,
 *    the event-key deduplication ensures events aren't duplicated across message boundaries.
 */

interface UseEventArchiveOptions {
  data: unknown[];
  isLoading: boolean;
}

interface UseEventArchiveReturn {
  getEventsForMessage: (messageId: string, isLast: boolean) => ChatEvent[];
  getDurationForMessage: (messageId: string) => number;
  archiveCurrentEvents: (messageId: string) => void;
  resetArchive: () => void;
  events: ChatEvent[];
  eventsRef: React.MutableRefObject<ChatEvent[]>;
  thinkingDurationRef: React.MutableRefObject<number>;
}

function eventKey(event: ChatEvent): string {
  if (event.id) return `id:${event.id}`;
  return `json:${JSON.stringify(event)}`;
}

export function useEventArchive({ data, isLoading }: UseEventArchiveOptions): UseEventArchiveReturn {
  const [archivedEvents, setArchivedEvents] = useState<Record<string, ArchivedEventData>>({});
  
  const eventsRef = useRef<ChatEvent[]>([]);
  const thinkingDurationRef = useRef(0);
  const lastArchivedEventKeysRef = useRef<Set<string>>(new Set());
  const currentRequestIdRef = useRef<string | null>(null);
  
  // Extract and filter events from data
  const events: ChatEvent[] = Array.isArray(data)
    ? (data.filter((x): x is ChatEvent => isChatEvent(x)) as ChatEvent[])
    : [];

  // Sync events to ref and track request_id
  useEffect(() => {
    eventsRef.current = events;
    
    // Find latest request_id
    let latestRequestId: string | null = null;
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const requestId = events[i].request_id;
      if (requestId) {
        latestRequestId = requestId;
        break;
      }
    }
    
    // Only update if we have a request_id or no events
    if (latestRequestId || events.length === 0) {
      currentRequestIdRef.current = latestRequestId;
    }
  }, [events]);

  // Track isLoading transitions for deduplication
  const prevLoadingRef = useRef(isLoading);
  useEffect(() => {
    if (prevLoadingRef.current && !isLoading) {
      // Transitioned from loading to not loading
      const keys = new Set<string>();
      eventsRef.current.forEach((event) => {
        keys.add(eventKey(event));
      });
      lastArchivedEventKeysRef.current = keys;
    }
    prevLoadingRef.current = isLoading;
  }, [isLoading]);

  const archiveCurrentEvents = useCallback((messageId: string) => {
    const requestId = currentRequestIdRef.current;
    const eventsToArchive = requestId
      ? eventsRef.current.filter((event) => event.request_id === requestId)
      : eventsRef.current.filter((event) => !lastArchivedEventKeysRef.current.has(eventKey(event)));
    
    setArchivedEvents((prev) => ({
      ...prev,
      [messageId]: {
        events: eventsToArchive,
        duration: thinkingDurationRef.current,
        requestId,
      },
    }));
    
    // Reset for next message
    thinkingDurationRef.current = 0;
  }, []);

  const resetArchive = useCallback(() => {
    setArchivedEvents({});
    thinkingDurationRef.current = 0;
    eventsRef.current = [];
    lastArchivedEventKeysRef.current = new Set();
    currentRequestIdRef.current = null;
  }, []);

  const getEventsForMessage = useCallback(
    (messageId: string, isLast: boolean): ChatEvent[] => {
      if (archivedEvents[messageId]) {
        return archivedEvents[messageId].events;
      }
      
      if (isLast) {
        const requestId = currentRequestIdRef.current;
        if (requestId) {
          return events.filter((event) => event.request_id === requestId);
        }
        const lastArchivedKeys = lastArchivedEventKeysRef.current;
        return events.filter((event) => !lastArchivedKeys.has(eventKey(event)));
      }
      
      return [];
    },
    [archivedEvents, events]
  );

  const getDurationForMessage = useCallback(
    (messageId: string): number => {
      return archivedEvents[messageId]?.duration || 0;
    },
    [archivedEvents]
  );

  return {
    getEventsForMessage,
    getDurationForMessage,
    archiveCurrentEvents,
    resetArchive,
    events,
    eventsRef,
    thinkingDurationRef,
  };
}
