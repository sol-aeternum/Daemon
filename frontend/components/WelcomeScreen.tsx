'use client';

import {
  Search,
  FileText,
  GitCompare,
  MessagesSquare,
  Sparkles,
} from 'lucide-react';
import { useClientMounted } from '../hooks/useClientMounted';

interface WelcomeScreenProps {
  setInput: (input: string) => void;
  input?: string;
  /**
   * Existing Council shortcut retained pending a separate disposition decision.
   */
  onDeliberate?: () => void;
}

const getTimeGreeting = () => {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) {
    return 'Good morning';
  }
  if (hour >= 12 && hour < 17) {
    return 'Good afternoon';
  }
  return 'Good evening';
};

/**
 * Four ordinary prompt starters from the Midnight study (DESIGN.md §3 Home).
 * They are plain draft prefixes, not separate modes or executable commands.
 */
const promptStarters = [
  {
    icon: MessagesSquare,
    label: 'Explain',
    description: 'A clear answer, at your level',
    starter: 'Help me understand…',
  },
  {
    icon: Search,
    label: 'Find',
    description: 'Search and read available sources',
    starter: 'Search the web for…',
  },
  {
    icon: GitCompare,
    label: 'Compare',
    description: 'Make the differences easier to see',
    starter: 'Compare the tradeoffs between…',
  },
  {
    icon: FileText,
    label: 'Draft',
    description: 'Preview and download a result',
    starter: 'Create a short document about…',
  },
];

export function WelcomeScreen({
  setInput,
  input = '',
  onDeliberate,
}: WelcomeScreenProps) {
  const isClientMounted = useClientMounted();
  const greeting = isClientMounted ? getTimeGreeting() : 'Good evening';

  const handleStarterClick = (starter: string) => {
    if (input) {
      setInput(`${input}\n${starter}`);
    } else {
      setInput(starter);
    }
    // Focus the input after a short delay to allow state update
    setTimeout(() => {
      const inputEl = document.querySelector(
        'textarea[aria-label="Message Daemon"]',
      ) as HTMLElement | null;
      inputEl?.focus();
    }, 50);
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-full px-4 py-8">
      <div className="flex flex-col items-center max-w-2xl w-full space-y-8">
        {/* Logo / Wordmark (existing mark; not a new logo approval) */}
        <div className="flex items-center gap-3 mb-2">
          <div className="w-12 h-12 rounded-xl bg-[var(--color-accent-primary)] flex items-center justify-center shadow-md">
            <Sparkles
              className="w-6 h-6 text-[var(--color-text-on-accent)]"
              aria-hidden="true"
            />
          </div>
          <span className="text-3xl font-bold tracking-tight text-[var(--color-text-primary)]">
            Daemon
          </span>
        </div>

        {/* Greeting */}
        <div className="text-center space-y-2">
          <h1 className="text-4xl md:text-5xl font-semibold text-[var(--color-text-primary)] tracking-tight">
            {greeting}
          </h1>
          <p className="text-lg text-[var(--color-text-secondary)]">
            What would you like to do?
          </p>
        </div>

        {/* Prompt starters */}
        <div
          className="grid grid-cols-2 md:grid-cols-4 gap-3 w-full mt-8"
          role="group"
          aria-label="Prompt starters"
        >
          {promptStarters.map(({ icon: Icon, label, description, starter }) => (
            <button
              type="button"
              key={label}
              onClick={() => handleStarterClick(starter)}
              className="min-h-touch min-w-touch flex flex-col items-center gap-2 p-4 rounded-xl bg-[var(--color-bg-secondary)] border border-[var(--color-border-primary)] text-center transition-colors duration-200 hover:border-[var(--color-accent-primary)] hover:bg-[var(--color-bg-hover)] focus-visible:bg-[var(--color-bg-hover)]"
            >
              <Icon
                className="w-5 h-5 text-[var(--color-text-accent)]"
                aria-hidden="true"
              />
              <span className="text-sm font-medium text-[var(--color-text-primary)]">
                {label}
              </span>
              <span className="text-xs text-[var(--color-text-muted)] leading-snug">
                {description}
              </span>
            </button>
          ))}
        </div>

        {onDeliberate && (
          <button
            type="button"
            onClick={onDeliberate}
            className="min-h-touch rounded-lg px-3 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
          >
            Deliberate
          </button>
        )}

        {/* Hint text */}
        <p className="text-sm text-[var(--color-text-muted)] mt-8 text-center">
          Or type your message below to get started
        </p>
        <p className="text-xs text-[var(--color-text-muted)] text-center">
          Voice and image generation are unavailable in the current runtime.
        </p>
      </div>
    </div>
  );
}
