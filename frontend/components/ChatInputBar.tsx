"use client";

import { useEffect, useRef } from "react";
import { Paperclip, Send } from "lucide-react";
import { ModelSelector } from "./ModelSelector";
import { MicButton } from "./MicButton";

const MAX_TEXTAREA_HEIGHT = 200;

interface ChatInputBarProps {
  selectedModel: string;
  onSelectModel: (modelId: string) => void;
  isRecording: boolean;
  isConnecting: boolean;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
  micDisabled?: boolean;
  micError?: Error | null;
  input: string;
  onInputChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  onSubmit: (e: React.FormEvent) => void;
  isLoading: boolean;
}

export function ChatInputBar({
  selectedModel,
  onSelectModel,
  isRecording,
  isConnecting,
  startRecording,
  stopRecording,
  micDisabled,
  micError,
  input,
  onInputChange,
  onSubmit,
  isLoading,
}: ChatInputBarProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(
        textareaRef.current.scrollHeight,
        MAX_TEXTAREA_HEIGHT
      )}px`;
    }
  }, [input]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSubmit(e);
    }
  };

  return (
    <div className="w-full max-w-3xl mx-auto p-4">
      <div className="mb-2 flex justify-start">
        <ModelSelector selected={selectedModel} onSelect={onSelectModel} />
      </div>

      <div className="relative flex items-end gap-2 p-3 bg-white rounded-xl border border-daemon-border-primary shadow-sm focus-within:border-daemon-border-secondary transition-colors">
        <button
          type="button"
          className="p-2 text-gray-400 hover:text-white transition-colors rounded-md hover:bg-gray-700/50"
          title="Attach file (coming soon)"
        >
          <Paperclip className="w-5 h-5" />
        </button>

        <textarea
          ref={textareaRef}
          value={input}
          onChange={onInputChange}
          onKeyDown={handleKeyDown}
          placeholder="Send a message..."
          rows={1}
          className="flex-1 bg-transparent text-daemon-text-primary placeholder-daemon-text-muted resize-none focus:outline-none py-2 max-h-[200px] overflow-y-auto scrollbar-thin scrollbar-thumb-gray-300 scrollbar-track-transparent"
          style={{ minHeight: "24px" }}
        />

        <div className="flex items-center gap-2 pb-1">
          <MicButton
            isRecording={isRecording}
            isConnecting={isConnecting}
            start={startRecording}
            stop={stopRecording}
            disabled={micDisabled || isLoading}
            error={micError}
          />

          <button
            type="submit"
            disabled={!input.trim() || isLoading}
            className={`p-2 rounded-md transition-all duration-200 ${
              input.trim() && !isLoading
                ? "bg-daemon-accent text-white hover:bg-daemon-accent-hover shadow-sm"
                : "bg-transparent text-gray-500 cursor-not-allowed"
            }`}
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
      
      <div className="text-center mt-2 text-xs text-gray-500">
        Daemon can make mistakes. Consider checking important information.
      </div>
    </div>
  );
}
