"use client";

import { Conversation } from "../hooks/useConversationHistory";
import { useLocalStorage } from "../hooks/useLocalStorage";

interface ConversationListProps {
  conversations: Conversation[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onNewChat: () => void;
  className?: string;
}

export function ConversationList({
  conversations,
  currentId,
  onSelect,
  onDelete,
  onNewChat,
  className = "",
}: ConversationListProps) {
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

  return (
    <div className={`w-full md:w-[280px] bg-gray-50 border-r flex flex-col h-full ${className}`}>
      <div className="p-4 border-b pt-[max(1rem,env(safe-area-inset-top))]">
        <button
          onClick={onNewChat}
          className="w-full bg-blue-600 text-white px-4 py-3 rounded-lg font-medium hover:bg-blue-700 transition-colors flex items-center justify-center gap-2 min-h-[44px] touch-manipulation"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          New Chat
        </button>
      </div>

      <div className="px-4 pb-4 border-b">
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

      <div className="flex-1 overflow-y-auto pb-[env(safe-area-inset-bottom)]">
        {conversations.length === 0 ? (
          <div className="p-4 text-center text-gray-500 text-sm">
            No conversations yet
          </div>
        ) : (
          <div className="divide-y">
            {conversations.map((conv) => (
              <div
                key={conv.id}
                className={`group p-3 cursor-pointer hover:bg-gray-100 transition-colors min-h-[60px] flex items-center ${
                  currentId === conv.id ? "bg-blue-50 hover:bg-blue-100" : ""
                }`}
                onClick={() => onSelect(conv.id)}
              >
                <div className="flex items-start justify-between w-full">
                  <div className="flex-1 min-w-0 pr-2">
                    <p className="text-sm font-medium text-gray-900 truncate">
                      {conv.title || "New conversation"}
                    </p>
                    <p className="text-xs text-gray-500 mt-1">
                      {new Date(conv.updatedAt).toLocaleDateString()}
                    </p>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(conv.id);
                    }}
                    className="opacity-100 md:opacity-0 md:group-hover:opacity-100 p-2 text-gray-400 hover:text-red-600 transition-opacity min-h-[44px] min-w-[44px] flex items-center justify-center -mr-2"
                    title="Delete conversation"
                    aria-label="Delete conversation"
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                      />
                    </svg>
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
