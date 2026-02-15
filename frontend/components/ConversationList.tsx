"use client";

import { useState, useMemo } from "react";
import { Conversation } from "../hooks/useConversationHistory";
import { useLocalStorage } from "../hooks/useLocalStorage";
import { useError } from "./ErrorProvider";
import { MoreHorizontal, Pin, Trash2, Edit2, MessageSquare, Search } from "lucide-react";

interface ConversationListProps {
  conversations: Conversation[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onUpdate: (id: string, updates: Partial<Conversation>) => void;
  onNewChat: () => void;
  className?: string;
  sttSettings?: {
    language: string;
    enablePartials: boolean;
  };
  setSttSettings?: (settings: { language: string; enablePartials: boolean }) => void;
  searchQuery: string;
  setSearchQuery: (query: string) => void;
}

export function ConversationList({
  conversations,
  currentId,
  onSelect,
  onDelete,
  onUpdate,
  onNewChat,
  className = "",
  sttSettings,
  setSttSettings,
  searchQuery,
  setSearchQuery,
}: ConversationListProps) {
  const { showError } = useError();
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isDeletingMemories, setIsDeletingMemories] = useState(false);
  const [deleteResult, setDeleteResult] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState("");

  const defaultTtsSettings = {
    enabled: true,
    voice: "Xb7hH8MSUJpSbSDYk0k2",
    model: "eleven_flash_v2_5",
    speed: 1.0,
    format: "mp3",
  };
  const { value: ttsSettings, setValue: setTtsSettings } = useLocalStorage(
    "tts_settings",
    defaultTtsSettings
  );
  const effectiveTtsSettings = ttsSettings || defaultTtsSettings;
  const apiUrls = [process.env.NEXT_PUBLIC_API_URL, "http://localhost:8000"].filter(
    Boolean
  ) as string[];
  const apiBaseUrl = apiUrls[0] ?? "http://localhost:8000";

