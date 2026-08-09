'use client';

import { Mic, Square } from 'lucide-react';

interface MicButtonProps {
  isRecording: boolean;
  isConnecting: boolean;
  start: () => Promise<void>;
  stop: () => void;
  disabled?: boolean;
  error?: Error | null;
}

export function MicButton({
  isRecording,
  isConnecting,
  start,
  stop,
  disabled,
  error,
}: MicButtonProps) {
  const handleMouseDown = async () => {
    if (disabled || isRecording) return;
    await start();
  };

  const handleMouseUp = () => {
    if (isRecording) {
      stop();
    }
  };

  const handleTouchStart = async (e: React.TouchEvent) => {
    e.preventDefault();
    if (disabled || isRecording) return;
    await start();
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    e.preventDefault();
    if (isRecording) {
      stop();
    }
  };

  return (
    <div className="flex items-center gap-2 relative">
      {isRecording && (
        <div className="absolute inset-0 rounded-full animate-ping bg-[var(--color-status-error)] opacity-75 pointer-events-none" />
      )}

      {(isRecording || isConnecting) && (
        <span className="absolute -top-8 left-1/2 -translate-x-1/2 whitespace-nowrap text-xs bg-black/75 text-white px-2 py-1 rounded pointer-events-none">
          {isRecording ? 'Listening...' : 'Connecting...'}
        </span>
      )}

      {error && (
        <span className="absolute -top-8 left-1/2 -translate-x-1/2 whitespace-nowrap rounded border border-[var(--color-status-error)]/30 bg-[var(--color-bg-secondary)] px-2 py-1 text-xs text-[var(--color-status-error)] shadow-sm pointer-events-none">
          {error.message}
        </span>
      )}

      <button
        type="button"
        disabled={disabled || isConnecting}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
        title={isRecording ? 'Release to stop' : 'Hold to speak'}
        className={`relative z-10 min-h-[44px] min-w-[44px] rounded-full p-2 transition-all duration-200 ${
          isRecording
            ? 'bg-[var(--color-status-error)] text-white scale-110 shadow-lg'
            : isConnecting
              ? 'bg-[var(--color-status-warning)] text-white'
              : 'bg-[var(--color-text-muted)] text-[var(--color-text-primary)] hover:bg-[var(--color-text-secondary)] hover:scale-105'
        } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
      >
        {isRecording ? (
          <Square className="h-4 w-4 fill-current" />
        ) : (
          <Mic className="h-4 w-4" />
        )}
      </button>
    </div>
  );
}
