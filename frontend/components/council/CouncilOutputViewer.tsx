"use client";

import { useState, useMemo } from "react";
import { FileText, AlertTriangle, Activity, BarChart3, ShieldAlert, ScrollText, ChevronDown, ChevronRight } from "lucide-react";
import { isCouncilOutputEvent, isCouncilDoneEvent } from "../../lib/events";
import type { ChatEvent } from "../../lib/events";
import MarkdownMessage from "../MarkdownMessage";
import { AdvisorCard } from "./AdvisorCard";
import { AuditFindingCard } from "./AuditFindingCard";
import { ROSTER_CONFIG } from "./constants";
import { parseRound1Response, parseRound2Response } from "./parseResponse";

interface SectionConfig {
  key: string;
  label: string;
  icon: typeof FileText;
  borderColor: string;
  headerColor: string;
  defaultOpen: boolean;
}

const SECTIONS: SectionConfig[] = [
  {
    key: "consensus",
    label: "Where All Advisors Agree",
    icon: FileText,
    borderColor: "var(--color-status-success)",
    headerColor: "var(--color-status-success)",
    defaultOpen: true,
  },
  {
    key: "contested",
    label: "Council Positions",
    icon: AlertTriangle,
    borderColor: "var(--color-status-warning)",
    headerColor: "var(--color-status-warning)",
    defaultOpen: true,
  },
  {
    key: "signals",
    label: "Key Signals",
    icon: Activity,
    borderColor: "var(--color-status-info)",
    headerColor: "var(--color-status-info)",
    defaultOpen: false,
  },
  {
    key: "confidence",
    label: "Confidence Level",
    icon: BarChart3,
    borderColor: "hsl(270, 70%, 65%)",
    headerColor: "hsl(270, 70%, 65%)",
    defaultOpen: false,
  },
  {
    key: "audit",
    label: "Audit Findings",
    icon: ShieldAlert,
    borderColor: "var(--color-status-error)",
    headerColor: "var(--color-status-error)",
    defaultOpen: true,
  },
  {
    key: "raw",
    label: "Raw Reasoning",
    icon: ScrollText,
    borderColor: "var(--color-text-muted)",
    headerColor: "var(--color-text-muted)",
    defaultOpen: false,
  },
];

const ADVISOR_ORDER = ["analyst", "strategist", "skeptic", "contrarian", "auditor"];

interface CouncilOutputViewerProps {
  events: ChatEvent[];
}

interface SessionMetadata {
  session_id: string;
  total_tokens: number;
  total_cost_usd: number;
  models_used: string[];
}

interface AdvisorResponse {
  role: string;
  content: string;
  round: number;
}

