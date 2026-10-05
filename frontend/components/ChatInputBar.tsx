'use client';

import { useEffect, useRef, useState } from 'react';
import { Mic, Paperclip, Send, X } from 'lucide-react';
import { ModelSelector } from './ModelSelector';
import { StopButton } from './StopButton';

const MAX_TEXTAREA_HEIGHT = 200;

interface ChatInputBarProps {
  selectedModel: string;
  onSelectModel: (modelId: string) => void;
  /* Microphone props are retained in the prop API for compatibility;
     voice input is currently presented as unavailable. */
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
  onStop: () => void;
  attachments?: Array<{ id: string; name: string; size: number }>;
  onAttachFiles?: (files: FileList) => void;
  onRemoveAttachment?: (id: string) => void;
}

const VOICE_UNAVAILABLE_TEXT =
  'Voice input is unavailable in the current runtime. Use the text composer.';

export function ChatInputBar({
  selectedModel,
  onSelectModel,
  input,
  onInputChange,
  onSubmit,
  isLoading,
  onStop,
  attachments = [],
  onAttachFiles,
  onRemoveAttachment,
}: ChatInputBarProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [notice, setNotice] = useState('');
  const noticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(
        textareaRef.current.scrollHeight,
        MAX_TEXTAREA_HEIGHT,
      )}px`;
    }
  }, [input]);

  useEffect(() => {
    return () => {
      if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current);
    };
  }, []);

  const announceNotice = (text: string) => {
    if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current);
    setNotice(text);
    noticeTimerRef.current = setTimeout(() => {
      setNotice('');
      noticeTimerRef.current = null;
    }, 5000);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // IME-safe Enter: composing keystrokes (isComposing, or the legacy
    // keyCode 229 "process" event) insert text instead of submitting.
    const native = e.nativeEvent as KeyboardEvent;
    const isComposing =
      native.isComposing === true ||
      (native as KeyboardEvent & { keyCode?: number }).keyCode === 229;
    if (e.key === 'Enter' && !e.shiftKey && !isComposing) {
      e.preventDefault();
      if (isLoading) {
        announceNotice(
          'The response is still streaming. Stop it or wait — your draft is kept.',
        );
        return;
      }
      if (!isLoading && !input.trim() && attachments.length === 0) return;
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
    event.target.value = '';
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const hasDraggedFiles = (event: React.DragEvent<HTMLDivElement>) => {
    const { types } = event.dataTransfer;
    return Array.from(types).includes('Files');
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
    <div className="w-full max-w-composer mx-auto p-4">
      {/* Unified input container */}
      <div
        onDragOver={handleDragOver}
        onDragEnter={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`relative bg-[var(--color-bg-secondary)] border rounded-2xl shadow-md hover:shadow-lg focus-within:shadow-lg transition-all duration-200 ${
          isDragOver
            ? 'border-[var(--color-accent-primary)] ring-2 ring-[var(--color-accent-primary)]/25'
            : 'border-[var(--color-border-primary)] focus-within:border-[var(--color-border-accent)]'
        }`}
      >
        {isDragOver && (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-2xl bg-[var(--color-bg-tertiary)]/85 backdrop-blur-subtle">
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
          </div>

          {/* Spacer */}
          <div className="flex-1" />

          {/* Attachment button (compact) */}
          <button
            type="button"
            onClick={handleAttachmentClick}
            aria-label="Attach file"
            className="min-h-touch min-w-touch rounded-md p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-primary)] focus-visible:text-[var(--color-text-primary)]"
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
                <span className="max-w-attachment-label truncate">
                  {attachment.name}
                </span>
                <span className="text-[var(--color-text-muted)]">
                  {formatFileSize(attachment.size)}
                </span>
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
            placeholder="Ask a question or describe what you need…"
            aria-label="Message Daemon"
            rows={1}
            className="flex-1 bg-transparent text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] resize-none focus:outline-none focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--color-border-focus)] py-2 max-h-composer overflow-y-auto scrollbar-thin scrollbar-thumb-[var(--color-border-secondary)] scrollbar-track-transparent"
            style={{ minHeight: '24px' }}
          />

          <div className="flex items-center gap-2 pb-1">
            {/* Voice execution is retired: mount a disabled, explained
                control instead of an enableable microphone launch. */}
            <button
              type="button"
              disabled
              aria-disabled="true"
              aria-label={VOICE_UNAVAILABLE_TEXT}
              title={VOICE_UNAVAILABLE_TEXT}
              className="min-h-touch min-w-touch rounded-full p-2 text-[var(--color-text-muted)] bg-transparent cursor-not-allowed"
            >
              <Mic className="h-4 w-4" aria-hidden="true" />
            </button>

            {isLoading ? (
              <StopButton onStop={onStop} />
            ) : (
              <button
                type="submit"
                aria-label="Send message"
                disabled={!input.trim() && attachments.length === 0}
                className={`min-h-touch min-w-touch rounded-xl p-2 transition-all duration-200 disabled:cursor-not-allowed ${
                  input.trim() || attachments.length > 0
                    ? 'bg-[var(--color-accent-primary)] text-[var(--color-text-on-accent)] hover:bg-[var(--color-accent-hover)] shadow-sm'
                    : 'bg-transparent text-[var(--color-text-muted)]'
                }`}
              >
                <Send className="w-4 h-4" />
              </button>
            )}
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

      {/* Streaming notice: a visible, announced blocked-send explanation */}
      {notice && (
        <p
          role="status"
          aria-live="polite"
          className="text-center mt-1 text-sm text-[var(--color-text-secondary)]"
        >
          {notice}
        </p>
      )}

      {/* Disclaimer */}
      <div className="text-center mt-2 text-xs text-[var(--color-text-muted)]">
        <kbd>Esc</kbd> stop · Daemon can make mistakes. Consider checking
        important information.
      </div>
    </div>
  );
}
