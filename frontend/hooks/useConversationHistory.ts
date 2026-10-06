'use client';

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { getAuthHeader, refreshIfNeeded } from '@/lib/auth';
import {
  type DaemonMessage,
  normalizeDaemonMessages,
} from '@/lib/chatMessages';

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

export type TaskCancelOutcome = 'cancelled' | 'finished' | 'unconfirmed';

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
  title: string;
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
    title: conv.title,
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
  query: string;
  status: ConversationSearchStatus;
  results: Conversation[];
  hasMore: boolean;
}

const IDLE_SEARCH: SearchState = {
  query: '',
  status: 'idle',
  results: [],
  hasMore: false,
};

export function useConversationHistory() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [isLoaded, setIsLoaded] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [search, setSearch] = useState<SearchState>(IDLE_SEARCH);
  // Only the latest search request may publish results.
  const searchRequest = useRef(0);
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
    return {};
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
      const candidates = apiCandidates(path);
      let lastError: unknown = null;

      for (let index = 0; index < candidates.length; index += 1) {
        const candidate = candidates[index];
        const controller = new AbortController();
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
          const response = await fetch(candidate, {
            ...init,
            signal: controller.signal,
          });
          clearTimeout(timeoutId);

          if (response.status === 404 && index < candidates.length - 1) {
            continue;
          }

          return response;
        } catch (error) {
          clearTimeout(timeoutId);
          lastError = error;
          if (index === candidates.length - 1) {
            throw error;
          }
        }
      }

      if (lastError instanceof Error) {
        throw lastError;
      }
      throw new Error('Request failed');
    },
    [apiCandidates],
  );

  const fetchConversations = useCallback(async () => {
    try {
      const response = await apiFetch('/conversations?limit=100', {
        headers: await getAuthHeaders(),
      });
      if (!response.ok) {
        setConversations([]);
        return;
      }
      const data = await response.json();
      const conversationsArray: ApiConversation[] = data.conversations || [];

      const formattedConversations: Conversation[] =
        conversationsArray.map(toConversation);

      setConversations(formattedConversations);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return;
      }
      setConversations([]);
    } finally {
      setIsLoaded(true);
    }
  }, [apiFetch, getAuthHeaders]);

  const runSearch = useCallback(
    async (query: string, offset: number) => {
      const request = ++searchRequest.current;
      setSearch((prev) =>
        offset === 0
          ? { query, status: 'searching', results: [], hasMore: false }
          : { ...prev, status: 'searching' },
      );
      try {
        const params = new URLSearchParams({
          search: query,
          limit: String(SEARCH_PAGE_SIZE),
          offset: String(offset),
        });
        const response = await apiFetch(`/conversations?${params}`, {
          headers: await getAuthHeaders(),
        });
        if (request !== searchRequest.current) return;
        if (!response.ok) throw new Error(`Search failed: ${response.status}`);
        const data = await response.json();
        if (request !== searchRequest.current) return;
        const page: Conversation[] = (
          (data.conversations || []) as ApiConversation[]
        ).map(toConversation);
        setSearch((prev) => {
          const seen = new Set(
            offset === 0 ? [] : prev.results.map((c) => c.id),
          );
          const merged = offset === 0 ? [] : [...prev.results];
          for (const conv of page) {
            if (!seen.has(conv.id)) merged.push(conv);
          }
          return {
            query,
            status: 'ready',
            results: merged,
            hasMore: page.length === SEARCH_PAGE_SIZE,
          };
        });
      } catch {
        if (request !== searchRequest.current) return;
        setSearch((prev) => ({ ...prev, query, status: 'error' }));
      }
    },
    [apiFetch, getAuthHeaders],
  );

  useEffect(() => {
    const query = searchQuery.trim().slice(0, MAX_SEARCH_LENGTH);
    if (!query) {
      searchRequest.current += 1;
      setSearch(IDLE_SEARCH);
      return;
    }
    const timer = setTimeout(
      () => void runSearch(query, 0),
      SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [searchQuery, runSearch]);

  const loadMoreSearch = useCallback(() => {
    if (search.status !== 'ready' || !search.hasMore) return;
    void runSearch(search.query, search.results.length);
  }, [runSearch, search]);

  const retrySearch = useCallback(() => {
    if (search.query) void runSearch(search.query, 0);
  }, [runSearch, search.query]);

  const conversationSearch: ConversationSearch = {
    ...search,
    loadMore: loadMoreSearch,
    retry: retrySearch,
  };

  // Initial fetch and polling
  useEffect(() => {
    fetchConversations();
    const interval = setInterval(fetchConversations, 30000); // Poll every 30s
    return () => clearInterval(interval);
  }, [fetchConversations]);

  const createConversation = useCallback(async () => {
    try {
      const response = await apiFetch('/conversations', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(await getAuthHeaders()),
        },
        body: JSON.stringify({ title: 'New conversation' }),
      });

      if (!response.ok) return null;

      const newConv: ApiConversation = await response.json();
      const formattedConv: Conversation = {
        id: newConv.id,
        title: newConv.title,
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
  }, [apiFetch, getAuthHeaders, router]);

  const updateConversation = useCallback(
    async (
      id: string,
      updates: Partial<Conversation> & { messages?: DaemonMessage[] },
    ) => {
      // Optimistic update
      setConversations((prev) =>
        prev.map((conv) => (conv.id === id ? { ...conv, ...updates } : conv)),
      );
      setSearch((prev) => ({
        ...prev,
        results: prev.results.map((conv) =>
          conv.id === id ? { ...conv, ...updates } : conv,
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
          await apiFetch(`/conversations/${id}`, {
            method: 'PATCH',
            headers: {
              'Content-Type': 'application/json',
              ...(await getAuthHeaders()),
            },
            body: JSON.stringify(payload),
          });
        }
      } catch {
        fetchConversations(); // Revert on error
      }
    },
    [apiFetch, conversations, fetchConversations, getAuthHeaders],
  );

  const setConversationModel = useCallback(
    (id: string, model: string) => {
      updateConversation(id, { selectedModel: model });
    },
    [updateConversation],
  );

  const deleteConversation = useCallback(
    async (id: string) => {
      // Optimistic update
      setConversations((prev) => prev.filter((conv) => conv.id !== id));
      setSearch((prev) => ({
        ...prev,
        results: prev.results.filter((conv) => conv.id !== id),
      }));
      if (currentId === id) {
        router.push('/');
      }

      try {
        const response = await apiFetch(`/conversations/${id}`, {
          method: 'DELETE',
          headers: await getAuthHeaders(),
        });

        if (!response.ok) {
          fetchConversations();
          return false;
        }

        return true;
      } catch {
        fetchConversations(); // Revert on error
        return false;
      }
    },
    [apiFetch, currentId, router, fetchConversations, getAuthHeaders],
  );

  const fetchConversationById = useCallback(
    async (id: string): Promise<Conversation | null> => {
      try {
        const response = await apiFetch(`/conversations/${id}`, {
          headers: await getAuthHeaders(),
        });
        if (!response.ok) {
          return null;
        }

        const data = await response.json();
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

        return formattedConv;
      } catch {
        return null;
      }
    },
    [apiFetch, getAuthHeaders],
  );

  const [currentConversation, setCurrentConversation] =
    useState<Conversation | null>(null);

  useEffect(() => {
    if (!currentId) {
      setCurrentConversation(null);
      return;
    }

    const fetchConversationDetails = async () => {
      const conversation = await fetchConversationById(currentId);
      if (conversation) {
        setCurrentConversation(conversation);
      }
    };

    fetchConversationDetails();
  }, [currentId, fetchConversationById]);

  const getCurrentConversation = useCallback(() => {
    return currentConversation;
  }, [currentConversation]);

  // The open conversation id as of the latest render, for discarding refreshes
  // that finish after the user has moved to another conversation.
  const currentIdRef = useRef(currentId);
  // A layout effect runs before passive effects, so a refresh that resolves
  // during the switch already sees the new id.
  useLayoutEffect(() => {
    currentIdRef.current = currentId;
  }, [currentId]);

  /**
   * Re-read the open conversation, e.g. while a durable task is running.
   * Resolves to ``null`` when the user has switched conversations meanwhile,
   * so callers never render one conversation's messages in another.
   */
  const refreshCurrentConversation = useCallback(async () => {
    const requestedId = currentIdRef.current;
    if (!requestedId) return null;
    const conversation = await fetchConversationById(requestedId);
    if (!conversation || currentIdRef.current !== requestedId) return null;
    setCurrentConversation((previous) =>
      previous === null || previous.id === conversation.id
        ? conversation
        : previous,
    );
    return conversation;
  }, [fetchConversationById]);

  /**
   * The id of the caller's task created with ``key``: ``null`` when the
   * server has none (it did not accept that submission), ``undefined`` when
   * the answer is unknown (network error, older backend).
   */
  const taskIdForKey = useCallback(
    async (key: string): Promise<string | null | undefined> => {
      try {
        const response = await apiFetch(
          `/tasks/by-key/${encodeURIComponent(key)}`,
          { headers: await getAuthHeaders() },
        );
        if (response.status === 404) return null;
        if (!response.ok) return undefined;
        const task = (await response.json()) as { id?: unknown };
        return typeof task.id === 'string' ? task.id : undefined;
      } catch {
        return undefined;
      }
    },
    [apiFetch, getAuthHeaders],
  );

  /**
   * Ask the server to cancel a durable task (Stop, not a disconnect).
   * ``cancelled``: the server accepted the cancellation. ``finished``: the
   * task had already ended on its own. ``unconfirmed``: no answer either way.
   */
  const cancelTask = useCallback(
    async (taskId: string): Promise<TaskCancelOutcome> => {
      try {
        const response = await apiFetch(
          `/tasks/${encodeURIComponent(taskId)}/cancel`,
          { method: 'POST', headers: await getAuthHeaders() },
        );
        if (response.ok) return 'cancelled';
        if (response.status === 409) return 'finished';
        return 'unconfirmed';
      } catch {
        return 'unconfirmed';
      }
    },
    [apiFetch, getAuthHeaders],
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
    taskIdForKey,
    switchConversation,
    fetchConversationById,
    searchQuery,
    setSearchQuery,
    conversationSearch,
    refreshConversations: fetchConversations,
  };
}
