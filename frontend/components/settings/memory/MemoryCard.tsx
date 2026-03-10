"use client";

import { Memory } from "@/hooks/useMemories";
import { formatRelativeTime } from "@/lib/format";
import { Sparkles, Pencil, Wrench } from "lucide-react";

interface MemoryCardProps {
  memory: Memory;
  onSelect: (memoryId: string) => void;
}

const sourceIcons = {
  extracted: Sparkles,
  manual: Pencil,
  tool: Wrench,
} as const;

function getSourceIcon(sourceType: string) {
  return sourceIcons[sourceType as keyof typeof sourceIcons] || Sparkles;
}

function getConfidenceColor(confidence?: number): string {
  if (confidence === undefined) return "bg-text-muted";
  if (confidence >= 0.8) return "bg-status-success";
  if (confidence >= 0.5) return "bg-status-warning";
  return "bg-status-error";
}

function truncateContent(content: string, maxLines: number = 2): string {
  const lines = content.split("\n").filter((line) => line.trim());
  if (lines.length <= maxLines) {
    return content.length > 120 ? content.slice(0, 120) + "..." : content;
  }
  return lines.slice(0, maxLines).join(" ").slice(0, 120) + "...";
}

export function MemoryCard({ memory, onSelect }: MemoryCardProps) {
  const SourceIcon = getSourceIcon(memory.source_type);
  const confidence = memory.metadata?.confidence as number | undefined;
  const confidenceColor = getConfidenceColor(confidence);

  return (
    <button
      type="button"
      onClick={() => onSelect(memory.id)}
      className="w-full text-left p-4 rounded-lg bg-bg-secondary border border-border-primary
                 hover:bg-bg-tertiary hover:border-border-focus hover:shadow-sm
                 transition-all duration-200 ease-out group"
    >
      <div className="flex items-start gap-3">
        {/* Confidence indicator dot */}
        <div className="flex-shrink-0 mt-1.5">
          <div
            className={`w-2 h-2 rounded-full ${confidenceColor} ring-2 ring-opacity-20 ring-current`}
            aria-label={`Confidence: ${
              confidence === undefined
                ? "unknown"
                : confidence >= 0.8
                  ? "high"
                  : confidence >= 0.5
                    ? "medium"
                    : "low"
            }`}
          />
        </div>

        {/* Content area */}
        <div className="flex-1 min-w-0">
          {/* Content text */}
          <p className="text-sm text-text-primary line-clamp-2 leading-relaxed">
            {truncateContent(memory.content)}
          </p>

          {/* Meta row */}
          <div className="flex items-center gap-2 mt-2 flex-wrap">
            {/* Category badge */}
            <span className="inline-flex items-center rounded-full bg-bg-tertiary text-text-muted px-3 py-1 text-xs font-medium">
              {memory.category}
            </span>

            {/* Source icon */}
            <span
              className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-bg-tertiary text-text-muted"
              title={`Source: ${memory.source_type}`}
            >
              <SourceIcon className="w-3 h-3" />
            </span>

            {/* Timestamp */}
            <span className="text-xs text-text-muted">
              {formatRelativeTime(memory.created_at)}
            </span>
          </div>
        </div>
      </div>
    </button>
  );
}
