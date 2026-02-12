"use client";

import { useEffect, useRef, useMemo } from "react";
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
  const { isPlaying, isConnecting, startStreaming, stop, sendTextChunk, flushBuffer, isStreaming: isWsStreaming } = useStreamingTts({
    voice,
    model,
    speed,
    onError: (err) => console.error("TTS Error:", err),
  });

  const startedRef = useRef(false);
  const lastSentLengthRef = useRef(0);
  const formattedText = useMemo(() => text || "", [text]);

  useEffect(() => {
    if (!enabled) return;
    if (!isStreaming) return;
    if (startedRef.current || isConnecting || isWsStreaming) return;
    startStreaming();
    startedRef.current = true;
  }, [enabled, isStreaming, isConnecting, isWsStreaming, startStreaming]);

  useEffect(() => {
    if (!startedRef.current) return;
    if (!isWsStreaming) return;
    if (!formattedText) return;
    const newLength = formattedText.length;
    const prevLength = lastSentLengthRef.current;
    if (newLength <= prevLength) return;
    const delta = formattedText.slice(prevLength);
    sendTextChunk(delta);
    lastSentLengthRef.current = newLength;
  }, [formattedText, isWsStreaming, sendTextChunk]);

  useEffect(() => {
    if (!startedRef.current) return;
    if (!isWsStreaming) return;
    if (!formattedText) return;
    if (lastSentLengthRef.current >= formattedText.length) return;
    const delta = formattedText.slice(lastSentLengthRef.current);
    sendTextChunk(delta);
    lastSentLengthRef.current = formattedText.length;
  }, [isWsStreaming, formattedText, sendTextChunk]);

  useEffect(() => {
    if (!isStreaming && startedRef.current) {
      flushBuffer();
    }
  }, [isStreaming, flushBuffer]);

  useEffect(() => {
    return () => {
      if (startedRef.current) {
        stop();
      }
    };
  }, [stop]);

  if (!enabled) return null;

  const handleClick = () => {
    if (isPlaying || isConnecting || isWsStreaming) {
      stop();
      startedRef.current = false;
      lastSentLengthRef.current = 0;
      return;
    }
    startStreaming();
    startedRef.current = true;
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
