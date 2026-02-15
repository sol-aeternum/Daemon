"use client";

import { useEffect, useRef, useState } from "react";
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
  onSendMessage: (message: string) => void;
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
  onSendMessage,
  isLoading,
}: ChatInputBarProps) {
  const [input, setInput] = useState("");
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

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSend = () => {
    if (!input.trim() || isLoading) return;
    onSendMessage(input);
    setInput("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  return (
    <div className="w-full max-w-3xl mx-auto p-4">
      <div className="mb-2 flex justify-start">
        <ModelSelector selected={selectedModel} onSelect={onSelectModel} />
      </div>

      <div className="relative flex items-end gap-2 p-3 bg-gpt-input rounded-xl border border-gray-600/50 shadow-lg focus-within:border-gray-500/80 transition-colors">
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
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Send a message..."
          rows={1}
          className="flex-1 bg-transparent text-gpt-text-primary placeholder-gray-400 resize-none focus:outline-none py-2 max-h-[200px] overflow-y-auto scrollbar-thin scrollbar-thumb-gray-600 scrollbar-track-transparent"
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
            onClick={handleSend}
            disabled={!input.trim() || isLoading}
            className={`p-2 rounded-md transition-all duration-200 ${
              input.trim() && !isLoading
                ? "bg-gpt-accent text-white hover:bg-opacity-90 shadow-sm"
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
