"use client";

import { Message } from "ai";
import { useLocalStorage } from "./useLocalStorage";
import { useCallback, useState } from "react";

export interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  createdAt: string;
  updatedAt: string;
}

export function useConversationHistory() {
  const { value: conversations, setValue: setConversations, isLoaded } = useLocalStorage<Conversation[]>(
    "daemon-conversations",
    []
  );

  const [currentId, setCurrentId] = useState<string | null>(null);

  const createConversation = useCallback(() => {
    const newConversation: Conversation = {
      id: `conv_${Date.now()}`,
      title: "New conversation",
      messages: [],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    setConversations((prev) => [newConversation, ...prev]);
    setCurrentId(newConversation.id);
    return newConversation.id;
  }, [setConversations]);

  const updateConversation = useCallback(
    (id: string, messages: Message[]) => {
      setConversations((prev) => {
        const updated = prev.map((conv) => {
          if (conv.id === id) {
            const title =
              messages.length > 0
                ? messages.find((m) => m.role === "user")?.content.slice(0, 50) ||
                  conv.title
                : conv.title;
            return {
              ...conv,
              messages,
              title: title.slice(0, 50) + (title.length > 50 ? "..." : ""),
              updatedAt: new Date().toISOString(),
            };
          }
          return conv;
        });
        return updated;
      });
    },
    [setConversations]
  );

  const deleteConversation = useCallback(
    (id: string) => {
      setConversations((prev) => prev.filter((conv) => conv.id !== id));
      if (currentId === id) {
        setCurrentId(null);
      }
    },
    [currentId, setConversations]
  );

  const getCurrentConversation = useCallback(() => {
    if (!currentId) return null;
    return conversations.find((conv) => conv.id === currentId) || null;
  }, [currentId, conversations]);

  const switchConversation = useCallback((id: string) => {
    setCurrentId(id);
  }, []);

  return {
    conversations,
    currentId,
    isLoaded,
    createConversation,
    updateConversation,
    deleteConversation,
    getCurrentConversation,
    switchConversation,
  };
}
