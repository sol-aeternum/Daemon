'use client';

import { useState } from 'react';
import { Brain, Play, Check, Settings } from 'lucide-react';
import { isCouncilInterviewEvent } from '../../lib/events';
import type { ChatEvent } from '../../lib/events';
import { ROSTER_CONFIG } from './constants';

export interface CouncilInterviewEvent {
  type: 'council_interview';
  roster: Record<string, { name: string; role: string; description: string }>;
  presets: string[];
  rounds_options: number[];
  audit_default: boolean;
}

interface CouncilConfig {
  preset: string;
  rounds: number;
  audit: boolean;
}

interface CouncilInterviewCardProps {
  event: ChatEvent;
  onSendConfig: (config: CouncilConfig) => void;
}

export function CouncilInterviewCard({
  event,
  onSendConfig,
}: CouncilInterviewCardProps) {
  const councilEvent = isCouncilInterviewEvent(event) ? event : null;
  const [selectedPreset, setSelectedPreset] = useState('Default');
  const [selectedRounds, setSelectedRounds] = useState(1);
  const [auditEnabled, setAuditEnabled] = useState(false);

  if (!councilEvent) {
    return null;
  }

  const { roster, presets, rounds_options } = councilEvent;

  const handleRunCouncil = () => {
    onSendConfig({
      preset: selectedPreset,
      rounds: selectedRounds,
      audit: auditEnabled,
    });
  };

  const handleUseDefaults = () => {
    onSendConfig({
      preset: presets[0] || 'Default',
      rounds: rounds_options[0] || 1,
      audit: false,
    });
  };

  return (
    <div className="my-3 max-w-2xl rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] shadow-sm overflow-hidden">
      <div className="px-4 py-3 border-b border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)]">
        <div className="flex items-center gap-2">
          <Brain className="w-5 h-5 text-[var(--color-accent-primary)]" />
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            Council Configuration
          </h3>
        </div>
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          Multi-model deliberation for complex decisions
        </p>
      </div>

      <div className="px-4 py-3 border-b border-[var(--color-border-primary)]">
        <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
          Roster
        </div>
        <div className="grid grid-cols-2 gap-2">
          {Object.entries(roster).map(([key, member]) => {
            const config = ROSTER_CONFIG[key] || ROSTER_CONFIG.analyst;
            const Icon = config.icon;
            return (
              <div
                key={key}
                className="flex items-center gap-2 p-2 rounded-lg bg-[var(--color-bg-tertiary)] border border-[var(--color-border-muted)]"
              >
                <Icon className="w-4 h-4" style={{ color: config.color }} />
                <div className="min-w-0">
                  <div className="text-xs font-medium text-[var(--color-text-muted)] capitalize">
                    {member.name}
                  </div>
                  <div className="text-[10px] text-[var(--color-text-muted)] truncate">
                    {member.description}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="px-4 py-3 space-y-4">
        <div>
          <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
            Preset
          </div>
          <div className="flex gap-2">
            {presets.map((preset) => (
              <button
                key={preset}
                onClick={() => setSelectedPreset(preset)}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
                  selectedPreset === preset
                    ? 'bg-[var(--color-accent-primary)] text-white'
                    : 'bg-[var(--color-bg-tertiary)] text-[var(--color-text-secondary)] border border-[var(--color-border-primary)] hover:border-[var(--color-border-secondary)]'
                }`}
              >
                {preset}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
            Rounds
          </div>
          <div className="flex gap-2">
            {rounds_options.map((rounds) => (
              <button
                key={rounds}
                onClick={() => setSelectedRounds(rounds)}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
                  selectedRounds === rounds
                    ? 'bg-[var(--color-accent-primary)] text-white'
                    : 'bg-[var(--color-bg-tertiary)] text-[var(--color-text-secondary)] border border-[var(--color-border-primary)] hover:border-[var(--color-border-secondary)]'
                }`}
              >
                {rounds}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Settings className="w-4 h-4 text-[var(--color-text-muted)]" />
            <span className="text-sm text-[var(--color-text-secondary)]">
              Enable audit trail
            </span>
          </div>
          <button
            onClick={() => setAuditEnabled(!auditEnabled)}
            className={`relative w-10 h-5 rounded-full transition-colors ${
              auditEnabled
                ? 'bg-[var(--color-accent-primary)]'
                : 'bg-[var(--color-bg-tertiary)] border border-[var(--color-border-primary)]'
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
                auditEnabled ? 'translate-x-5' : 'translate-x-0'
              }`}
            />
          </button>
        </div>
      </div>

      <div className="px-4 py-3 border-t border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] flex gap-2">
        <button
          onClick={handleRunCouncil}
          className="flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-[var(--color-accent-primary)] hover:bg-[var(--color-accent-hover)] text-white text-sm font-medium rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)]"
        >
          <Play className="w-4 h-4" />
          Run Council
        </button>
        <button
          onClick={handleUseDefaults}
          className="flex items-center justify-center gap-2 px-4 py-2 bg-[var(--color-bg-secondary)] hover:bg-[var(--color-bg-hover)] text-[var(--color-text-secondary)] text-sm font-medium rounded-lg border border-[var(--color-border-primary)] transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--color-border-secondary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)]"
        >
          <Check className="w-4 h-4" />
          Use Defaults
        </button>
      </div>
    </div>
  );
}
