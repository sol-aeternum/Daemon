"use client";

import { useEffect, useRef, useState } from "react";
import { Paperclip, Send, X } from "lucide-react";
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
  attachments?: Array<{ id: string; name: string; size: number }>;
  onAttachFiles?: (files: FileList) => void;
  onRemoveAttachment?: (id: string) => void;
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
  attachments = [],
  onAttachFiles,
  onRemoveAttachment,
}: ChatInputBarProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);

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
      if ((!input.trim() && attachments.length === 0) || isLoading) return;
      onSubmit(e);
    }
  };

  const handleAttachmentClick = () => {
    fileInputRef.current?.click();
  };

  const handleFilesSelected = (event: React.ChangeEvent<HTMLInputElement>) => {
    const { files } = event.target;
    if (files && files.length > 0) {
      onAttachFiles?.(files);
    }
    event.target.value = "";
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const hasDraggedFiles = (event: React.DragEvent<HTMLDivElement>) => {
    const { types } = event.dataTransfer;
    return Array.from(types).includes("Files");
  };

  const handleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (!isDragOver) {
      setIsDragOver(true);
    }
  };

  const handleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    const relatedTarget = event.relatedTarget as Node | null;
    if (relatedTarget && event.currentTarget.contains(relatedTarget)) {
      return;
    }
    setIsDragOver(false);
  };

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    setIsDragOver(false);
    const { files } = event.dataTransfer;
    if (files && files.length > 0) {
      onAttachFiles?.(files);
    }
  };

  return (
    <div className="w-full max-w-3xl mx-auto p-4">
      {/* Unified input container */}
      <div
        onDragOver={handleDragOver}
        onDragEnter={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`relative bg-[var(--color-bg-secondary)] border rounded-2xl shadow-md hover:shadow-lg focus-within:shadow-lg transition-all duration-200 ${
          isDragOver
            ? "border-[var(--color-accent-primary)] ring-2 ring-[var(--color-accent-primary)]/25"
            : "border-[var(--color-border-primary)] focus-within:border-[var(--color-border-secondary)]"
        }`}
      >
        {isDragOver && (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-2xl bg-[var(--color-bg-tertiary)]/85 backdrop-blur-[1px]">
            <div className="rounded-lg border border-[var(--color-accent-primary)]/40 bg-[var(--color-bg-secondary)] px-4 py-2 text-sm font-medium text-[var(--color-text-primary)] shadow-sm">
              Drop files to attach
            </div>
          </div>
        )}
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
            onClick={handleAttachmentClick}
            aria-label="Attach file"
            className="min-h-[44px] min-w-[44px] rounded-md p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
            title="Attach file"
          >
            <Paperclip className="w-4 h-4" />
          </button>
        </div>

        {attachments.length > 0 && (
          <div className="px-3 pt-2 flex flex-wrap gap-2 border-b border-[var(--color-border-muted)]">
            {attachments.map((attachment) => (
              <div
                key={attachment.id}
                className="inline-flex items-center gap-2 rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] px-2 py-1 text-xs text-[var(--color-text-secondary)]"
              >
                <span className="max-w-[180px] truncate">{attachment.name}</span>
                <span className="text-[var(--color-text-muted)]">{formatFileSize(attachment.size)}</span>
                <button
                  type="button"
                  onClick={() => onRemoveAttachment?.(attachment.id)}
                  className="rounded p-0.5 text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)]"
                  aria-label={`Remove ${attachment.name}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        
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
              disabled={(!input.trim() && attachments.length === 0) || isLoading}
              className={`min-h-[44px] min-w-[44px] rounded-xl p-2 transition-all duration-200 ${
                (input.trim() || attachments.length > 0) && !isLoading
                  ? "bg-[var(--color-accent-primary)] text-white hover:bg-[var(--color-accent-hover)] shadow-sm"
                  : "bg-transparent text-[var(--color-text-muted)] cursor-not-allowed"
              }`}
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFilesSelected}
        />
      </div>
      
      {/* Disclaimer */}
      <div className="text-center mt-2 text-xs text-[var(--color-text-muted)]">
        Daemon can make mistakes. Consider checking important information.
      </div>
    </div>
  );
}
