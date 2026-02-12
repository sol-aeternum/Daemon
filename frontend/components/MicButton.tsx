"use client";

import { useState, useCallback } from "react";
import { Mic, Square } from "lucide-react";
import { useStt } from "../hooks/useStt";

interface MicButtonProps {
  onTranscript: (text: string) => void;
  disabled?: boolean;
}

export function MicButton({ onTranscript, disabled }: MicButtonProps) {
  const [partialText, setPartialText] = useState("");
  
  const handlePartial = useCallback((text: string) => {
    setPartialText(text);
  }, []);
  
  const handleFinal = useCallback((text: string) => {
    setPartialText("");
    onTranscript(text);
  }, [onTranscript]);
  
  const { isRecording, isConnecting, start, stop, error } = useStt({
    onPartialTranscript: handlePartial,
    onTranscript: handleFinal,
  });

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
    <div className="flex items-center gap-2">
      {partialText && (
        <span className="text-sm text-muted-foreground">
          {partialText}...
        </span>
      )}
      {error && (
        <span className="text-xs text-red-500">
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
        title={isRecording ? "Release to stop" : "Hold to speak"}
        className={`rounded-full p-2 transition-all ${
          isRecording
            ? "bg-red-500 text-white animate-pulse"
            : isConnecting
            ? "bg-yellow-500 text-white"
            : "bg-muted text-muted-foreground hover:bg-muted/80"
        } ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
      >
        {isRecording ? (
          <Square className="h-4 w-4" />
        ) : (
          <Mic className="h-4 w-4" />
        )}
      </button>
    </div>
  );
}
