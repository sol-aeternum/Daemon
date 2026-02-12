"use client";

import { useEffect } from "react";
import { useStreamingTts } from "../hooks/useStreamingTts";
import { Volume2, VolumeX } from "lucide-react";

interface StreamingTtsMessageProps {
  messageId: string;
  text: string;
  isStreaming: boolean;
  enabled: boolean;
  voice?: string;
  model?: string;
  speed?: number;
}

export function StreamingTtsMessage({
  messageId,
  text,
  isStreaming,
  enabled,
  voice,
  model,
  speed,
}: StreamingTtsMessageProps) {
  const { isPlaying, isConnecting, startStreaming, stop, sendTextChunk, flushBuffer } = useStreamingTts({
    voice,
    model,
    speed,
    onError: (err) => console.error("TTS Error:", err),
  });

  useEffect(() => {
    if (enabled && isStreaming && !isPlaying && !isConnecting) {
      startStreaming();
    }
  }, [enabled, isStreaming, isPlaying, isConnecting, startStreaming]);

  useEffect(() => {
    if (isPlaying && text) {
      sendTextChunk(text);
    }
  }, [text, isPlaying, sendTextChunk]);

  useEffect(() => {
    if (!isStreaming && isPlaying) {
      flushBuffer();
    }
  }, [isStreaming, isPlaying, flushBuffer]);

  useEffect(() => {
    return () => {
      if (isPlaying) {
        stop();
      }
    };
  }, [isPlaying, stop]);

  if (!enabled) return null;

  const handleClick = () => {
    if (isPlaying || isConnecting) {
      stop();
    } else {
      startStreaming();
    }
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      className="ml-2 inline-flex items-center gap-1 rounded px-1 py-0.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
      title={isPlaying ? "Stop TTS" : isConnecting ? "Connecting..." : "Play TTS"}
    >
      {isConnecting ? (
        <span className="animate-pulse">Connecting...</span>
      ) : isPlaying ? (
        <VolumeX className="h-3.5 w-3.5" />
      ) : (
        <Volume2 className="h-3.5 w-3.5" />
      )}
    </button>
  );
}
