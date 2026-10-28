'use client';

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  getAuthHeader,
  refreshIfNeeded,
  getAuthGeneration,
  subscribeAuthGeneration,
} from '@/lib/auth';
import { useAuthGeneration } from './useAuthGeneration';
import {
  MessageHistory,
  type MessageHistorySnapshot,
} from '@/lib/messageHistory';
import {
  ConversationPager,
  type ConversationPaging,
} from '@/lib/conversationPager';
import { HistoryScope, readScopedHistory } from '@/lib/historyScope';
import {
  compareConversationBoundary,
  parseConversationHistoryPage,
  parseMessageHistoryPage,
  type ConversationHistoryRow,
} from '@/lib/historyPages';
import {
  type DaemonMessage,
  normalizeDaemonMessages,
} from '@/lib/chatMessages';

/** Bounded delays before retrying a transient conversation-load failure. */
export const CONVERSATION_RETRY_DELAYS_MS = [1000, 3000, 10000, 30000];

/**
 * HTTP classifications for loading the open conversation (#477). ``403``
 * and ``404`` are answers about this conversation (missing or forbidden):
 * waiting cannot change them, so the retry loop stops with an actionable
 * state. Anything else is transient and is retried, a bounded number of
 * times.
 */
export interface Conversation {
  id: string;
  title: string;
  messages: DaemonMessage[];
  selectedModel?: string;
  createdAt: string;
  updatedAt: string;
  messageCount?: number;
  lastActivityAt?: string | null;
  pinned: boolean;
  title_locked: boolean;
  status: string;
  metadata: Record<string, any>;
  /** The conversation's queued or running durable task, if any. */
  activeTask?: ActiveTask | null;
  /** The conversation's most recent durable task, finished or not. */
  latestTask?: ActiveTask | null;
}

/**
 * ``cancelled``: the task is cancelled. ``cancelling``: cancellation was
 * accepted but the running task has not stopped yet. ``finished``: it had
 * already ended on its own. ``unconfirmed``: no answer either way.
 */
export type TaskCancelOutcome =
  | 'cancelled'
  | 'cancelling'
  | 'finished'
  | 'unconfirmed';

/** A task as found by its idempotency key. */
export type KeyedTask = { id: string; conversationId: string; status: string };

/** Server-owned work still in progress for a conversation (durable tasks). */
export interface ActiveTask {
  id: string;
  status: string;
  content: string;
  cancelRequested: boolean;
}

function toActiveTask(value: unknown): ActiveTask | null {
  if (typeof value !== 'object' || value === null) return null;
  const task = value as Record<string, unknown>;
  if (typeof task.id !== 'string' || typeof task.status !== 'string') {
    return null;
  }
  return {
    id: task.id,
    status: task.status,
    content: typeof task.content === 'string' ? task.content : '',
    cancelRequested: task.cancel_requested === true,
  };
}

interface ApiConversation {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  message_count?: number;
  last_activity_at?: string | null;
  pinned: boolean;
  title_locked: boolean;
  status: string;
  metadata: Record<string, any>;
}

export type ConversationSearchStatus = 'idle' | 'searching' | 'ready' | 'error';

export interface ConversationSearch {
  /** The trimmed query these results answer. */
  query: string;
  status: ConversationSearchStatus;
  /** Server title matches across all conversations, newest activity first. */
  results: Conversation[];
  hasMore: boolean;
  loadMore: () => void;
  retry: () => void;
}

export const SEARCH_PAGE_SIZE = 50;
export const SEARCH_DEBOUNCE_MS = 250;
const MAX_SEARCH_LENGTH = 200;

function toConversation(conv: ApiConversation): Conversation {
  return {
    id: conv.id,
    title: conv.title ?? '',
    messages: [], // Messages are fetched individually
    selectedModel: conv.metadata?.model || 'auto',
    createdAt: conv.created_at,
    updatedAt: conv.updated_at,
    messageCount: conv.message_count,
    lastActivityAt: conv.last_activity_at,
    pinned: conv.pinned,
    title_locked: conv.title_locked,
    status: conv.status,
    metadata: conv.metadata || {},
  };
}

