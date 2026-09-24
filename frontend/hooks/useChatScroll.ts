'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

export function useChatScroll({
  conversationId,
  messages,
  isLoading,
}: {
  conversationId: string | null;
  messages: readonly unknown[];
  isLoading: boolean;
}) {
  const scrollContainerRef = useRef<HTMLElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const autoScrollEnabledRef = useRef(true);
  const previousConversationRef = useRef(conversationId);
  const previousScrollTopRef = useRef(0);
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
  }, []);

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
    const isScrolledUp = !nearBottom && !autoScrollEnabledRef.current;
    setScrollState((previous) =>
      previous.conversationId === conversationId &&
      previous.isScrolledUp === isScrolledUp
        ? previous
        : { conversationId, isScrolledUp },
    );
  }, [conversationId]);

  useEffect(() => {
    if (previousConversationRef.current !== conversationId) {
      previousConversationRef.current = conversationId;
      autoScrollEnabledRef.current = true;
    }
    if (autoScrollEnabledRef.current) scrollToBottom();
  }, [conversationId, messages, isLoading, scrollToBottom]);

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
