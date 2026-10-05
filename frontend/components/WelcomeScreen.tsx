'use client';

import { type ReactNode } from 'react';
import { Sparkles } from 'lucide-react';
import { useClientMounted } from '../hooks/useClientMounted';
import { useHomeSuggestions } from '../hooks/useHomeSuggestions';
import type { HomeSuggestion } from '../lib/homeSuggestions';
import { isHomeSuggestionExpired } from '../lib/homeSuggestions';
import { HomeSuggestionsPanel } from './home-suggestions/HomeSuggestionsPanel';

interface WelcomeScreenProps {
  /**
   * Current unsent composer text. Having unrelated unsent text pauses the
   * suggestion rows without ever touching or submitting that draft.
   */
  input?: string;
  /**
   * The home composer element owned by app/page.tsx; rendered first so the
   * composer, not the suggestions, is the first home surface.
   */
  composer?: ReactNode;
  /**
   * Clicking/tapping a suggestion immediately submits its exact prompt in a
   * NEW chat; the component never stages a draft or sends anything itself.
   */
  onSuggestionSelect?: (suggestion: HomeSuggestion) => void | Promise<void>;
  /** True while a suggestion submission is in flight. */
  isSubmittingSuggestion?: boolean;
  /** Truthful selection failure message; nothing is requested here. */
  selectionError?: string | null;
  /**
   * Existing Council shortcut retained pending a separate disposition
   * decision; unrelated to suggestions and always passed through.
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

export function WelcomeScreen({
  input = '',
  composer = null,
  onSuggestionSelect,
  isSubmittingSuggestion = false,
  selectionError = null,
  onDeliberate,
}: WelcomeScreenProps) {
  const isClientMounted = useClientMounted();
  const greeting = isClientMounted ? getTimeGreeting() : 'Good evening';
  const suggestions = useHomeSuggestions();
  // Unrelated unsent text pauses the rows. "Show them anyway" is handled
  // inside the panel and resets whenever the pause state changes.
  const frozen = Boolean(input.trim());

  const handleSelect = (suggestion: HomeSuggestion) => {
    if (isSubmittingSuggestion || isHomeSuggestionExpired(suggestion))
      return undefined;
    return onSuggestionSelect?.(suggestion);
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-full px-4 py-8">
      <div className="flex flex-col items-center max-w-2xl w-full space-y-6">
        {/* Logo / Wordmark (existing mark; not a new logo approval) */}
        <div className="flex items-center gap-3">
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
            What would you like to pick up?
          </p>
        </div>

        {/* Composer first: owned and passed by app/page.tsx. The exact
            same composer element is reused, never cloned. */}
        {composer ? (
          <div className="w-full flex flex-col items-center">{composer}</div>
        ) : null}

        {/* Contextual task summaries (zero to three rows). */}
        <HomeSuggestionsPanel
          view={suggestions.view}
          hiddenAll={suggestions.hiddenAll}
          frozen={frozen}
          draftRevision={input}
          isSubmitting={isSubmittingSuggestion}
          preferencePending={suggestions.preferencePending}
          isBusy={
            suggestions.view.status === 'loading' ||
            suggestions.view.status === 'generating'
          }
          selectionError={selectionError}
          onSuggestionSelect={handleSelect}
          onEnable={() => {
            void suggestions.enable();
          }}
          onDisable={() => {
            void suggestions.disable();
          }}
          onRefresh={() => {
            void suggestions.refresh();
          }}
          onRetry={() => {
            void suggestions.load();
          }}
          onDismiss={(id) => suggestions.dismiss(id)}
          onUndoDismiss={(id) => suggestions.undoDismiss(id)}
          onHideAll={suggestions.dismissAll}
          onRestoreAll={suggestions.restoreAll}
        />

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
        <p className="text-sm text-[var(--color-text-muted)] text-center">
          Type a message to get started
        </p>
        <p className="text-xs text-[var(--color-text-muted)] text-center">
          Voice and image generation are unavailable in the current runtime.
        </p>
      </div>
    </div>
  );
}

export default WelcomeScreen;
