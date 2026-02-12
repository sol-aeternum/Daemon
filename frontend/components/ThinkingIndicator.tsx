"use client";

import { useState, useRef, useEffect } from "react";
import { ChatEvent } from "../lib/events";


interface ThinkingIndicatorProps {
  event?: ChatEvent;
  isThinking: boolean;
  isFinished?: boolean;
  duration?: number;
  onDurationChange?: (duration: number) => void;
}

export function ThinkingIndicator({ event, isThinking, isFinished, duration: initialDuration, onDurationChange }: ThinkingIndicatorProps) {
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
  
  if (!isThinking && !event && !isFinished) return null;

  const content = event?.type === "thinking" ? event.content : "";
  const agent = event?.type === "thinking" ? event.agent : undefined;
  
  return (
    <div className="my-2 rounded-lg overflow-hidden border border-gray-200/50">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-3 py-2 flex items-center gap-2 text-left hover:bg-gray-50 transition-colors group"
      >
        <div className="flex items-center gap-2 text-gray-500">
            {isThinking ? (
               <div className="relative flex items-center justify-center w-4 h-4">
                 <div className="absolute w-full h-full border-2 border-gray-300 border-t-gray-600 rounded-full animate-spin"></div>
               </div>
            ) : (
               <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                 <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
               </svg>
            )}
            <span className="text-xs font-medium">
              {isThinking ? "Thinking" : `Thought for ${duration}s`}
            </span>
        </div>
        
        <div className="flex-1" />
        
        <svg
          className={`w-3 h-3 text-gray-400 transition-transform ${isExpanded ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {content && isExpanded && (
        <div className="px-3 pb-3 pt-1 bg-gray-50/50 border-t border-gray-100">
           <div className="text-xs text-gray-600 font-mono whitespace-pre-wrap leading-relaxed">
             {content}
           </div>
        </div>
      )}
    </div>
  );
}
