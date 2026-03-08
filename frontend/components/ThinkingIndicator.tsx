"use client";

import { useState, useRef, useEffect } from "react";
import { ChatEvent } from "../lib/events";


interface ThinkingIndicatorProps {
  event?: ChatEvent;
  isThinking: boolean;
  isFinished?: boolean;
  duration?: number;
  modelName?: string;
  onDurationChange?: (duration: number) => void;
}

export function ThinkingIndicator({ event, isThinking, isFinished, duration: initialDuration, modelName, onDurationChange }: ThinkingIndicatorProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [duration, setDuration] = useState(initialDuration || 0);
  const startTimeRef = useRef<number | null>(null);

  // Sync prop duration if provided (e.g. from history)
  useEffect(() => {
    if (initialDuration !== undefined) {
      setDuration(initialDuration);
    }
  }, [initialDuration]);

  useEffect(() => {
    if (isThinking) {
      if (!startTimeRef.current) startTimeRef.current = Date.now();
      const interval = setInterval(() => {
        const d = Math.floor((Date.now() - (startTimeRef.current || 0)) / 1000);
        setDuration(d);
        onDurationChange?.(d);
      }, 100);
      return () => clearInterval(interval);
    } else if (isFinished && startTimeRef.current) {
      // Final update
      const d = Math.floor((Date.now() - startTimeRef.current) / 1000);
      setDuration(d);
      onDurationChange?.(d);
      startTimeRef.current = null; // Reset
    }
  }, [isThinking, isFinished, onDurationChange]);
  
  // If we have content but no duration (loaded from history), try to estimate or show simple label
  // For now, if duration is 0 and it's finished, we might want to just show "Thought" without seconds if we can't recover it
  // But let's keep it simple. If 0, it just says "0s".
  
  const hasThinkingContent =
    event?.type === "thinking"
    && typeof event.content === "string"
    && event.content.trim().length > 0;

  if (!isThinking && !hasThinkingContent) return null;
  
  const content = hasThinkingContent ? event.content : "";
  
  return (
    <div className="my-2">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="group flex w-full items-center gap-2 px-0 py-1.5 text-left transition-colors"
      >
        <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
            {isThinking ? (
               <div className="relative flex items-center justify-center w-4 h-4">
                 <div className="absolute w-full h-full border-2 border-[var(--color-border-secondary)] border-t-[var(--color-text-secondary)] rounded-full animate-spin"></div>
               </div>
            ) : (
               <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                 <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
               </svg>
            )}
            <span className="text-xs font-medium tracking-wide">
              {modelName ? `${modelName} • Thinking for ${duration}s` : `Thinking for ${duration}s`}
            </span>
        </div>
        
        <div className="flex-1" />
        
        <svg
          className={`w-3 h-3 text-[var(--color-text-muted)] transition-transform ${isExpanded ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {content && isExpanded && (
        <div className="mt-1 pl-3">
          <div className="border-l-2 border-[var(--color-border-primary)]/70 pl-3 text-sm leading-relaxed text-[var(--color-text-secondary)] whitespace-normal break-words">
            {content}
          </div>
        </div>
      )}
    </div>
  );
}
