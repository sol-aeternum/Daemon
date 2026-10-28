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
  const anchorRef = useRef<ReadingAnchor | undefined>(undefined);
  const navigationFrame = useRef<number | undefined>(undefined);
  const operation = useRef(0);
  const scopeVersion = useRef(0);
  const cancelPlacement = useCallback(() => {
    operation.current++;
    if (navigationFrame.current !== undefined)
      cancelAnimationFrame(navigationFrame.current);
    navigationFrame.current = undefined;
    prependAnchor.current = null;
    anchorRef.current = undefined;
  }, []);
  // Invalidate on committed scope transitions, including A-B-A and same-ID
  // account changes. Speculative renders must not revoke the committed view.
  useLayoutEffect(() => {
    const scope = ++scopeVersion.current;
    autoScrollEnabledRef.current = true;
    restoredKey.current = null;
    previousScrollTopRef.current = 0;
    return () => {
      scopeVersion.current = scope + 1;
      cancelPlacement();
    };
  }, [conversationId, authGeneration, cancelPlacement]);

  const schedulePlacement = useCallback(
    (container: HTMLElement, place: () => void) => {
      const token = ++operation.current;
      navigationFrame.current = requestAnimationFrame(() => {
        if (
          token !== operation.current ||
          scrollContainerRef.current !== container
        )
          return;
        navigationFrame.current = undefined;
        place();
      });
    },
    [],
  );
  const [scrollState, setScrollState] = useState({
    conversationId,
    authGeneration,
    isScrolledUp: false,
  });
  const isScrolledUp =
    scrollState.conversationId === conversationId &&
    scrollState.authGeneration === authGeneration &&
    scrollState.isScrolledUp;

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
    cancelPlacement();
    restoredKey.current = positionKey;
    autoScrollEnabledRef.current = true;
    scrollToBottom();
    setScrollState({ conversationId, authGeneration, isScrolledUp: false });
  }, [
    conversationId,
    authGeneration,
    scrollToBottom,
    cancelPlacement,
    positionKey,
  ]);

  const pauseFollowing = useCallback(() => {
    cancelPlacement();
    restoredKey.current = positionKey;
    autoScrollEnabledRef.current = false;
    setScrollState({ conversationId, authGeneration, isScrolledUp: true });
  }, [conversationId, authGeneration, cancelPlacement, positionKey]);

  const scrollToMessage = useCallback(
    (id: string) => {
      const container = scrollContainerRef.current;
      const element = container && findMessage(container, id);
      if (!container || !element) return;
      // Expansion notifies ResizeObserver. Pause BEFORE revealing, otherwise
      // the observer can return the reader to the streaming end.
      pauseFollowing();
      revealMessage(element);
      schedulePlacement(container, () => {
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
    [authGeneration, pauseFollowing, positionKey, schedulePlacement],
  );

  const onScroll = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container || navigationFrame.current !== undefined) return;
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
      previous.authGeneration === authGeneration &&
      previous.isScrolledUp === isScrolledUp
        ? previous
        : { conversationId, authGeneration, isScrolledUp },
    );
  }, [conversationId, authGeneration, positionKey, anchorKey]);

  const preservePrepend = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container || navigationFrame.current !== undefined) return;
    anchorRef.current = undefined;
    restoredKey.current = positionKey;
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
    setScrollState({ conversationId, authGeneration, isScrolledUp: true });
  }, [anchorKey, conversationId, authGeneration, positionKey]);

  const restorePrepend = useCallback(() => {
    const container = scrollContainerRef.current;
    const anchor = prependAnchor.current;
    if (!container || !anchor || navigationFrame.current !== undefined) return;
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
      cancelPlacement();
      restoredKey.current = positionKey;
      if (anchor) autoScrollEnabledRef.current = anchor.follow;
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
  }, [anchorKey, cancelPlacement, positionKey]);

  useEffect(() => {
    if (navigationFrame.current !== undefined || prependAnchor.current) return;
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
        cancelPlacement();
        autoScrollEnabledRef.current = false;
        container.scrollTop = saved.top;
        previousScrollTopRef.current = saved.top;
        anchorRef.current = undefined;
        if (saved.anchor) {
          const anchor = saved.anchor;
          const element = findMessage(container, saved.anchor.id);
          if (element && saved.anchor.expanded) revealMessage(element);
          schedulePlacement(container, () => {
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
        setScrollState({ conversationId, authGeneration, isScrolledUp: true });
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
    cancelPlacement,
    schedulePlacement,
  ]);

  const hasMessages = messages.length > 0;

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
    const scope = scopeVersion.current;
    let active = true;
    const observer = new ResizeObserver(() => {
      if (
        !active ||
        scope !== scopeVersion.current ||
        navigationFrame.current !== undefined
      )
        return;
      if (prependAnchor.current) restorePrepend();
      else if (autoScrollEnabledRef.current) scrollToBottom();
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
    return () => {
      active = false;
      observer.disconnect();
    };
  }, [
    conversationId,
    hasMessages,
    scrollToBottom,
    authGeneration,
    positionKey,
    restorePrepend,
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
    preservePrepend,
    scrollToMessage,
  };
}
