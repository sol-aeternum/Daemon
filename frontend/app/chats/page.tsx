"use client";

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Search, Trash2, CheckSquare, Square } from "lucide-react";
import { SidebarShell } from "@/components/SidebarShell";
import {
  ConversationHistoryProvider,
  useConversationHistoryContext,
} from "@/components/ConversationHistoryProvider";

function ChatsView() {
  const router = useRouter();
  const {
    conversations,
    createConversation,
    searchQuery,
    setSearchQuery,
    deleteConversation,
    fetchConversationById,
  } = useConversationHistoryContext();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [isDeleting, setIsDeleting] = useState(false);
  const [contentMatchedIds, setContentMatchedIds] = useState<Set<string>>(new Set());
  const [isSearchingContent, setIsSearchingContent] = useState(false);
  const contentIndexCacheRef = useRef<Map<string, string>>(new Map());

  const normalizedQuery = searchQuery.trim().toLowerCase();

  const selectedCount = selectedIds.size;

  const sortedConversations = useMemo(() => {
    return [...conversations].sort(
      (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime(),
    );
  }, [conversations]);

  const titleMatchedIds = useMemo(() => {
    if (!normalizedQuery) {
      return new Set<string>();
    }

    return new Set(
      sortedConversations
        .filter((conversation) => (conversation.title || "").toLowerCase().includes(normalizedQuery))
        .map((conversation) => conversation.id),
    );
  }, [normalizedQuery, sortedConversations]);

  useEffect(() => {
    let isCancelled = false;

    if (!normalizedQuery) {
      setContentMatchedIds(new Set());
      setIsSearchingContent(false);
      return;
    }

    const runSearch = async () => {
      setIsSearchingContent(true);

      const matches = new Set<string>();
      const candidates = sortedConversations.filter(
        (conversation) => !titleMatchedIds.has(conversation.id),
      );

      for (const candidate of candidates) {
        const cachedText = contentIndexCacheRef.current.get(candidate.id);
        if (cachedText !== undefined && cachedText.includes(normalizedQuery)) {
          matches.add(candidate.id);
        }
      }

      const missingCandidates = candidates.filter(
        (candidate) => !contentIndexCacheRef.current.has(candidate.id),
      );

      const chunkSize = 5;
      for (let i = 0; i < missingCandidates.length; i += chunkSize) {
        const chunk = missingCandidates.slice(i, i + chunkSize);

        const results = await Promise.all(
          chunk.map(async (conversation) => {
            const details = await fetchConversationById(conversation.id);
            const searchableText = (details?.messages || [])
              .map((message) =>
                typeof message.content === "string" ? message.content.toLowerCase() : "",
              )
              .join("\n");

            contentIndexCacheRef.current.set(conversation.id, searchableText);
            return { id: conversation.id, searchableText };
          }),
        );

        if (isCancelled) {
          return;
        }

        for (const result of results) {
          if (result.searchableText.includes(normalizedQuery)) {
            matches.add(result.id);
          }
        }
      }

      if (!isCancelled) {
        setContentMatchedIds(matches);
        setIsSearchingContent(false);
      }
    };

    void runSearch();

    return () => {
      isCancelled = true;
    };
  }, [fetchConversationById, normalizedQuery, sortedConversations, titleMatchedIds]);

  const filteredConversations = useMemo(() => {
    if (!normalizedQuery) {
      return sortedConversations;
    }

    return sortedConversations.filter(
      (conversation) =>
        titleMatchedIds.has(conversation.id) || contentMatchedIds.has(conversation.id),
    );
  }, [contentMatchedIds, normalizedQuery, sortedConversations, titleMatchedIds]);

  const allSelected =
    filteredConversations.length > 0 &&
    filteredConversations.every((conversation) => selectedIds.has(conversation.id));

  const toggleSelection = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedIds(new Set());
      return;
    }
    setSelectedIds(new Set(filteredConversations.map((conversation) => conversation.id)));
  };

  const handleDeleteSelected = async () => {
    if (selectedCount === 0 || isDeleting) {
      return;
    }

    setIsDeleting(true);
    const ids = [...selectedIds];
    const results = await Promise.all(ids.map((id) => deleteConversation(id)));

    const failedIds = ids.filter((_, index) => !results[index]);
    setSelectedIds(new Set(failedIds));
    setIsDeleting(false);
  };

  const handleNewChat = async () => {
    const id = await createConversation();
    if (id) {
      router.push(`/?id=${id}`);
    }
  };

  return (
    <SidebarShell
      section="chats"
      title="Chats"
      headerAction={
        <button
          onClick={handleNewChat}
          className="hidden md:inline-flex items-center gap-2 rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-2.5 text-sm font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)] transition-colors"
        >
          <Plus className="h-4 w-4" />
          New chat
        </button>
      }
    >
      <div className="mx-auto w-full max-w-5xl px-4 py-6 md:px-6 md:py-8 space-y-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-muted)]" />
          <input
            type="text"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Search your chats..."
            className="w-full rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] py-3 pl-10 pr-3 text-sm text-[var(--color-text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)]"
          />
        </div>

        {normalizedQuery && (
          <p className="text-xs text-[var(--color-text-muted)]">
            {isSearchingContent
              ? "Searching conversation titles and message content..."
              : "Showing matches from titles and message content."}
          </p>
        )}

        <div className="flex items-center justify-between border-b border-[var(--color-border-muted)] pb-3">
          <button
            onClick={toggleSelectAll}
            className="inline-flex items-center gap-2 text-sm text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
          >
            {allSelected ? <CheckSquare className="h-4 w-4" /> : <Square className="h-4 w-4" />}
            {allSelected ? "Clear selection" : "Select all"}
          </button>

          <div className="inline-flex items-center gap-3">
            <span className="text-sm text-[var(--color-text-secondary)]">{selectedCount} selected</span>
            <button
              onClick={handleDeleteSelected}
              disabled={selectedCount === 0 || isDeleting}
              className="inline-flex items-center gap-2 rounded-lg border border-[var(--color-status-error)]/40 px-3 py-2 text-sm text-[var(--color-status-error)] disabled:opacity-40 disabled:cursor-not-allowed hover:bg-[var(--color-status-error-bg)]"
            >
              <Trash2 className="h-4 w-4" />
              {isDeleting ? "Deleting..." : "Delete"}
            </button>
          </div>
        </div>

        <div className="divide-y divide-[var(--color-border-muted)] rounded-xl border border-[var(--color-border-primary)] overflow-hidden">
          {filteredConversations.map((conversation) => {
            const selected = selectedIds.has(conversation.id);

            return (
              <div
                key={conversation.id}
                className={`flex items-start gap-3 p-4 transition-colors ${
                  selected ? "bg-[var(--color-accent-subtle)]" : "bg-[var(--color-bg-secondary)]"
                }`}
              >
                <button
                  onClick={() => toggleSelection(conversation.id)}
                  className="mt-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                  aria-label={selected ? "Deselect chat" : "Select chat"}
                >
                  {selected ? <CheckSquare className="h-5 w-5" /> : <Square className="h-5 w-5" />}
                </button>

                <button
                  onClick={() => router.push(`/?id=${conversation.id}`)}
                  className="flex-1 min-w-0 text-left"
                >
                  <p className="truncate text-base font-medium text-[var(--color-text-primary)]">
                    {conversation.title || "New conversation"}
                  </p>
                  <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
                    Last message {new Date(conversation.updatedAt).toLocaleString()}
                  </p>
                  <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                    {conversation.messageCount ?? 0} messages
                  </p>
                </button>
              </div>
            );
          })}

          {filteredConversations.length === 0 && (
            <div className="p-6 text-sm text-[var(--color-text-muted)]">No chats match this search.</div>
          )}
        </div>
      </div>
    </SidebarShell>
  );
}

export default function ChatsPage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center">Loading chats...</div>}>
      <ConversationHistoryProvider>
        <ChatsView />
      </ConversationHistoryProvider>
    </Suspense>
  );
}
