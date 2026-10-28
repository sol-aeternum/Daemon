'use client';

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';

// UI-only positions, never transcript text. One sign-in lifetime at a time.
let positionScope: number | undefined;
const positions = new Map<string, { top: number; follow: boolean }>();

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
  useEffect(() => {
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
  const prependAnchor = useRef<{
    key: string;
    candidates: Array<{ id: string; offset: number }>;
    top: number;
    height: number;
    expectedTop: number;
    follow: boolean;
  } | null>(null);
  const anchorKey = `${authGeneration ?? ''}:${positionKey}`;
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
    prependAnchor.current = null;
    autoScrollEnabledRef.current = true;
    scrollToBottom();
    setScrollState({ conversationId, isScrolledUp: false });
  }, [conversationId, scrollToBottom]);

  const onScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const anchor = prependAnchor.current;
    if (anchor?.key === anchorKey) {
      // Ignore our own restoration/native scroll anchoring, not explicit scroll
      // commands (including a contents-rail reveal) that move to another place.
      const nodes = [
        ...container.querySelectorAll<HTMLElement>('[data-message-id]'),
      ];
      const first = anchor.candidates.find((candidate) =>
        nodes.some((node) => node.dataset.messageId === candidate.id),
      );
      const node =
        first && nodes.find((item) => item.dataset.messageId === first.id);
      if (
        Math.abs(container.scrollTop - anchor.expectedTop) < 1 ||
        (node &&
          first &&
          Math.abs(
            node.getBoundingClientRect().top -
              container.getBoundingClientRect().top -
              first.offset,
          ) < 1)
      ) {
        anchor.expectedTop = container.scrollTop;
        previousScrollTopRef.current = container.scrollTop;
        return;
      }
      prependAnchor.current = null;
      autoScrollEnabledRef.current = anchor.follow;
    }
    const nearBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight <
      64;
    if (nearBottom) {
      autoScrollEnabledRef.current = true;
    } else if (container.scrollTop < previousScrollTopRef.current) {
      autoScrollEnabledRef.current = false;
    }
    previousScrollTopRef.current = container.scrollTop;
    if (authGeneration !== undefined && positionScope === authGeneration)
      positions.set(positionKey, {
        top: container.scrollTop,
        follow: autoScrollEnabledRef.current,
      });
    const isScrolledUp = !nearBottom && !autoScrollEnabledRef.current;
    setScrollState((previous) =>
      previous.conversationId === conversationId &&
      previous.isScrolledUp === isScrolledUp
        ? previous
        : { conversationId, isScrolledUp },
    );
  }, [conversationId, authGeneration, positionKey, anchorKey]);

  const preservePrepend = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const top = container.getBoundingClientRect().top;
    const candidates = [
      ...container.querySelectorAll<HTMLElement>('[data-message-id]'),
    ]
      .filter((node) => node.getBoundingClientRect().bottom > top)
      .map((node) => ({
        id: node.dataset.messageId!,
        offset: node.getBoundingClientRect().top - top,
      }));
    prependAnchor.current = {
      key: anchorKey,
      candidates,
      top: container.scrollTop,
      height: container.scrollHeight,
      expectedTop: container.scrollTop,
      follow: autoScrollEnabledRef.current,
    };
    autoScrollEnabledRef.current = false;
    setScrollState({ conversationId, isScrolledUp: true });
  }, [anchorKey, conversationId]);

  const restorePrepend = useCallback(() => {
    const container = scrollContainerRef.current;
    const anchor = prependAnchor.current;
    if (!container || !anchor) return;
    if (anchor.key !== anchorKey) {
      prependAnchor.current = null;
      return;
    }
    const nodes = [
      ...container.querySelectorAll<HTMLElement>('[data-message-id]'),
    ];
    const candidate = anchor.candidates.find((item) =>
      nodes.some((node) => node.dataset.messageId === item.id),
    );
    const node =
      candidate &&
      nodes.find((item) => item.dataset.messageId === candidate.id);
    if (node && candidate) {
      container.scrollTop +=
        node.getBoundingClientRect().top -
        container.getBoundingClientRect().top -
        candidate.offset;
    } else {
      // Deleted anchor and no surviving successor: preserve the old viewport's
      // distance from the bottom of the prior loaded content, never jump latest.
      container.scrollTop = anchor.top + container.scrollHeight - anchor.height;
    }
    anchor.expectedTop = container.scrollTop;
    previousScrollTopRef.current = container.scrollTop;
  }, [anchorKey]);

  useLayoutEffect(() => {
    restorePrepend();
  }, [messages, restorePrepend]);

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const release = () => {
      const anchor = prependAnchor.current;
      if (!anchor) return;
      prependAnchor.current = null;
      autoScrollEnabledRef.current = anchor.follow;
    };
    container.addEventListener('wheel', release, { passive: true });
    container.addEventListener('touchstart', release, { passive: true });
    container.addEventListener('pointerdown', release, { passive: true });
    container.addEventListener('keydown', release);
    return () => {
      container.removeEventListener('wheel', release);
      container.removeEventListener('touchstart', release);
      container.removeEventListener('pointerdown', release);
      container.removeEventListener('keydown', release);
    };
  }, [anchorKey]);

  useEffect(() => {
    if (previousConversationRef.current !== conversationId) {
      previousConversationRef.current = conversationId;
      autoScrollEnabledRef.current = true;
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
        return;
      }
    }
    if (
      authGeneration !== undefined &&
      messages.length === 0 &&
      positions.has(positionKey)
    )
      return;
    if (!prependAnchor.current && autoScrollEnabledRef.current)
      scrollToBottom();
  }, [
    conversationId,
    messages,
    isLoading,
    scrollToBottom,
    authGeneration,
    positionKey,
  ]);

  const hasMessages = messages.length > 0;
  useEffect(() => {
    const container = scrollContainerRef.current;
    const content = messagesEndRef.current?.parentElement;
    if (!container || !content || typeof ResizeObserver === 'undefined') return;
    // Images, expanded tools, and viewport changes can alter height without
    // changing the message array. Follow only while the reader is at the end.
    const observer = new ResizeObserver(() => {
      if (prependAnchor.current) restorePrepend();
      else if (autoScrollEnabledRef.current) scrollToBottom();
    });
    observer.observe(container);
    observer.observe(content);
    return () => observer.disconnect();
  }, [conversationId, hasMessages, scrollToBottom, restorePrepend]);

  return {
    scrollContainerRef,
    messagesEndRef,
    isScrolledUp,
    onScroll,
    jumpToLatest,
    preservePrepend,
  };
}
