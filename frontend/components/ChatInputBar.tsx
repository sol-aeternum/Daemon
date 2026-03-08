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
  onSubmit: (e?: { preventDefault?: () => void }) => void;
  isLoading: boolean;
  // Cloud/Local toggle props
  isLocal?: boolean;
  onToggleLocal?: () => void;
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
  isLocal = false,
  onToggleLocal,
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
      if (!input.trim() || isLoading) return;
      onSubmit(e);
    }
  };

  return (
    <div className="w-full max-w-3xl mx-auto p-4">
      {/* Unified input container */}
      <div className="relative bg-[var(--color-bg-secondary)] border border-[var(--color-border-primary)] rounded-2xl shadow-md hover:shadow-lg focus-within:shadow-lg focus-within:border-[var(--color-border-secondary)] transition-all duration-200">
        {/* Top row: Controls */}
        <div className="flex items-center gap-2 px-3 pt-3 pb-2 border-b border-[var(--color-border-muted)]">
          {/* Left: Model selector pill */}
          <div className="flex min-w-0 flex-wrap items-center gap-2 pb-1 overflow-visible">
            <ModelSelector selected={selectedModel} onSelect={onSelectModel} />
            
            {/* Cloud/Local toggle */}
            {onToggleLocal && (
              <div className="flex min-h-[44px] items-center gap-1.5 rounded-md border border-[var(--color-border-muted)] bg-[var(--color-bg-tertiary)] px-2 py-1">
                <span className={`hidden text-[10px] font-medium transition-colors sm:inline ${!isLocal ? 'text-[var(--color-text-primary)]' : 'text-[var(--color-text-muted)]'}`}>
                  Cloud
                </span>
                <button
                  type="button"
                  disabled
                  onClick={onToggleLocal}
                  aria-label="Local pipeline coming soon"
                  className="relative inline-flex h-6 w-10 cursor-not-allowed items-center rounded-full bg-[var(--color-border-primary)] opacity-70 focus:outline-none"
                  title="Local pipeline coming soon"
                >
                  <span className={`${isLocal ? 'translate-x-5' : 'translate-x-1'} inline-block h-4 w-4 transform rounded-full bg-[var(--color-bg-secondary)] transition-transform duration-200`} />
                </button>
                <span className={`hidden text-[10px] font-medium transition-colors sm:inline ${isLocal ? 'text-[var(--color-text-primary)]' : 'text-[var(--color-text-muted)]'}`}>
                  Local
                </span>
              </div>
            )}
          </div>
          
          {/* Spacer */}
          <div className="flex-1" />
          
          {/* Attachment button (compact) */}
          <button
            type="button"
            aria-label="Attach file"
            className="min-h-[44px] min-w-[44px] rounded-md p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            title="Attach file (coming soon)"
          >
            <Paperclip className="w-4 h-4" />
          </button>
        </div>
        
        {/* Bottom row: Input and actions */}
        <div className="flex items-end gap-2 p-3">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={onInputChange}
            onKeyDown={handleKeyDown}
            placeholder="Message Daemon..."
            rows={1}
            className="flex-1 bg-transparent text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] resize-none focus:outline-none py-2 max-h-[200px] overflow-y-auto scrollbar-thin scrollbar-thumb-[var(--color-border-secondary)] scrollbar-track-transparent"
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
              aria-label="Send message"
              disabled={!input.trim() || isLoading}
              className={`min-h-[44px] min-w-[44px] rounded-xl p-2 transition-all duration-200 ${
                input.trim() && !isLoading
                  ? "bg-[var(--color-accent-primary)] text-white hover:bg-[var(--color-accent-hover)] shadow-sm"
                  : "bg-transparent text-[var(--color-text-muted)] cursor-not-allowed"
              }`}
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
      
      {/* Disclaimer */}
      <div className="text-center mt-2 text-xs text-[var(--color-text-muted)]">
        Daemon can make mistakes. Consider checking important information.
      </div>
    </div>
  );
}
