"use client";

import { Message } from "ai";
import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { getAuthHeader, refreshIfNeeded } from "@/lib/auth";

export interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  selectedModel?: string;
  createdAt: string;
  updatedAt: string;
  messageCount?: number;
  lastActivityAt?: string | null;
  pinned: boolean;
  title_locked: boolean;
  status: string;
  metadata: Record<string, any>;
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

export function useConversationHistory() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [isLoaded, setIsLoaded] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const router = useRouter();
  const searchParams = useSearchParams();
  const currentId = searchParams.get("id");

  const apiBaseUrl =
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");

  const getAuthHeaders = useCallback(async (): Promise<Record<string, string>> => {
    const header = getAuthHeader();
    if (header) return { Authorization: header };
    const token = await refreshIfNeeded();
    if (token) return { Authorization: `Bearer ${token}` };
    return {};
  }, []);

  const apiCandidates = useCallback(
    (path: string) => {
      const normalizedPath = path.startsWith("/") ? path : `/${path}`;
      const trimmedBase = apiBaseUrl.endsWith("/") ? apiBaseUrl.slice(0, -1) : apiBaseUrl;

      if (!trimmedBase) {
        return [normalizedPath];
      }

      return [`${trimmedBase}${normalizedPath}`, normalizedPath];
    },
    [apiBaseUrl]
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
            controller.abort(new DOMException("Request timed out", "AbortError"));
          } catch {
            controller.abort();
          }
        }, timeoutMs);

        try {
          const response = await fetch(candidate, { ...init, signal: controller.signal });
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
      throw new Error("Request failed");
    },
    [apiCandidates]
  );

  const fetchConversations = useCallback(async () => {
    try {
      const response = await apiFetch("/conversations?limit=100", {
        headers: await getAuthHeaders(),
      });
      if (!response.ok) {
        setConversations([]);
        return;
      }
      const data = await response.json();
      const conversationsArray: ApiConversation[] = data.conversations || [];
      
      const formattedConversations: Conversation[] = conversationsArray.map((conv) => ({
        id: conv.id,
        title: conv.title,
        messages: [], // Messages are fetched individually
        selectedModel: conv.metadata?.model || "auto",
        createdAt: conv.created_at,
        updatedAt: conv.updated_at,
        messageCount: conv.message_count,
        lastActivityAt: conv.last_activity_at,
        pinned: conv.pinned,
        title_locked: conv.title_locked,
        status: conv.status,
        metadata: conv.metadata || {},
      }));

      setConversations(formattedConversations);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      setConversations([]);
    } finally {
      setIsLoaded(true);
    }
  }, [apiFetch, getAuthHeaders]);

  // Initial fetch and polling
  useEffect(() => {
    fetchConversations();
    const interval = setInterval(fetchConversations, 30000); // Poll every 30s
    return () => clearInterval(interval);
  }, [fetchConversations]);

  const createConversation = useCallback(async () => {
    try {
      const response = await apiFetch("/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await getAuthHeaders()) },
        body: JSON.stringify({ title: "New conversation" }),
      });
      
      if (!response.ok) return null;
      
      const newConv: ApiConversation = await response.json();
      const formattedConv: Conversation = {
        id: newConv.id,
        title: newConv.title,
        messages: [],
        selectedModel: "auto",
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
    async (id: string, updates: Partial<Conversation> & { messages?: Message[] }) => {
      // Optimistic update
      setConversations((prev) =>
        prev.map((conv) => (conv.id === id ? { ...conv, ...updates } : conv))
      );

      try {
        const payload: any = {};
        if (updates.title !== undefined) payload.title = updates.title;
        if (updates.pinned !== undefined) payload.pinned = updates.pinned;
        if (updates.title_locked !== undefined) payload.title_locked = updates.title_locked;
        if (updates.selectedModel !== undefined) {
            // Update metadata for model selection
            const currentConv = conversations.find(c => c.id === id);
            payload.metadata = { ...(currentConv?.metadata || {}), model: updates.selectedModel };
        }

        if (Object.keys(payload).length > 0) {
            await apiFetch(`/conversations/${id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json", ...(await getAuthHeaders()) },
                body: JSON.stringify(payload),
            });
        }
      } catch {
        fetchConversations(); // Revert on error
      }
    },
    [apiFetch, conversations, fetchConversations, getAuthHeaders]
  );

  const setConversationModel = useCallback(
    (id: string, model: string) => {
      updateConversation(id, { selectedModel: model });
    },
    [updateConversation]
  );

  const deleteConversation = useCallback(
    async (id: string) => {
      // Optimistic update
      setConversations((prev) => prev.filter((conv) => conv.id !== id));
      if (currentId === id) {
        router.push("/");
      }

      try {
        const response = await apiFetch(`/conversations/${id}`, {
          method: "DELETE",
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
    [apiFetch, currentId, router, fetchConversations, getAuthHeaders]
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
          messages: data.messages || [],
          selectedModel: data.metadata?.model || "auto",
          createdAt: data.created_at,
          updatedAt: data.updated_at,
          messageCount: data.message_count,
          lastActivityAt: data.last_activity_at,
          pinned: data.pinned,
          title_locked: data.title_locked,
          status: data.status,
          metadata: data.metadata || {},
        };

        return formattedConv;
      } catch {
        return null;
      }
    },
    [apiFetch, getAuthHeaders]
  );

  const [currentConversation, setCurrentConversation] = useState<Conversation | null>(null);

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

  const switchConversation = useCallback((id: string) => {
    router.push(`/?id=${id}`);
  }, [router]);

  return {
    conversations,
    currentId,
    isLoaded,
    createConversation,
    updateConversation,
    setConversationModel,
    deleteConversation,
    getCurrentConversation,
    switchConversation,
    fetchConversationById,
    searchQuery,
    setSearchQuery,
    refreshConversations: fetchConversations
  };
}
