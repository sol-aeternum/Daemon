'use client';

import { useState, useMemo, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Conversation } from '../hooks/useConversationHistory';
import { AccountWidget } from './AccountWidget';
import { SkeletonCircle, SkeletonLine } from './ui/Skeleton';
import {
  MoreHorizontal,
  Pin,
  Trash2,
  Edit2,
  MessageSquare,
  Search,
  FolderKanban,
  GalleryHorizontal,
  Image as ImageIcon,
  Plus,
} from 'lucide-react';

export type SidebarSection =
  | 'home'
  | 'chats'
  | 'projects'
  | 'artifacts'
  | 'studio';

interface ConversationListProps {
  conversations: Conversation[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onUpdate: (id: string, updates: Partial<Conversation>) => void;
  onNewChat: () => void;
  className?: string;
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  isLoading?: boolean;
  activeSection?: SidebarSection;
  onNavigate?: (section: SidebarSection) => void;
  onGoHome?: () => void;
}

export function ConversationList({
  conversations,
  currentId,
  onSelect,
  onDelete,
  onUpdate,
  onNewChat,
  className = '',
  searchQuery,
  setSearchQuery,
  isLoading = false,
  activeSection = 'home',
  onNavigate,
  onGoHome,
}: ConversationListProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState<{
    top: number;
    left: number;
  } | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const isBrowser = typeof document !== 'undefined';

  const normalizedSearchQuery = searchQuery.trim().toLowerCase();
  const visibleConversations = useMemo(() => {
    const filteredConversations = conversations.filter((conversation) => {
      if (conversation.id === currentId) return true;
      if (conversation.messageCount && conversation.messageCount > 0)
        return true;
      if (conversation.title && conversation.title !== 'New conversation')
        return true;
      return false;
    });

    if (!normalizedSearchQuery) {
      return filteredConversations;
    }

    return filteredConversations.filter((conversation) =>
      (conversation.title || '').toLowerCase().includes(normalizedSearchQuery),
    );
  }, [conversations, normalizedSearchQuery, currentId]);

  const pinnedConversations = useMemo(
    () => visibleConversations.filter((c) => c.pinned),
    [visibleConversations],
  );

  const unpinnedConversations = useMemo(
    () => visibleConversations.filter((c) => !c.pinned),
    [visibleConversations],
  );

  // Group unpinned conversations by time
  const groupedUnpinnedConversations = useMemo(() => {
    const groups: Record<string, Conversation[]> = {
      Today: [],
      Yesterday: [],
      'Previous 7 days': [],
      'Previous 30 days': [],
      Older: [],
    };

    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const sevenDaysAgo = new Date(today);
    sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);
    const thirtyDaysAgo = new Date(today);
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

    unpinnedConversations.forEach((conv) => {
      const convDate = new Date(conv.updatedAt);
      const convDay = new Date(
        convDate.getFullYear(),
        convDate.getMonth(),
        convDate.getDate(),
      );

      if (convDay >= today) {
        groups['Today'].push(conv);
      } else if (convDay >= yesterday) {
        groups['Yesterday'].push(conv);
      } else if (convDay >= sevenDaysAgo) {
        groups['Previous 7 days'].push(conv);
      } else if (convDay >= thirtyDaysAgo) {
        groups['Previous 30 days'].push(conv);
      } else {
        groups['Older'].push(conv);
      }
    });

    return groups;
  }, [unpinnedConversations]);

  const handleRename = (id: string, newTitle: string) => {
    onUpdate(id, { title: newTitle, title_locked: true });
    setEditingId(null);
  };

  const navItems: Array<{
    section: SidebarSection;
    label: string;
    icon: typeof MessageSquare;
  }> = [
    { section: 'chats', label: 'Chats', icon: MessageSquare },
    { section: 'projects', label: 'Projects', icon: FolderKanban },
    { section: 'artifacts', label: 'Artifacts', icon: GalleryHorizontal },
    { section: 'studio', label: 'Studio', icon: ImageIcon },
  ];

  const handleNavigate = (section: SidebarSection) => {
    onNavigate?.(section);
  };

  const togglePin = (
    e: React.MouseEvent,
    id: string,
    currentPinned: boolean,
  ) => {
    e.stopPropagation();
    onUpdate(id, { pinned: !currentPinned });
    setMenuOpenId(null);
  };

