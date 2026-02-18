"use client";

import React, { createContext, useContext, ReactNode } from "react";
import { useConversationHistory } from "@/hooks/useConversationHistory";

/**
 * Context for conversation history.
 * 
 * This context wraps the useConversationHistory hook to provide
 * a single instance of conversation state across the application.
 * 
 * NOTE: Production code should use useConversationHistoryContext.
 * The raw useConversationHistory hook is exported for testing purposes only.
 */
export interface ConversationHistoryContextValue {
  conversations: ReturnType<typeof useConversationHistory>["conversations"];
  currentId: ReturnType<typeof useConversationHistory>["currentId"];
  isLoaded: ReturnType<typeof useConversationHistory>["isLoaded"];
  createConversation: ReturnType<typeof useConversationHistory>["createConversation"];
  updateConversation: ReturnType<typeof useConversationHistory>["updateConversation"];
  setConversationModel: ReturnType<typeof useConversationHistory>["setConversationModel"];
  deleteConversation: ReturnType<typeof useConversationHistory>["deleteConversation"];
  getCurrentConversation: ReturnType<typeof useConversationHistory>["getCurrentConversation"];
  switchConversation: ReturnType<typeof useConversationHistory>["switchConversation"];
  searchQuery: ReturnType<typeof useConversationHistory>["searchQuery"];
  setSearchQuery: ReturnType<typeof useConversationHistory>["setSearchQuery"];
  refreshConversations: ReturnType<typeof useConversationHistory>["refreshConversations"];
}

const ConversationHistoryContext = createContext<ConversationHistoryContextValue | null>(null);

interface ConversationHistoryProviderProps {
  children: ReactNode;
}

/**
 * Provider component that instantiates useConversationHistory once
 * and exposes it via React context.
 * 
 * Place this at the top of your component tree (e.g., in layout or ChatContentWrapper)
 * to ensure all consumers share the same conversation state and polling interval.
 */
export function ConversationHistoryProvider({ children }: ConversationHistoryProviderProps) {
  const history = useConversationHistory();
  
  return (
    <ConversationHistoryContext.Provider value={history}>
      {children}
    </ConversationHistoryContext.Provider>
  );
}

/**
 * Hook to access conversation history from context.
 * 
 * @throws Error if used outside ConversationHistoryProvider
 * 
 * NOTE: Production code should use this hook.
 * The raw useConversationHistory hook is for testing purposes only.
 */
export function useConversationHistoryContext(): ConversationHistoryContextValue {
  const context = useContext(ConversationHistoryContext);
  
  if (!context) {
    throw new Error(
      "useConversationHistoryContext must be used within a ConversationHistoryProvider"
    );
  }
  
  return context;
}

// Re-export the hook for testing purposes
export { useConversationHistory };