interface SearchState {
  generation: number;
  nextOffset: number;
  query: string;
  status: ConversationSearchStatus;
  results: Conversation[];
  hasMore: boolean;
}

const IDLE_SEARCH: SearchState = {
  generation: -1,
  nextOffset: 0,
  query: '',
  status: 'idle',
  results: [],
  hasMore: false,
};

function fromHistoryRow(row: ConversationHistoryRow): Conversation {
  return toConversation({
    id: row.id,
    title: row.title,
    created_at: row.created_at,
    updated_at: row.updated_at,
    last_activity_at: row.last_activity_at,
    pinned: row.pinned,
    message_count:
      typeof row.message_count === 'number' ? row.message_count : 0,
    title_locked: row.title_locked === true,
    status: typeof row.status === 'string' ? row.status : 'active',
    metadata:
      typeof row.metadata === 'object' &&
      row.metadata !== null &&
      !Array.isArray(row.metadata)
        ? (row.metadata as Record<string, unknown>)
        : {},
  });
}

function listBoundary(row: Conversation): ConversationHistoryRow {
  return {
    id: row.id,
    user_id: '',
    title: row.title,
    created_at: row.createdAt,
    updated_at: row.updatedAt,
    last_activity_at: row.lastActivityAt ?? null,
    pinned: row.pinned,
  };
}

const EMPTY_LIST = {
  generation: -1,
  rows: [] as Conversation[],
  loaded: false,
  total: 0,
  hasMore: false,
  loading: false,
  refreshing: false,
  updatesPending: false,
  error: false,
};
const emptyList = () => EMPTY_LIST;

const EMPTY_MESSAGE_HISTORY: MessageHistorySnapshot<Conversation> = {
  generation: -1,
  id: null,
  scope: '',
  tail: null,
  messages: [],
  hasOlder: false,
  loadingOlder: false,
  syncing: false,
  error: false,
  failure: null,
  retryKind: null,
  incomplete: false,
};
const emptyMessageHistory = () => EMPTY_MESSAGE_HISTORY;