  const startRename = (e: React.MouseEvent, conv: Conversation) => {
    e.stopPropagation();
    setEditingId(conv.id);
    setEditTitle(conv.title);
    setMenuOpenId(null);
  };

  const confirmDelete = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    setDeleteConfirmId(id);
    setMenuOpenId(null);
  };

  const ConversationItem = ({ conv }: { conv: Conversation }) => (
    <div
      className={`group relative p-3 cursor-pointer hover:bg-[var(--color-bg-hover)] transition-colors min-h-[60px] flex items-center ${
        currentId === conv.id
          ? 'bg-[var(--color-accent-subtle)] hover:bg-[var(--color-accent-muted)]'
          : ''
      }`}
      onClick={() => onSelect(conv.id)}
    >
      <div className="flex items-start justify-between w-full min-w-0">
        <div className="flex-1 min-w-0 pr-8">
          {editingId === conv.id ? (
            <input
              autoFocus
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              onBlur={() => handleRename(conv.id, editTitle)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleRename(conv.id, editTitle);
                if (e.key === 'Escape') setEditingId(null);
              }}
              onClick={(e) => e.stopPropagation()}
              className="w-full px-1 py-0.5 text-sm border rounded focus:outline-none focus:border-[var(--color-border-focus)]"
            />
          ) : (
            <>
              <div className="flex items-center gap-2">
                {conv.pinned && (
                  <Pin className="w-3 h-3 text-[var(--color-accent-primary)] flex-shrink-0" />
                )}
                <p className="text-sm font-medium text-[var(--color-text-primary)] truncate">
                  {conv.title || 'New conversation'}
                </p>
              </div>
              <p className="text-xs text-[var(--color-text-muted)] mt-1 truncate">
                {new Date(conv.updatedAt).toLocaleDateString()}
              </p>
            </>
          )}
        </div>

        <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center">
          <button
            ref={menuButtonRef}
            aria-label="Conversation actions"
            onClick={(e) => {
              e.stopPropagation();
              if (menuOpenId === conv.id) {
                setMenuOpenId(null);
                setMenuPosition(null);
              } else {
                const rect = e.currentTarget.getBoundingClientRect();
                setMenuPosition({
                  top: rect.bottom + 4,
                  left: rect.right - 128,
                });
                setMenuOpenId(conv.id);
              }
            }}
            className={`min-h-[44px] min-w-[44px] rounded-md p-1.5 text-[var(--color-text-muted)] transition-all hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-secondary)] ${
              menuOpenId === conv.id
                ? 'opacity-100 bg-[var(--color-bg-hover)]'
                : 'opacity-100 md:opacity-0 md:group-hover:opacity-100'
            }`}
          >
            <MoreHorizontal className="w-4 h-4" />
          </button>

          {menuOpenId === conv.id &&
            menuPosition &&
            isBrowser &&
            createPortal(
              <div
                data-stop-shortcut-block="true"
                className="fixed w-32 bg-[var(--color-bg-secondary)] rounded-lg shadow-lg border border-[var(--color-border-muted)] py-1 z-50"
                style={{ top: menuPosition.top, left: menuPosition.left }}
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  onClick={(e) => {
                    togglePin(e, conv.id, conv.pinned);
                    setMenuOpenId(null);
                    setMenuPosition(null);
                  }}
                  className="flex w-full min-h-[44px] items-center gap-2 px-3 py-2 text-left text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)]"
                >
                  <Pin className="w-3 h-3" />
                  {conv.pinned ? 'Unpin' : 'Pin'}
                </button>
                <button
                  onClick={(e) => {
                    startRename(e, conv);
                    setMenuOpenId(null);
                    setMenuPosition(null);
                  }}
                  className="flex w-full min-h-[44px] items-center gap-2 px-3 py-2 text-left text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)]"
                >
                  <Edit2 className="w-3 h-3" />
                  Rename
                </button>
                <button
                  onClick={(e) => {
                    confirmDelete(e, conv.id);
                    setMenuOpenId(null);
                    setMenuPosition(null);
                  }}
                  className="flex w-full min-h-[44px] items-center gap-2 px-3 py-2 text-left text-xs text-[var(--color-status-error)] hover:bg-[var(--color-status-error-bg)]"
                >
                  <Trash2 className="w-3 h-3" />
                  Delete
                </button>
              </div>,
              document.body,
            )}
        </div>
      </div>
    </div>
  );

  return (
    <div
      className={`w-full md:w-[260px] bg-[var(--color-bg-tertiary)] border-r border-[var(--color-border-primary)] flex flex-col h-full ${className}`}
    >
      <div
        className="p-4 border-b pt-[max(1rem,env(safe-area-inset-top))] space-y-2"
        suppressHydrationWarning
      >
        <button
          onClick={onGoHome}
          className="w-full text-left rounded-lg px-2 py-1 text-xl font-semibold tracking-tight text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)] transition-colors"
        >
          Daemon
        </button>

        <nav className="space-y-1">
          <button
            onClick={onNewChat}
            className="w-full min-h-[40px] rounded-md px-3 py-2 text-sm flex items-center gap-2.5 transition-colors text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
          >
            <Plus className="w-4 h-4" />
            <span>New chat</span>
          </button>

          {navItems.map(({ section, label, icon: Icon }) => {
            const isActive = activeSection === section;
            return (
              <button
                key={section}
                onClick={() => handleNavigate(section)}
                className={`w-full min-h-[40px] rounded-md px-3 py-2 text-sm flex items-center gap-2.5 transition-colors ${
                  isActive
                    ? 'bg-[var(--color-accent-subtle)] text-[var(--color-text-primary)]'
                    : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]'
                }`}
              >
                <Icon className="w-4 h-4" />
                <span>{label}</span>
              </button>
            );
          })}
        </nav>

        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--color-text-muted)]" />
          <input
            type="text"
            placeholder="Search conversations..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full min-h-[44px] pl-9 pr-3 py-2 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border-primary)] rounded-lg focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:border-transparent"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto pb-[env(safe-area-inset-bottom)]">
        {isLoading ? (
          <div className="p-3 space-y-1">
            {[...Array(7)].map((_, i) => (
              <div key={i} className="flex items-center gap-3 p-3 min-h-[60px]">
                <SkeletonCircle size={40} />
                <div className="flex-1 space-y-2 min-w-0">
                  <SkeletonLine width="70%" height="0.875rem" />
                  <SkeletonLine width="30%" height="0.75rem" />
                </div>
              </div>
            ))}
          </div>
        ) : visibleConversations.length === 0 ? (
          <div className="p-4 text-center text-[var(--color-text-muted)] text-sm">
            No conversations found
          </div>
        ) : (
          <div className="divide-y divide-[var(--color-border-muted)]">
            {pinnedConversations.length > 0 && (
              <>
                <div className="px-4 py-2 bg-[var(--color-bg-secondary)]/50 text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                  Pinned
                </div>
                {pinnedConversations.map((conv) => (
                  <ConversationItem key={conv.id} conv={conv} />
                ))}
              </>
            )}

            {unpinnedConversations.length > 0 && (
              <>
                {Object.entries(groupedUnpinnedConversations).map(
                  ([group, convs]) =>
                    convs.length > 0 ? (
                      <div key={group}>
                        <div className="px-4 py-2 bg-[var(--color-bg-secondary)]/50 text-xs font-semibold text-[var(--color-text-muted)] uppercase tracking-wider">
                          {group}
                        </div>
                        {convs.map((conv) => (
                          <ConversationItem key={conv.id} conv={conv} />
                        ))}
                      </div>
                    ) : null,
                )}
              </>
            )}
          </div>
        )}
      </div>

      <AccountWidget />

      {deleteConfirmId && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Delete conversation"
          data-stop-shortcut-block="true"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
        >
          <div className="w-full max-w-sm rounded-lg bg-[var(--color-bg-secondary)] p-4 shadow-xl">
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
              Delete Conversation
            </h3>
            <p className="mt-2 text-xs text-[var(--color-text-secondary)]">
              Are you sure you want to delete this conversation? This cannot be
              undone.
            </p>
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setDeleteConfirmId(null)}
                className="rounded-md border border-[var(--color-border-primary)] px-3 py-2 text-xs font-semibold text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)]"
                autoFocus
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  onDelete(deleteConfirmId);
                  setDeleteConfirmId(null);
                }}
                className="rounded-md bg-[var(--color-status-error)] px-3 py-2 text-xs font-semibold text-white hover:bg-[var(--color-status-error)]/90"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
