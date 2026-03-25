"use client";

import { useMemo } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { ROSTER_CONFIG } from "./constants";
import { parseRound1Response, parseRound2Response, type ParsedResponse, type Round2ParsedResponse } from "./parseResponse";
import MarkdownMessage from "../MarkdownMessage";

interface AdvisorCardProps {
  role: string;
  response: string;
  round: number;
  isExpanded: boolean;
  onToggle: () => void;
}

export function AdvisorCard({ role, response, round, isExpanded, onToggle }: AdvisorCardProps) {
  const config = ROSTER_CONFIG[role] || ROSTER_CONFIG.analyst;
  const Icon = config.icon;

  const isRound2 = round >= 2;
  
  const parsedRound1 = useMemo(() => parseRound1Response(response), [response]);
  const parsedRound2 = useMemo(() => parseRound2Response(response), [response]);
  
  const parsed = isRound2 ? parsedRound2 : parsedRound1;
  const isParsed = parsed.parsed;

  const positionSummary = isRound2 
    ? (parsed as Round2ParsedResponse).revisedPosition
      ? (parsed as Round2ParsedResponse).revisedPosition.slice(0, 120) + ((parsed as Round2ParsedResponse).revisedPosition.length > 120 ? "..." : "")
      : ""
    : (parsed as ParsedResponse).position
      ? (parsed as ParsedResponse).position.slice(0, 120) + ((parsed as ParsedResponse).position.length > 120 ? "..." : "")
      : "";

  const confidence = isRound2 ? (parsed as Round2ParsedResponse).revisedConfidence : (parsed as ParsedResponse).confidence;

  return (
    <div
      className="rounded-lg border-2 overflow-hidden mb-3"
      style={{
        borderColor: config.borderColor,
        backgroundColor: "var(--color-bg-secondary)",
      }}
    >
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 p-4 text-left hover:bg-[var(--color-bg-hover)] transition-colors"
      >
        <div
          className={`p-2 rounded-lg ${config.bgColor}`}
          style={{ color: config.color }}
        >
          <Icon className="w-5 h-5" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="font-medium text-[var(--color-text-primary)]">
            {config.name}
          </div>
          {positionSummary && (
            <div className="text-sm text-[var(--color-text-muted)] truncate mt-1">
              {positionSummary}
            </div>
          )}
        </div>

        {confidence > 0 && (
          <div
            className="px-3 py-1 rounded-full text-sm font-medium"
            style={{
              backgroundColor:
                confidence <= 3
                  ? "rgba(239, 68, 68, 0.2)"
                  : confidence <= 6
                  ? "rgba(245, 158, 11, 0.2)"
                  : confidence <= 8
                  ? "rgba(59, 130, 246, 0.2)"
                  : "rgba(34, 197, 94, 0.2)",
              color:
                confidence <= 3
                  ? "rgb(239, 68, 68)"
                  : confidence <= 6
                  ? "rgb(245, 158, 11)"
                  : confidence <= 8
                  ? "rgb(59, 130, 246)"
                  : "rgb(34, 197, 94)",
            }}
          >
            {confidence}/10
          </div>
        )}

        {isExpanded ? (
          <ChevronDown className="w-5 h-5 text-[var(--color-text-muted)]" />
        ) : (
          <ChevronRight className="w-5 h-5 text-[var(--color-text-muted)]" />
        )}
      </button>

      {isExpanded && (
        <div className="p-4 pt-0 border-t border-[var(--color-border)]">
          {isParsed ? (
            <div className="space-y-4">
              {!isRound2 && (parsed as ParsedResponse).position && (
                <div
                  className="p-3 rounded-lg bg-[var(--color-bg-primary)] border-l-4"
                  style={{ borderLeftColor: config.borderColor }}
                >
                  <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase mb-1">
                    Position
                  </div>
                  <div className="text-[var(--color-text-primary)]">
                    {(parsed as ParsedResponse).position}
                  </div>
                </div>
              )}

              {isRound2 && (parsed as Round2ParsedResponse).revisedPosition && (
                <div
                  className="p-3 rounded-lg bg-[var(--color-bg-primary)] border-l-4"
                  style={{ borderLeftColor: config.borderColor }}
                >
                  <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase mb-1">
                    Revised Position
                  </div>
                  <div className="text-[var(--color-text-primary)]">
                    {(parsed as Round2ParsedResponse).revisedPosition}
                  </div>
                </div>
              )}

              {!isRound2 && (parsed as ParsedResponse).keyArguments && (
                <div>
                  <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase mb-2">
                    Key Arguments
                  </div>
                  <div className="text-[var(--color-text-primary)]">
                    <MarkdownMessage content={(parsed as ParsedResponse).keyArguments} />
                  </div>
                </div>
              )}

              {isRound2 && (parsed as Round2ParsedResponse).strongestPoint && (
                <div>
                  <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase mb-2">
                    Strongest Point
                  </div>
                  <div className="text-[var(--color-text-primary)]">
                    {(parsed as Round2ParsedResponse).strongestPoint}
                  </div>
                </div>
              )}

              {!isRound2 && (parsed as ParsedResponse).assumptions && (
                <div>
                  <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase mb-2 flex items-center gap-2">
                    <span>⚠️</span> Assumptions
                  </div>
                  <div className="text-[var(--color-text-secondary)] text-sm">
                    {(parsed as ParsedResponse).assumptions}
                  </div>
                </div>
              )}

              {isRound2 && (parsed as Round2ParsedResponse).weakestAssumption && (
                <div>
                  <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase mb-2">
                    Weakest Assumption
                  </div>
                  <div className="text-[var(--color-text-secondary)] text-sm">
                    {(parsed as Round2ParsedResponse).weakestAssumption}
                  </div>
                </div>
              )}

              {!isRound2 && (parsed as ParsedResponse).blindSpot && (
                <div className="p-3 rounded-lg bg-amber-500/10 border-l-4 border-l-amber-500">
                  <div className="text-xs font-medium text-amber-400 uppercase mb-1 flex items-center gap-2">
                    <span>⚠️</span> Blind Spot
                  </div>
                  <div className="text-[var(--color-text-primary)]">
                    {(parsed as ParsedResponse).blindSpot}
                  </div>
                </div>
              )}

              {confidence > 0 && (
                <div>
                  <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase mb-1">
                    {isRound2 ? "Revised Confidence" : "Confidence"}
                  </div>
                  <div className="text-[var(--color-text-primary)]">
                    {confidence}/10
                  </div>
                </div>
              )}

              {!isRound2 && (parsed as ParsedResponse).missingInformation && (
                <div>
                  <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase mb-2">
                    Missing Information
                  </div>
                  <div className="text-[var(--color-text-secondary)] text-sm">
                    {(parsed as ParsedResponse).missingInformation}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="text-[var(--color-text-primary)]">
              <MarkdownMessage content={response} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}