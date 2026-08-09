'use client';

import { useMemo } from 'react';
import { ROSTER_CONFIG } from './constants';

interface AuditFindingCardProps {
  content: string;
}

interface ParsedAudit {
  critical: string[];
  moderate: string[];
  notes: string[];
  sharedAssumptions: string[];
}

function parseAuditContent(content: string): ParsedAudit {
  const result: ParsedAudit = {
    critical: [],
    moderate: [],
    notes: [],
    sharedAssumptions: [],
  };

  if (!content) return result;

  const sections: Record<string, keyof ParsedAudit> = {
    '**CRITICAL FINDINGS**': 'critical',
    '**MODERATE FINDINGS**': 'moderate',
    '**NOTES**': 'notes',
    '**SHARED ASSUMPTIONS**': 'sharedAssumptions',
  };

  for (const [pattern, field] of Object.entries(sections)) {
    const regex = new RegExp(`${pattern}\\s*:?\\s*\\n?([^*]*)`, 'i');
    const match = content.match(regex);
    if (match && match[1]) {
      const items = match[1].split(/\n+/).filter((s) => s.trim());
      result[field] = items;
    }
  }

  return result;
}

function extractAgentReferences(text: string): string[] {
  const agentPattern = /(analyst|strategist|skeptic|contrarian|auditor)/gi;
  const matches = text.match(agentPattern);
  if (!matches) return [];
  return [...new Set(matches.map((m) => m.toLowerCase()))];
}

export function AuditFindingCard({ content }: AuditFindingCardProps) {
  const parsed = useMemo(() => parseAuditContent(content), [content]);

  const hasContent =
    parsed.critical.length > 0 ||
    parsed.moderate.length > 0 ||
    parsed.notes.length > 0 ||
    parsed.sharedAssumptions.length > 0;

  if (!hasContent) {
    return null;
  }

  const renderSection = (
    title: string,
    items: string[],
    borderColor: string,
    bgColor: string,
  ) => {
    if (items.length === 0) return null;

    return (
      <div
        className="p-3 rounded-lg border-l-4 mb-3"
        style={{
          borderLeftColor: borderColor,
          backgroundColor: bgColor,
        }}
      >
        <div
          className="text-xs font-medium uppercase mb-2"
          style={{ color: borderColor }}
        >
          {title}
        </div>
        <div className="space-y-2">
          {items.map((item, idx) => {
            const agents = extractAgentReferences(item);
            return (
              <div
                key={idx}
                className="text-sm text-[var(--color-text-primary)]"
              >
                <span className="text-[var(--color-text-muted)]">•</span> {item}
                {agents.length > 0 && (
                  <div className="flex gap-1 mt-1">
                    {agents.map((agent) => {
                      const config = ROSTER_CONFIG[agent];
                      if (!config) return null;
                      return (
                        <span
                          key={agent}
                          className="inline-flex items-center px-2 py-0.5 rounded text-xs"
                          style={{
                            backgroundColor: config.bgColor,
                            color: config.color,
                          }}
                        >
                          {config.name}
                        </span>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div className="rounded-lg border border-[var(--color-border)] p-4 bg-[var(--color-bg-secondary)]">
      <div className="font-medium text-[var(--color-text-primary)] mb-4 flex items-center gap-2">
        <span>🔍</span> Audit Findings
      </div>
      {renderSection(
        'Critical Findings',
        parsed.critical,
        'rgb(239, 68, 68)',
        'rgba(239, 68, 68, 0.1)',
      )}
      {renderSection(
        'Moderate Findings',
        parsed.moderate,
        'rgb(245, 158, 11)',
        'rgba(245, 158, 11, 0.1)',
      )}
      {renderSection(
        'Notes',
        parsed.notes,
        'rgb(107, 114, 128)',
        'rgba(107, 114, 128, 0.1)',
      )}
      {renderSection(
        'Shared Assumptions',
        parsed.sharedAssumptions,
        'rgb(168, 85, 247)',
        'rgba(168, 85, 247, 0.1)',
      )}
    </div>
  );
}