export function useConversationHistory() {
  const authGeneration = useAuthGeneration();
  const [searchQuery, setSearchQueryState] = useState('');
  const [search, setSearch] = useState<SearchState>(IDLE_SEARCH);
  // Only the latest search request may publish results.
  const searchRequest = useRef(0);
  const searchAbort = useRef<AbortController | null>(null);
  const setSearchQuery = useCallback((query: string) => {
    searchRequest.current += 1;
    searchAbort.current?.abort();
    setSearchQueryState(query);
  }, []);
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentId = searchParams.get('id');

  const apiBaseUrl =
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '');

  const getAuthHeaders = useCallback(async (): Promise<
    Record<string, string>
  > => {
    const header = getAuthHeader();
    if (header) return { Authorization: header };
    const token = await refreshIfNeeded();
    if (token) return { Authorization: `Bearer ${token}` };
    throw new Error('Authentication or browser cache readiness unavailable');
  }, []);

  const apiCandidates = useCallback(
    (path: string) => {
      const normalizedPath = path.startsWith('/') ? path : `/${path}`;
      const trimmedBase = apiBaseUrl.endsWith('/')
        ? apiBaseUrl.slice(0, -1)
        : apiBaseUrl;

      if (!trimmedBase) {
        return [normalizedPath];
      }

      return [`${trimmedBase}${normalizedPath}`, normalizedPath];
    },
    [apiBaseUrl],
  );

  const apiFetch = useCallback(
    async (path: string, init: RequestInit = {}, timeoutMs = 12000) => {
      const generation = getAuthGeneration();
      const assertCurrent = () => {
        if (generation !== getAuthGeneration() || init.signal?.aborted) {
          throw new DOMException('Request scope changed', 'AbortError');
        }
      };
      const candidates = apiCandidates(path);
      let lastError: unknown = null;

      for (let index = 0; index < candidates.length; index += 1) {
        assertCurrent();
        const candidate = candidates[index];
        const controller = new AbortController();
        const abort = () => controller.abort(init.signal?.reason);
        init.signal?.addEventListener('abort', abort, { once: true });
        const timeoutId = setTimeout(() => {
          try {
            controller.abort(
              new DOMException('Request timed out', 'AbortError'),
            );
          } catch {
            controller.abort();
          }
        }, timeoutMs);

        try {
          assertCurrent();
          const response = await fetch(candidate, {
            ...init,
            signal: controller.signal,
          });
          clearTimeout(timeoutId);
          assertCurrent();

          if (response.status === 404 && index < candidates.length - 1) {
            continue;
          }

          return response;
        } catch (error) {
          clearTimeout(timeoutId);
          assertCurrent();
          lastError = error;
          if (index === candidates.length - 1) {
            throw error;
          }
        } finally {
          init.signal?.removeEventListener('abort', abort);
        }
      }

      if (lastError instanceof Error) {
        throw lastError;
      }
      throw new Error('Request failed');
    },
    [apiCandidates],
  );

  const [pager] = useState(
    () =>
      new ConversationPager<Conversation>({
        generation: getAuthGeneration,
        compare: (a, b) =>
          compareConversationBoundary(listBoundary(a), listBoundary(b)),
        readPage: async (cursor, lease) => {
          const params = new URLSearchParams({
            pagination: 'cursor',
            limit: '100',
          });
          if (cursor !== null) params.set('cursor', cursor);
          const result = await readScopedHistory(
            lease,
            apiCandidates(`/conversations?${params}`),
            { getAuthHeaders },
          );
          lease.assertCurrent();
          if (result.status !== 200)
            throw new Error('Conversation page unavailable');
          const parsed = parseConversationHistoryPage(result.data, cursor);
          return {
            rows: parsed.conversations.map(fromHistoryRow),
            total: parsed.total,
            page: parsed.page,
          };
        },
        readOne: async (id, lease) => {
          const result = await readScopedHistory(
            lease,
            apiCandidates(`/conversations/${id}?limit=1`),
            { getAuthHeaders },
          );
          lease.assertCurrent();
          if (result.status !== 200) return { status: result.status };
          const parsed = parseConversationHistoryPage({
            conversations: [result.data],
            total: 1,
            page: {
              has_more: false,
              next_cursor: null,
              start_cursor: 'metadata',
              end_cursor: 'metadata',
            },
          });
          if (parsed.conversations[0].id !== id)
            throw new Error('Conversation identity mismatch');
          return { status: 200, row: fromHistoryRow(parsed.conversations[0]) };
        },
      }),
  );
  const listSnapshot = useSyncExternalStore(
    pager.subscribe,
    pager.getSnapshot,
    emptyList,
  );
  const list =
    listSnapshot.generation === authGeneration ? listSnapshot : EMPTY_LIST;
  const conversations = list.rows;
  const isLoaded = list.loaded;
  const setConversations = useCallback(
    (update: (rows: Conversation[]) => Conversation[]) => pager.update(update),
    [pager],
  );

  useEffect(() => {
    const owner = pager;
    owner.resume();
    let observedGeneration = getAuthGeneration();
    const reconcile = () => {
      if (observedGeneration !== getAuthGeneration()) {
        observedGeneration = getAuthGeneration();
        searchRequest.current += 1;
        searchAbort.current?.abort();
        setSearch(IDLE_SEARCH);
        setSearchQueryState('');
      }
      owner.syncGeneration();
      if (!owner.getSnapshot().loaded || owner.getSnapshot().error)
        owner.refresh();
    };
    const unsubscribe = subscribeAuthGeneration(reconcile);
    reconcile();
    const interval = setInterval(owner.refresh, 30000);
    return () => {
      clearInterval(interval);
      unsubscribe();
      searchAbort.current?.abort();
      owner.dispose();
    };
  }, [pager]);

  const fetchConversations = useCallback(() => {
    pager?.refresh();
  }, [pager]);
  const conversationPaging: ConversationPaging = {
    hasMore: list.hasMore,
    loading: list.loading,
    refreshing: list.refreshing,
    updatesPending: list.updatesPending,
    error: list.error,
    loadMore: pager?.loadMore ?? (() => {}),
    retry: pager?.retry ?? (() => {}),
  };

  const runSearch = useCallback(
    async (query: string, offset: number) => {
      const generation = getAuthGeneration();
      const request = ++searchRequest.current;
      searchAbort.current?.abort();
      const controller = new AbortController();
      searchAbort.current = controller;
      const scope = new HistoryScope(getAuthGeneration, null);
      const lease = scope.capture({
        selected: false,
        signal: controller.signal,
      });
      const current = () =>
        !controller.signal.aborted &&
        generation === getAuthGeneration() &&
        request === searchRequest.current;
      setSearch((prev) =>
        !current()
          ? prev
          : offset === 0
            ? {
                generation,
                nextOffset: 0,
                query,
                status: 'searching',
                results: [],
                hasMore: false,
              }
            : { ...prev, status: 'searching' },
      );
      try {
        const params = new URLSearchParams({
          search: query,
          limit: String(SEARCH_PAGE_SIZE),
          offset: String(offset),
        });
        const result = await readScopedHistory(
          lease,
          apiCandidates(`/conversations?${params}`),
          { getAuthHeaders },
        );
        if (!current()) return;
        if (result.status !== 200)
          throw new Error(`Search failed: ${result.status}`);
        const data = result.data as { conversations?: ApiConversation[] };
        const page: Conversation[] = (
          (data.conversations || []) as ApiConversation[]
        ).map(toConversation);
        setSearch((prev) => {
          if (!current()) return prev;
          const seen = new Set(
            offset === 0 ? [] : prev.results.map((c) => c.id),
          );
          const merged = offset === 0 ? [] : [...prev.results];
          for (const conv of page) {
            if (!seen.has(conv.id)) {
              merged.push(conv);
              seen.add(conv.id);
            }
          }
          return {
            query,
            generation,
            nextOffset: offset + page.length,
            status: 'ready',
            results: merged,
            hasMore: page.length === SEARCH_PAGE_SIZE,
          };
        });
      } catch {
        if (!current()) return;
        setSearch((prev) =>
          current() ? { ...prev, generation, query, status: 'error' } : prev,
        );
      } finally {
        lease.release();
        scope.dispose();
        if (searchAbort.current === controller) searchAbort.current = null;
      }
    },
    [apiCandidates, getAuthHeaders],
  );

  const normalizedSearchQuery = searchQuery.trim().slice(0, MAX_SEARCH_LENGTH);
  const [observedSearchQuery, setObservedSearchQuery] = useState(
    normalizedSearchQuery,
  );
  if (observedSearchQuery !== normalizedSearchQuery) {
    setObservedSearchQuery(normalizedSearchQuery);
    if (!normalizedSearchQuery) setSearch(IDLE_SEARCH);
  }

  useEffect(() => {
    const query = normalizedSearchQuery;
    if (!query) {
      searchRequest.current += 1;
      return;
    }
    const timer = setTimeout(
      () => void runSearch(query, 0),
      SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [searchQuery, normalizedSearchQuery, runSearch]);

  const loadMoreSearch = useCallback(() => {
    if (search.status !== 'ready' || !search.hasMore) return;
    if (search.generation !== getAuthGeneration()) return;
    void runSearch(search.query, search.nextOffset);
  }, [runSearch, search]);

  const retrySearch = useCallback(() => {
    if (search.query) void runSearch(search.query, 0);
  }, [runSearch, search.query]);

  const conversationSearch: ConversationSearch = {
    ...(search.generation === authGeneration ? search : IDLE_SEARCH),
    loadMore: loadMoreSearch,
    retry: retrySearch,
  };

  const createConversation = useCallback(async () => {
    const generation = getAuthGeneration();
    try {
      const headers = await getAuthHeaders();
      if (generation !== getAuthGeneration()) return null;
      const response = await apiFetch('/conversations', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...headers,
        },
        body: JSON.stringify({ title: 'New conversation' }),
      });

      if (generation !== getAuthGeneration() || !response.ok) return null;

      const newConv: ApiConversation = await response.json();
      if (generation !== getAuthGeneration()) return null;
      const formattedConv: Conversation = {
        id: newConv.id,
        title: newConv.title ?? '',
        messages: [],
        selectedModel: 'auto',
        createdAt: newConv.created_at,
        updatedAt: newConv.updated_at,
        messageCount: newConv.message_count,
        lastActivityAt: newConv.last_activity_at,
        pinned: newConv.pinned,
        title_locked: newConv.title_locked,
        status: newConv.status,
        metadata: newConv.metadata || {},
      };

      setConversations((prev) => [formattedConv, ...prev]);
      router.push(`/?id=${newConv.id}`);
      return newConv.id;
    } catch {
      return null;
    }
  }, [apiFetch, getAuthHeaders, router, setConversations]);

  const updateConversation = useCallback(
    async (
      id: string,
      updates: Partial<Conversation> & { messages?: DaemonMessage[] },
    ) => {
      const generation = getAuthGeneration();
      // Optimistic update
      setConversations((prev) =>
        prev.map((conv) => (conv.id === id ? { ...conv, ...updates } : conv)),
      );
      setSearch((prev) => ({
        ...prev,
        results: prev.results.map((conv) =>
          generation === getAuthGeneration() && conv.id === id
            ? { ...conv, ...updates }
            : conv,
        ),
      }));

      try {
        const payload: any = {};
        if (updates.title !== undefined) payload.title = updates.title;
        if (updates.pinned !== undefined) payload.pinned = updates.pinned;
        if (updates.title_locked !== undefined)
          payload.title_locked = updates.title_locked;
        if (updates.selectedModel !== undefined) {
          // Update metadata for model selection
          const currentConv = conversations.find((c) => c.id === id);
          payload.metadata = {
            ...(currentConv?.metadata || {}),
            model: updates.selectedModel,
          };
        }

        if (Object.keys(payload).length > 0) {
          const headers = await getAuthHeaders();
          if (generation !== getAuthGeneration()) return;
          await apiFetch(`/conversations/${id}`, {
            method: 'PATCH',
            headers: {
              'Content-Type': 'application/json',
              ...headers,
            },
            body: JSON.stringify(payload),
          });
        }
      } catch {
        if (generation === getAuthGeneration()) fetchConversations(); // Revert on error
      }
    },
    [
      apiFetch,
      conversations,
      fetchConversations,
      getAuthHeaders,
      setConversations,
    ],
  );

  const setConversationModel = useCallback(
    (id: string, model: string) => {
      updateConversation(id, { selectedModel: model });
    },
    [updateConversation],
  );

  const deleteConversation = useCallback(
    async (id: string) => {
      const generation = getAuthGeneration();
      // Optimistic update
      setConversations((prev) => prev.filter((conv) => conv.id !== id));
      setSearch((prev) => ({
        ...prev,
        results:
          generation === getAuthGeneration()
            ? prev.results.filter((conv) => conv.id !== id)
            : prev.results,
      }));
      if (currentId === id) {
        router.push('/');
      }

      try {
        const headers = await getAuthHeaders();
        if (generation !== getAuthGeneration()) return false;
        const response = await apiFetch(`/conversations/${id}`, {
          method: 'DELETE',
          headers,
        });

        if (generation !== getAuthGeneration()) return false;
        if (!response.ok) {
          fetchConversations();
          return false;
        }

        return true;
      } catch {
        if (generation === getAuthGeneration()) fetchConversations(); // Revert on error
        return false;
      }
    },
    [
      apiFetch,
      currentId,
      router,
      fetchConversations,
      getAuthHeaders,
      setConversations,
    ],
  );

  const fetchConversationById = useCallback(
    async (id: string): Promise<Conversation | null> => {
      const scope = new HistoryScope(getAuthGeneration, null);
      const lease = scope.capture({ selected: false });
      try {
        const result = await readScopedHistory(
          lease,
          apiCandidates(`/conversations/${id}`),
          { getAuthHeaders },
        );
        lease.assertCurrent();
        if (result.status !== 200) {
          return null;
        }

        const data = result.data as Record<string, any>;
        if (data.id !== id) return null;
        const formattedConv: Conversation = {
          id: data.id,
          title: data.title,
          messages: normalizeDaemonMessages(data.messages),
          selectedModel: data.metadata?.model || 'auto',
          createdAt: data.created_at,
          updatedAt: data.updated_at,
          messageCount: data.message_count,
          lastActivityAt: data.last_activity_at,
          pinned: data.pinned,
          title_locked: data.title_locked,
          status: data.status,
          metadata: data.metadata || {},
          activeTask: toActiveTask(data.active_task),
          latestTask: toActiveTask(data.latest_task),
        };

        lease.assertCurrent();
        return formattedConv;
      } catch {
        return null;
      } finally {
        lease.release();
        scope.dispose();
      }
    },
    [apiCandidates, getAuthHeaders],
  );

  const [messageOwner] = useState(
    () =>
      new MessageHistory<Conversation>({
        generation: getAuthGeneration,
        retryDelays: CONVERSATION_RETRY_DELAYS_MS,
        read: async (id, bounds, lease) => {
          const params = new URLSearchParams({
            pagination: 'cursor',
            limit: '100',
          });
          if (bounds.before) params.set('before', bounds.before);
          if (bounds.through) params.set('through', bounds.through);
          const result = await readScopedHistory(
            lease,
            apiCandidates(`/conversations/${id}?${params}`),
            { getAuthHeaders },
          );
          lease.assertCurrent();
          if (result.status !== 200) return { status: result.status };
          const parsed = parseMessageHistoryPage(
            result.data,
            id,
            bounds.before,
          );
          const metadata = parseConversationHistoryPage({
            conversations: [parsed.conversation],
            total: 1,
            page: {
              has_more: false,
              next_cursor: null,
              start_cursor: 'metadata',
              end_cursor: 'metadata',
            },
          });
          const conversation: Conversation = {
            ...fromHistoryRow(metadata.conversations[0]),
            messages: parsed.messages,
            activeTask: toActiveTask(parsed.conversation.active_task),
            latestTask: toActiveTask(parsed.conversation.latest_task),
          };
          return {
            status: 200,
            value: {
              conversation,
              messages: parsed.messages,
              page: parsed.page,
            },
          };
        },
      }),
  );
  const selectedSnapshot = useSyncExternalStore(
    messageOwner.subscribe,
    messageOwner.getSnapshot,
    emptyMessageHistory,
  );
  const messageHistory =
    selectedSnapshot.generation === authGeneration &&
    selectedSnapshot.id === currentId
      ? selectedSnapshot
      : EMPTY_MESSAGE_HISTORY;
  const currentIdRef = useRef(currentId);
  useLayoutEffect(() => {
    currentIdRef.current = currentId;
    messageOwner.select(currentId);
  }, [messageOwner, currentId, authGeneration]);
  useEffect(() => {
    messageOwner.resume();
    const reconcile = () => {
      messageOwner.select(currentIdRef.current);
      messageOwner.startInitial();
    };
    const unsubscribe = subscribeAuthGeneration(reconcile);
    reconcile();
    const interval = setInterval(() => {
      if (
        messageOwner.getSnapshot().failure === null &&
        messageOwner.getSnapshot().tail !== null
      )
        void messageOwner.refreshRange();
    }, 30000);
    return () => {
      unsubscribe();
      clearInterval(interval);
      messageOwner.dispose();
    };
  }, [messageOwner]);
  useEffect(() => {
    messageOwner.startInitial();
  }, [messageOwner, currentId, authGeneration]);
  const getCurrentConversation = useCallback(() => {
    const tail = messageHistory.tail;
    return tail && messageOwner.canUse(tail) ? tail : null;
  }, [messageHistory.tail, messageOwner]);
  const conversationLoadFailure = messageHistory.failure;
  const refreshCurrentConversation = useCallback(
    () => messageOwner.refresh(),
    [messageOwner],
  );

  /**
   * The caller's task created with ``key``: ``null`` when the server has
   * none (not accepted, or not yet), ``undefined`` when the answer is
   * unknown (network error, older backend).
   */
  const taskForKey = useCallback(
    async (key: string): Promise<KeyedTask | null | undefined> => {
      const lease = messageOwner.captureOperation();
      try {
        const result = await readScopedHistory(
          lease,
          apiCandidates(`/tasks/by-key/${encodeURIComponent(key)}`),
          { getAuthHeaders },
        );
        lease.assertCurrent();
        if (result.status === 404) return null;
        if (result.status !== 200) return undefined;
        const task = result.data as Record<string, unknown>;
        return typeof task.id === 'string' &&
          typeof task.conversation_id === 'string' &&
          typeof task.status === 'string'
          ? {
              id: task.id,
              conversationId: task.conversation_id,
              status: task.status,
            }
          : undefined;
      } catch {
        return undefined;
      } finally {
        lease.release();
      }
    },
    [apiCandidates, getAuthHeaders, messageOwner],
  );

  /** A task's current status, or ``undefined`` when it cannot be read. */
  const taskStatus = useCallback(
    async (taskId: string): Promise<string | undefined> => {
      const lease = messageOwner.captureOperation();
      try {
        const result = await readScopedHistory(
          lease,
          apiCandidates(`/tasks/${encodeURIComponent(taskId)}`),
          { getAuthHeaders },
        );
        lease.assertCurrent();
        if (result.status !== 200) return undefined;
        const task = result.data as { status?: unknown };
        return typeof task.status === 'string' ? task.status : undefined;
      } catch {
        return undefined;
      } finally {
        lease.release();
      }
    },
    [apiCandidates, getAuthHeaders, messageOwner],
  );

  /**
   * Ask the server to cancel a durable task (Stop, not a disconnect).
   * ``cancelled``: the server accepted the cancellation. ``finished``: the
   * task had already ended on its own. ``unconfirmed``: no answer either way.
   */
  const cancelTask = useCallback(
    async (taskId: string): Promise<TaskCancelOutcome> => {
      const lease = messageOwner.captureOperation();
      try {
        const headers = await getAuthHeaders();
        lease.assertCurrent();
        const response = await apiFetch(
          `/tasks/${encodeURIComponent(taskId)}/cancel`,
          { method: 'POST', headers, signal: lease.signal },
        );
        lease.assertCurrent();
        if (response.status === 409) return 'finished';
        if (!response.ok) return 'unconfirmed';
        // 200 with status "running": cancellation is requested, but the task
        // has not stopped yet.
        const body = (await response.json().catch(() => ({}))) as {
          status?: unknown;
        };
        lease.assertCurrent();
        return body.status === 'cancelled' ? 'cancelled' : 'cancelling';
      } catch {
        return 'unconfirmed';
      } finally {
        lease.release();
      }
    },
    [apiFetch, getAuthHeaders, messageOwner],
  );

  const switchConversation = useCallback(
    (id: string) => {
      router.push(`/?id=${id}`);
    },
    [router],
  );

  return {
    conversations,
    currentId,
    isLoaded,
    createConversation,
    updateConversation,
    setConversationModel,
    deleteConversation,
    getCurrentConversation,
    refreshCurrentConversation,
    cancelTask,
    taskForKey,
    taskStatus,
    switchConversation,
    fetchConversationById,
    conversationLoadFailure,
    searchQuery,
    setSearchQuery,
    conversationSearch,
    conversationPaging,
    messageHistory,
    loadOlderMessages: messageOwner.loadOlder,
    refreshMessageHistory: messageOwner.refreshRange,
    isCurrentConversation: messageOwner.canUse,
    isHistoryScopeCurrent: messageOwner.isScopeCurrent,
    refreshConversations: fetchConversations,
  };
}
