'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

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
    autoScrollEnabledRef.current = true;
    scrollToBottom();
    setScrollState({ conversationId, isScrolledUp: false });
  }, [conversationId, scrollToBottom]);

  const onScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
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
  }, [conversationId, authGeneration, positionKey]);

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
  useEffect(() => {
    const container = scrollContainerRef.current;
    const content = messagesEndRef.current?.parentElement;
    if (!container || !content || typeof ResizeObserver === 'undefined') return;
    // Images, expanded tools, and viewport changes can alter height without
    // changing the message array. Follow only while the reader is at the end.
    const observer = new ResizeObserver(() => {
      if (autoScrollEnabledRef.current) scrollToBottom();
    });
    observer.observe(container);
    observer.observe(content);
    return () => observer.disconnect();
  }, [conversationId, hasMessages, scrollToBottom]);

  return {
    scrollContainerRef,
    messagesEndRef,
    isScrolledUp,
    onScroll,
    jumpToLatest,
  };
}