export function CouncilOutputViewer({ events }: CouncilOutputViewerProps) {
  const outputEvents = events.filter(isCouncilOutputEvent);
  const doneEvent = events.find(isCouncilDoneEvent);

  const sectionContent: Record<string, string[]> = {};
  outputEvents.forEach((event) => {
    if (!sectionContent[event.section]) {
      sectionContent[event.section] = [];
    }
    sectionContent[event.section].push(event.content);
  });

  const availableSections = SECTIONS.filter(
    (section) => sectionContent[section.key] && sectionContent[section.key].length > 0
  );

  if (availableSections.length === 0) {
    return null;
  }

  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    availableSections.forEach((section) => {
      initial[section.key] = section.key === "audit" ? true : section.defaultOpen;
    });
    return initial;
  });

  const [expandedAdvisors, setExpandedAdvisors] = useState<Record<string, boolean>>({});

  const toggleSection = (key: string) => {
    setExpandedSections((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  };

  const toggleAdvisor = (role: string) => {
    setExpandedAdvisors((prev) => ({
      ...prev,
      [role]: !prev[role],
    }));
  };

  const expandAllAdvisors = () => {
    const allExpanded: Record<string, boolean> = {};
    ADVISOR_ORDER.forEach((role) => {
      allExpanded[role] = true;
    });
    setExpandedAdvisors(allExpanded);
  };

  const collapseAllAdvisors = () => {
    setExpandedAdvisors({});
  };

  const advisorResponses = useMemo((): AdvisorResponse[] => {
    const contestedContent = sectionContent["contested"]?.join("\n\n") || "";
    if (!contestedContent) return [];

    const responses: AdvisorResponse[] = [];
    const lines = contestedContent.split("\n");
    let currentRole = "";
    let currentContent: string[] = [];
    let currentRound = 1;

    for (const line of lines) {
      const roleMatch = line.match(/^(analyst|strategist|skeptic|contrarian|auditor):/i);
      if (roleMatch) {
        if (currentRole && currentContent.length > 0) {
          responses.push({
            role: currentRole,
            content: currentContent.join("\n"),
            round: currentRound,
          });
        }
        currentRole = roleMatch[1].toLowerCase();
        currentContent = [line.replace(/^[^:]+:\s*/, "")];
      } else if (line.includes("**Round 2**") || line.includes("**REVISED**")) {
        currentRound = 2;
      } else if (currentRole) {
        currentContent.push(line);
      }
    }

    if (currentRole && currentContent.length > 0) {
      responses.push({
        role: currentRole,
        content: currentContent.join("\n"),
        round: currentRound,
      });
    }

    return responses;
  }, [sectionContent]);

  const consensusContent = sectionContent["consensus"]?.join("\n\n") || "";
  const hasRealConsensus = consensusContent && !consensusContent.toLowerCase().includes("no clear consensus") && !consensusContent.toLowerCase().includes("no unanimous");

  const auditContent = sectionContent["audit"]?.join("\n\n") || "";
  const rawContent = sectionContent["raw"]?.join("\n\n") || "";

  const formatMetadata = (metadata: SessionMetadata | undefined) => {
    if (!metadata) return null;

    return (
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--color-text-muted)]">
        <span>Session: <span className="font-mono text-[var(--color-text-secondary)]">{metadata.session_id.slice(0, 8)}...</span></span>
        <span>Tokens: <span className="text-[var(--color-text-secondary)]">{metadata.total_tokens.toLocaleString()}</span></span>
        <span>Cost: <span className="text-[var(--color-text-secondary)]">${metadata.total_cost_usd.toFixed(4)}</span></span>
        <span className="flex items-center gap-1">
          Models: 
          <span className="text-[var(--color-text-secondary)]">
            {metadata.models_used.length} used
          </span>
        </span>
      </div>
    );
  };

  return (
    <div className="my-4 max-w-3xl rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] shadow-md overflow-hidden">
      <div className="px-4 py-3 border-b border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)]">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
              Council Output
            </h3>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              {availableSections.length} section{availableSections.length !== 1 ? "s" : ""} available
            </p>
          </div>
        </div>
      </div>

      <div className="divide-y divide-[var(--color-border-primary)]">
        {hasRealConsensus && (
          <div
            className="border-l-4"
            style={{ borderLeftColor: SECTIONS[0].borderColor }}
          >
            <button
              type="button"
              onClick={() => toggleSection("consensus")}
              className="group flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-[var(--color-bg-hover)]"
            >
              <FileText
                className="w-4 h-4 flex-shrink-0"
                style={{ color: SECTIONS[0].headerColor }}
              />
              <span
                className="text-sm font-medium flex-1"
                style={{ color: SECTIONS[0].headerColor }}
              >
                {SECTIONS[0].label}
              </span>
              {expandedSections["consensus"] ? (
                <ChevronDown className="w-4 h-4 text-[var(--color-text-muted)]" />
              ) : (
                <ChevronRight className="w-4 h-4 text-[var(--color-text-muted)]" />
              )}
            </button>
            {expandedSections["consensus"] && (
              <div className="px-4 pb-4">
                <div className="pl-7">
                  <div className="prose prose-sm prose-invert max-w-none">
                    <MarkdownMessage content={consensusContent} />
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {!hasRealConsensus && consensusContent && (
          <div className="px-4 py-3 text-sm text-[var(--color-text-muted)] italic">
            No unanimous agreement — see individual positions below.
          </div>
        )}

        {advisorResponses.length > 0 && (
          <div>
            <div className="flex items-center justify-between px-4 py-3 bg-[var(--color-bg-tertiary)]">
              <div className="flex items-center gap-3">
                <AlertTriangle
                  className="w-4 h-4"
                  style={{ color: SECTIONS[1].headerColor }}
                />
                <span
                  className="text-sm font-medium"
                  style={{ color: SECTIONS[1].headerColor }}
                >
                  Council Positions
                </span>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={expandAllAdvisors}
                  className="px-2 py-1 text-xs font-medium text-[var(--color-text-secondary)] bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] rounded-md border border-[var(--color-border-primary)] transition-colors"
                >
                  Expand All
                </button>
                <button
                  onClick={collapseAllAdvisors}
                  className="px-2 py-1 text-xs font-medium text-[var(--color-text-secondary)] bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] rounded-md border border-[var(--color-border-primary)] transition-colors"
                >
                  Collapse All
                </button>
              </div>
            </div>
            <div className="px-4 pb-4">
              {advisorResponses.map((advisor) => (
                <AdvisorCard
                  key={advisor.role}
                  role={advisor.role}
                  response={advisor.content}
                  round={advisor.round}
                  isExpanded={expandedAdvisors[advisor.role] || false}
                  onToggle={() => toggleAdvisor(advisor.role)}
                />
              ))}
            </div>
          </div>
        )}

        {auditContent && (
          <div className="px-4 pb-4">
            <AuditFindingCard content={auditContent} />
          </div>
        )}

        {rawContent && (
          <div
            className="border-l-4 transition-colors"
            style={{ borderLeftColor: SECTIONS[5].borderColor }}
          >
            <button
              type="button"
              onClick={() => toggleSection("raw")}
              className="group flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-[var(--color-bg-hover)]"
            >
              <ScrollText
                className="w-4 h-4 flex-shrink-0"
                style={{ color: SECTIONS[5].headerColor }}
              />
              <span
                className="text-sm font-medium flex-1"
                style={{ color: SECTIONS[5].headerColor }}
              >
                {SECTIONS[5].label}
              </span>
              {expandedSections["raw"] ? (
                <ChevronDown className="w-4 h-4 text-[var(--color-text-muted)]" />
              ) : (
                <ChevronRight className="w-4 h-4 text-[var(--color-text-muted)]" />
              )}
            </button>
            {expandedSections["raw"] && (
              <div className="px-4 pb-4">
                <div className="pl-7">
                  <div className="prose prose-sm prose-invert max-w-none">
                    <MarkdownMessage content={rawContent} />
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {doneEvent && (
        <div className="px-4 py-3 border-t border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)]">
          {formatMetadata({
            session_id: doneEvent.session_id,
            total_tokens: doneEvent.total_tokens,
            total_cost_usd: doneEvent.total_cost_usd,
            models_used: doneEvent.models_used,
          })}
        </div>
      )}
    </div>
  );
}