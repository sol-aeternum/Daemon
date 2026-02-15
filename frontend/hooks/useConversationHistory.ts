"use client";

import { Message } from "ai";
import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

export interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  selectedModel?: string;
  createdAt: string;
  updatedAt: string;
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

  const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const fetchConversations = useCallback(async () => {
    try {
      const response = await fetch(`${apiBaseUrl}/conversations?limit=100`);
      if (!response.ok) throw new Error("Failed to fetch conversations");
      const data = await response.json();
      const conversationsArray: ApiConversation[] = data.conversations || [];
      
      const formattedConversations: Conversation[] = conversationsArray.map((conv) => ({
        id: conv.id,
        title: conv.title,
        messages: [], // Messages are fetched individually
        selectedModel: conv.metadata?.model || "auto",
        createdAt: conv.created_at,
        updatedAt: conv.updated_at,
        pinned: conv.pinned,
        title_locked: conv.title_locked,
        status: conv.status,
        metadata: conv.metadata || {},
      }));
      
      setConversations(formattedConversations);
      setIsLoaded(true);
    } catch (error) {
      console.error("Error fetching conversations:", error);
      setIsLoaded(true);
    }
  }, [apiBaseUrl]);

  // Initial fetch and polling
  useEffect(() => {
    fetchConversations();
    const interval = setInterval(fetchConversations, 30000); // Poll every 30s
    return () => clearInterval(interval);
  }, [fetchConversations]);

  const createConversation = useCallback(async () => {
    try {
      const response = await fetch(`${apiBaseUrl}/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "New conversation" }),
      });
      
      if (!response.ok) throw new Error("Failed to create conversation");
      
      const newConv: ApiConversation = await response.json();
      const formattedConv: Conversation = {
        id: newConv.id,
        title: newConv.title,
        messages: [],
        selectedModel: "auto",
        createdAt: newConv.created_at,
        updatedAt: newConv.updated_at,
        pinned: newConv.pinned,
        title_locked: newConv.title_locked,
        status: newConv.status,
        metadata: newConv.metadata || {},
      };

      setConversations((prev) => [formattedConv, ...prev]);
      router.push(`/?id=${newConv.id}`);
      return newConv.id;
    } catch (error) {
      console.error("Error creating conversation:", error);
      return null;
    }
  }, [apiBaseUrl, router]);

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
            await fetch(`${apiBaseUrl}/conversations/${id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
        }
      } catch (error) {
        console.error("Error updating conversation:", error);
        fetchConversations(); // Revert on error
      }
    },
    [apiBaseUrl, conversations, fetchConversations]
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
        await fetch(`${apiBaseUrl}/conversations/${id}`, {
          method: "DELETE",
        });
      } catch (error) {
        console.error("Error deleting conversation:", error);
        fetchConversations(); // Revert on error
      }
    },
    [apiBaseUrl, currentId, router, fetchConversations]
  );

  const [currentConversation, setCurrentConversation] = useState<Conversation | null>(null);

  useEffect(() => {
    if (!currentId) {
      setCurrentConversation(null);
      return;
    }

    const fetchConversationDetails = async () => {
      try {
        const response = await fetch(`${apiBaseUrl}/conversations/${currentId}`);
        if (!response.ok) throw new Error("Failed to fetch conversation details");
        const data = await response.json();
        
        const formattedConv: Conversation = {
          id: data.id,
          title: data.title,
          messages: data.messages || [],
          selectedModel: data.metadata?.model || "auto",
          createdAt: data.created_at,
          updatedAt: data.updated_at,
          pinned: data.pinned,
          title_locked: data.title_locked,
          status: data.status,
          metadata: data.metadata || {},
        };
        
        setCurrentConversation(formattedConv);
      } catch (error) {
        console.error("Error fetching conversation details:", error);
      }
    };

    fetchConversationDetails();
  }, [currentId, apiBaseUrl]);

  const getCurrentConversation = useCallback(() => {
    return currentConversation;
  }, [currentConversation]);

  const switchConversation = useCallback((id: string) => {
    router.push(`/?id=${id}`);
  }, [router]);

  const filteredConversations = conversations.filter(conv => 
    conv.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return {
    conversations: filteredConversations,
    currentId,
    isLoaded,
    createConversation,
    updateConversation,
    setConversationModel,
    deleteConversation,
    getCurrentConversation,
    switchConversation,
    searchQuery,
    setSearchQuery,
    refreshConversations: fetchConversations
  };
}
