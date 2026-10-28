'use client';

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import {
  findMessage,
  messageElements,
  revealMessage,
} from '@/lib/messageNavigation';

type ReadingAnchor = { id: string; offset: number; expanded: boolean };
type Position = { top: number; follow: boolean; anchor?: ReadingAnchor };

function readingAnchor(container: HTMLElement): ReadingAnchor | undefined {
  const top = container.getBoundingClientRect().top;
  const elements = messageElements(container);
  let low = 0;
  let high = elements.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (elements[middle].getBoundingClientRect().bottom <= top + 24)
      low = middle + 1;
    else high = middle;
  }
  const element = elements[low];
  if (!element?.dataset.messageId) return;
  return {
    id: element.dataset.messageId,
    offset: element.getBoundingClientRect().top - top,
    expanded: !element.querySelector('[data-message-content][hidden]'),
  };
}

// UI-only positions, never transcript text. One sign-in lifetime at a time.
let positionScope: number | undefined;
const positions = new Map<string, Position>();

export function useChatScroll({
  conversationId,
  messages,
  isLoading,
  authGeneration,
}: {
  conversationId: string | null;
  messages: readonly unknown[];
  isLoading: boolean;
  authGeneration?: number;
}) {
  useLayoutEffect(() => {
    if (positionScope !== authGeneration) {
      positions.clear();
      positionScope = authGeneration;
    }
  }, [authGeneration]);
  const positionKey = conversationId ?? '__new__';
  const scrollContainerRef = useRef<HTMLElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const autoScrollEnabledRef = useRef(true);
  const previousConversationRef = useRef(conversationId);
  const previousScrollTopRef = useRef(0);
  const restoredKey = useRef<string | null>(null);
  const anchorRef = useRef<ReadingAnchor | undefined>(undefined);
  const navigationFrame = useRef<number | undefined>(undefined);
  const [scrollState, setScrollState] = useState({
    conversationId,
    isScrolledUp: false,
  });
  const isScrolledUp =
    scrollState.conversationId === conversationId && scrollState.isScrolledUp;

  const scrollToBottom = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    // Immediate scrolling avoids smooth-scroll events disabling following
    // while a streaming response is still changing the content height.
    container.scrollTo({ top: container.scrollHeight, behavior: 'auto' });
    previousScrollTopRef.current = container.scrollTop;
    if (authGeneration !== undefined && positionScope === authGeneration)
      positions.set(positionKey, { top: container.scrollTop, follow: true });
  }, [authGeneration, positionKey]);

  const jumpToLatest = useCallback(() => {
    if (navigationFrame.current !== undefined)
      cancelAnimationFrame(navigationFrame.current);
    navigationFrame.current = undefined;
    anchorRef.current = undefined;
    autoScrollEnabledRef.current = true;
    scrollToBottom();
    setScrollState({ conversationId, isScrolledUp: false });
  }, [conversationId, scrollToBottom]);

  const pauseFollowing = useCallback(() => {
    autoScrollEnabledRef.current = false;
    anchorRef.current = undefined;
    setScrollState({ conversationId, isScrolledUp: true });
  }, [conversationId]);

  const scrollToMessage = useCallback(
    (id: string) => {
      const container = scrollContainerRef.current;
      const element = container && findMessage(container, id);
      if (!container || !element) return;
      // Expansion notifies ResizeObserver. Pause BEFORE revealing, otherwise
      // the observer can return the reader to the streaming end.
      pauseFollowing();
      revealMessage(element);
      if (navigationFrame.current !== undefined)
        cancelAnimationFrame(navigationFrame.current);
      navigationFrame.current = undefined;
      navigationFrame.current = requestAnimationFrame(() => {
        navigationFrame.current = undefined;
        if (!container.contains(element)) return;
        container.scrollTop +=
          element.getBoundingClientRect().top -
          container.getBoundingClientRect().top -
          16;
        previousScrollTopRef.current = container.scrollTop;
        element.focus({ preventScroll: true });
        anchorRef.current = readingAnchor(container);
        if (authGeneration !== undefined && positionScope === authGeneration)
          positions.set(positionKey, {
            top: container.scrollTop,
            follow: false,
            anchor: anchorRef.current,
          });
      });
    },
    [authGeneration, pauseFollowing, positionKey],
  );

  const onScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container || navigationFrame.current !== undefined) return;
    const nearBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight <
      64;
    if (nearBottom) {
      autoScrollEnabledRef.current = true;
    } else if (container.scrollTop < previousScrollTopRef.current) {
      autoScrollEnabledRef.current = false;
    }
    previousScrollTopRef.current = container.scrollTop;
    anchorRef.current = autoScrollEnabledRef.current
      ? undefined
      : readingAnchor(container);
    if (authGeneration !== undefined && positionScope === authGeneration)
      positions.set(positionKey, {
        top: container.scrollTop,
        follow: autoScrollEnabledRef.current,
        anchor: anchorRef.current,
      });
    const isScrolledUp = !nearBottom && !autoScrollEnabledRef.current;
    setScrollState((previous) =>
      previous.conversationId === conversationId &&
      previous.isScrolledUp === isScrolledUp
        ? previous
        : { conversationId, isScrolledUp },
    );
  }, [conversationId, authGeneration, positionKey]);

  useEffect(() => {
    if (previousConversationRef.current !== conversationId) {
      previousConversationRef.current = conversationId;
      autoScrollEnabledRef.current = true;
      anchorRef.current = undefined;
      restoredKey.current = null;
    }
    // Wait for persisted messages before restoring; an empty loading container
    // would clamp scrollTop to zero and destroy the saved reading position.
    if (
      authGeneration !== undefined &&
      restoredKey.current !== positionKey &&
      messages.length > 0
    ) {
      restoredKey.current = positionKey;
      const saved = positions.get(positionKey);
      const container = scrollContainerRef.current;
      if (saved && container && !saved.follow) {
        autoScrollEnabledRef.current = false;
        container.scrollTop = saved.top;
        previousScrollTopRef.current = saved.top;
        anchorRef.current = undefined;
        if (saved.anchor) {
          const anchor = saved.anchor;
          const element = findMessage(container, saved.anchor.id);
          if (element && saved.anchor.expanded) revealMessage(element);
          navigationFrame.current = requestAnimationFrame(() => {
            navigationFrame.current = undefined;
            const target = findMessage(container, anchor.id);
            if (!target) return;
            container.scrollTop +=
              target.getBoundingClientRect().top -
              container.getBoundingClientRect().top -
              anchor.offset;
            previousScrollTopRef.current = container.scrollTop;
            anchorRef.current = anchor;
          });
        }
        setScrollState({ conversationId, isScrolledUp: true });
        return;
      }
    }
    if (messages.length === 0) return;
    if (autoScrollEnabledRef.current) scrollToBottom();
  }, [
    conversationId,
    messages,
    isLoading,
    scrollToBottom,
    authGeneration,
    positionKey,
  ]);

  const hasMessages = messages.length > 0;
  useLayoutEffect(
    () => () => {
      if (navigationFrame.current !== undefined)
        cancelAnimationFrame(navigationFrame.current);
      navigationFrame.current = undefined;
    },
    [conversationId],
  );

  useEffect(() => {
    const container = scrollContainerRef.current;
    const content = messagesEndRef.current?.parentElement;
    if (
      !hasMessages ||
      !container ||
      !content ||
      typeof ResizeObserver === 'undefined'
    )
      return;
    // Images, expanded tools, and viewport changes can alter height without
    // changing the message array. Follow only while the reader is at the end.
    const observer = new ResizeObserver(() => {
      if (autoScrollEnabledRef.current) scrollToBottom();
      else if (anchorRef.current) {
        // Stable-ID anchoring also covers prepended loaded messages and layout
        // changes above the reader. Native browser anchoring may already have
        // compensated, in which case this delta is zero.
        const anchor = anchorRef.current;
        const element = findMessage(container, anchor.id);
        if (element) {
          container.scrollTop +=
            element.getBoundingClientRect().top -
            container.getBoundingClientRect().top -
            anchor.offset;
          previousScrollTopRef.current = container.scrollTop;
          if (authGeneration !== undefined && positionScope === authGeneration)
            positions.set(positionKey, {
              top: container.scrollTop,
              follow: false,
              anchor,
            });
        }
      }
    });
    observer.observe(container);
    observer.observe(content);
    return () => observer.disconnect();
  }, [
    conversationId,
    hasMessages,
    scrollToBottom,
    authGeneration,
    positionKey,
  ]);

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    // Browser find owns its reveal/scroll. Do not let expansion re-enable
    // following before the browser gets to place the matching text.
    container.addEventListener('beforematch', pauseFollowing, true);
    return () =>
      container.removeEventListener('beforematch', pauseFollowing, true);
  }, [pauseFollowing]);

  return {
    scrollContainerRef,
    messagesEndRef,
    isScrolledUp,
    onScroll,
    jumpToLatest,
    scrollToMessage,
  };
}