  const handleDeleteAllMemories = async () => {
    setIsDeletingMemories(true);
    setDeleteResult(null);
    try {
      const response = await fetch(`${apiBaseUrl}/memories?confirm=true&hard=true`, {
        method: "DELETE",
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.detail || "Failed to delete memories");
      }
      setDeleteResult(`Deleted ${data.deleted ?? 0} memories`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to delete memories";
      showError(message);
    } finally {
      setIsDeletingMemories(false);
      setShowDeleteConfirm(false);
    }
  };

  const pinnedConversations = useMemo(() => 
    conversations.filter(c => c.pinned), 
    [conversations]
  );
  
  const unpinnedConversations = useMemo(() => 
    conversations.filter(c => !c.pinned), 
    [conversations]
  );

  const handleRename = (id: string, newTitle: string) => {
    onUpdate(id, { title: newTitle, title_locked: true });
    setEditingId(null);
  };

  const togglePin = (e: React.MouseEvent, id: string, currentPinned: boolean) => {
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
      className={`group relative p-3 cursor-pointer hover:bg-gray-100 transition-colors min-h-[60px] flex items-center ${
        currentId === conv.id ? "bg-blue-50 hover:bg-blue-100" : ""
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
                if (e.key === "Enter") handleRename(conv.id, editTitle);
                if (e.key === "Escape") setEditingId(null);
              }}
              onClick={(e) => e.stopPropagation()}
              className="w-full px-1 py-0.5 text-sm border rounded focus:outline-none focus:border-blue-500"
            />
          ) : (
            <>
              <div className="flex items-center gap-2">
                {conv.pinned && <Pin className="w-3 h-3 text-blue-500 flex-shrink-0" />}
                <p className="text-sm font-medium text-gray-900 truncate">
                  {conv.title || "New conversation"}
                </p>
              </div>
              <p className="text-xs text-gray-500 mt-1 truncate">
                {new Date(conv.updatedAt).toLocaleDateString()}
              </p>
            </>
          )}
        </div>

        <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center">
          <button
            onClick={(e) => {
              e.stopPropagation();
              setMenuOpenId(menuOpenId === conv.id ? null : conv.id);
            }}
            className={`p-1.5 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-all ${
              menuOpenId === conv.id ? "opacity-100 bg-gray-200" : "opacity-0 group-hover:opacity-100"
            }`}
          >
            <MoreHorizontal className="w-4 h-4" />
          </button>

          {menuOpenId === conv.id && (
            <>
              <div 
                className="fixed inset-0 z-40"
                onClick={(e) => {
                  e.stopPropagation();
                  setMenuOpenId(null);
                }}
              />
              <div className="absolute right-0 top-full mt-1 w-32 bg-white rounded-lg shadow-lg border border-gray-100 py-1 z-50">
              <button
                onClick={(e) => togglePin(e, conv.id, conv.pinned)}
                className="w-full px-3 py-2 text-left text-xs text-gray-700 hover:bg-gray-50 flex items-center gap-2"
              >
                <Pin className="w-3 h-3" />
                {conv.pinned ? "Unpin" : "Pin"}
              </button>
              <button
                onClick={(e) => startRename(e, conv)}
                className="w-full px-3 py-2 text-left text-xs text-gray-700 hover:bg-gray-50 flex items-center gap-2"
              >
                <Edit2 className="w-3 h-3" />
                Rename
              </button>
              <button
                onClick={(e) => confirmDelete(e, conv.id)}
                className="w-full px-3 py-2 text-left text-xs text-red-600 hover:bg-red-50 flex items-center gap-2"
              >
                <Trash2 className="w-3 h-3" />
                Delete
              </button>
            </div>
          </>
          )}
        </div>
      </div>
    </div>
  );

  return (
    <div className={`w-full md:w-[280px] bg-gray-50 border-r flex flex-col h-full ${className}`}>
      <div className="p-4 border-b pt-[max(1rem,env(safe-area-inset-top))] space-y-3">
        <button
          onClick={onNewChat}
          className="w-full bg-blue-600 text-white px-4 py-3 rounded-lg font-medium hover:bg-blue-700 transition-colors flex items-center justify-center gap-2 min-h-[44px] touch-manipulation"
        >
          <MessageSquare className="w-5 h-5" />
          New Chat
        </button>
        
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search conversations..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-2 text-sm bg-white border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto pb-[env(safe-area-inset-bottom)]">
        {conversations.length === 0 ? (
          <div className="p-4 text-center text-gray-500 text-sm">
            No conversations found
          </div>
        ) : (
          <div className="divide-y divide-gray-100">
            {pinnedConversations.length > 0 && (
              <>
                <div className="px-4 py-2 bg-gray-100/50 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                  Pinned
                </div>
                {pinnedConversations.map((conv) => (
                  <ConversationItem key={conv.id} conv={conv} />
                ))}
              </>
            )}
            
            {unpinnedConversations.length > 0 && (
              <>
                {pinnedConversations.length > 0 && (
                  <div className="px-4 py-2 bg-gray-100/50 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                    Recent
                  </div>
                )}
                {unpinnedConversations.map((conv) => (
                  <ConversationItem key={conv.id} conv={conv} />
                ))}
              </>
            )}
          </div>
        )}
      </div>

      <div className="border-t bg-white">
        <div className="px-4 py-3 border-b">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
              Speech To Text
            </p>
          </div>
          
          {sttSettings && setSttSettings && (
            <div className="space-y-3 text-xs text-gray-700">
              <div>
                <label className="block mb-1 text-gray-500">Language</label>
                <select
                  className="w-full rounded-md border border-gray-200 bg-white px-2 py-1"
                  value={sttSettings.language}
                  onChange={(e) =>
                    setSttSettings({
                      ...sttSettings,
                      language: e.target.value,
                    })
                  }
                >
                  <option value="en">English</option>
                  <option value="es">Spanish</option>
                  <option value="fr">French</option>
                  <option value="de">German</option>
                  <option value="it">Italian</option>
                  <option value="pt">Portuguese</option>
                  <option value="pl">Polish</option>
                  <option value="hi">Hindi</option>
                  <option value="ja">Japanese</option>
                  <option value="zh">Chinese</option>
                </select>
              </div>

              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="stt-partials"
                  className="h-4 w-4 accent-blue-600"
                  checked={sttSettings.enablePartials}
                  onChange={(e) =>
                    setSttSettings({
                      ...sttSettings,
                      enablePartials: e.target.checked,
                    })
                  }
                />
                <label htmlFor="stt-partials" className="text-gray-600">
                  Show partial results
                </label>
              </div>
            </div>
          )}
        </div>

        <div className="px-4 py-3 border-b">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
              Text To Speech
            </p>
            <label className="inline-flex items-center gap-2 text-xs text-gray-600">
              <input
                type="checkbox"
                className="h-4 w-4 accent-blue-600"
                checked={Boolean(effectiveTtsSettings.enabled)}
                onChange={(e) =>
                  setTtsSettings({
                    ...effectiveTtsSettings,
                    enabled: e.target.checked,
                  })
                }
              />
              Enabled
            </label>
          </div>

          <div className="space-y-3 text-xs text-gray-700">
            <div>
              <label className="block mb-1 text-gray-500">Voice</label>
              <select
                className="w-full rounded-md border border-gray-200 bg-white px-2 py-1"
                value={effectiveTtsSettings.voice}
                onChange={(e) =>
                  setTtsSettings({
                    ...effectiveTtsSettings,
                    voice: e.target.value,
                  })
                }
              >
                {[
                  { id: "Xb7hH8MSUJpSbSDYk0k2", name: "Adam" },
                  { id: "XB0fDUnXU5powFXDhCwa", name: "Bella" },
                  { id: "N2lVS1w4EtoT3dr4eOWO", name: "Callum" },
                  { id: "IKne3meq5aSn9XLyUdCD", name: "Josh" },
                  { id: "21m00Tcm4TlvDq8ikWAM", name: "Rachel" },
                  { id: "AZnzlk1XvdvUeBnXmlld", name: "Domi" },
                  { id: "EXAVITQu4vr4xnSDxMaL", name: "Bella (Alt)" },
                  { id: "MF3mGyEYCl7XYWbV9V6O", name: "Antoni" },
                  { id: "TxGEqnHWrfWFTfGW9XjX", name: "Thomas" },
                  { id: "VR6AewLTigWG4xSOukaG", name: "Liam" },
                ].map((voice) => (
                  <option key={voice.id} value={voice.id}>
                    {voice.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block mb-1 text-gray-500">Model</label>
              <input
                className="w-full rounded-md border border-gray-200 bg-white px-2 py-1"
                value={effectiveTtsSettings.model}
                onChange={(e) =>
                  setTtsSettings({
                    ...effectiveTtsSettings,
                    model: e.target.value,
                  })
                }
              />
            </div>

            <div>
              <label className="block mb-1 text-gray-500">Speed</label>
              <input
                type="range"
                min={0.5}
                max={2}
                step={0.1}
                value={effectiveTtsSettings.speed}
                onChange={(e) =>
                  setTtsSettings({
                    ...effectiveTtsSettings,
                    speed: Number(e.target.value),
                  })
                }
                className="w-full"
              />
              <div className="mt-1 text-gray-500">{effectiveTtsSettings.speed.toFixed(1)}x</div>
            </div>

            <div>
              <label className="block mb-1 text-gray-500">Format</label>
              <select
                className="w-full rounded-md border border-gray-200 bg-white px-2 py-1"
                value={effectiveTtsSettings.format}
                onChange={(e) =>
                  setTtsSettings({
                    ...effectiveTtsSettings,
                    format: e.target.value,
                  })
                }
              >
                {[
                  { value: "mp3", label: "MP3" },
                  { value: "wav", label: "WAV" },
                  { value: "ogg", label: "OGG" },
                ].map((fmt) => (
                  <option key={fmt.value} value={fmt.value}>
                    {fmt.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div className="px-4 py-3">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Memory</p>
          </div>
          <button
            type="button"
            onClick={() => setShowDeleteConfirm(true)}
            className="w-full rounded-md bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700 transition-colors min-h-[40px]"
          >
            Clear All Memories
          </button>
          {deleteResult && (
            <p className="mt-2 text-xs text-green-700" role="status">
              {deleteResult}
            </p>
          )}
        </div>
      </div>

      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-sm rounded-lg bg-white p-4 shadow-xl">
            <h3 className="text-sm font-semibold text-gray-900">Clear All Memories</h3>
            <p className="mt-2 text-xs text-gray-600">
              This will permanently delete all stored memories. This cannot be undone.
            </p>
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowDeleteConfirm(false)}
                className="rounded-md border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-50"
                autoFocus
                disabled={isDeletingMemories}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDeleteAllMemories}
                className="rounded-md bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-60"
                disabled={isDeletingMemories}
              >
                {isDeletingMemories ? "Deleting..." : "Delete All Memories"}
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteConfirmId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-sm rounded-lg bg-white p-4 shadow-xl">
            <h3 className="text-sm font-semibold text-gray-900">Delete Conversation</h3>
            <p className="mt-2 text-xs text-gray-600">
              Are you sure you want to delete this conversation? This cannot be undone.
            </p>
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setDeleteConfirmId(null)}
                className="rounded-md border border-gray-200 px-3 py-2 text-xs font-semibold text-gray-700 hover:bg-gray-50"
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
                className="rounded-md bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700"
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
