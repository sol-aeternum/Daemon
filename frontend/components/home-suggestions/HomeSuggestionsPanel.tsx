'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useClientMounted } from '../../hooks/useClientMounted';
import type {
  HomeSuggestion,
  HomeSuggestionsViewState,
} from '../../lib/homeSuggestions';
import { SuggestionRow } from './SuggestionRow';

interface HomeSuggestionsPanelProps {
  view: HomeSuggestionsViewState;
  /** Whole feature hidden via the global control (session-local). */
  hiddenAll: boolean;
  /** Unrelated unsent text exists; rows pause until shown anyway. */
  frozen: boolean;
  draftRevision?: string;
  /** A suggestion submission is running; rows are disabled while true. */
  isSubmitting: boolean;
  isBusy: boolean;
  preferencePending?: boolean;
  /** Selection failure message from the caller; nothing is requested here. */
  selectionError: string | null;
  onSuggestionSelect: (suggestion: HomeSuggestion) => void | Promise<void>;
  onEnable: () => void;
  onDisable?: () => void;
  onRefresh: () => void;
  onRetry: () => void;
  onDismiss: (id: string, summary: string) => void;
  onUndoDismiss: (id: string) => void;
  onHideAll: () => void;
  onRestoreAll: () => void;
}

interface DismissNotice {
  id: string;
  summary: string;
}

/**
 * The zero-to-three contextual task-summary rows under the home composer.
 * Every non-ready state is truthful (off-until-enabled, empty, generating,
 * expired, unavailable, error, paused-while-typing, dismissed, hidden) and
 * the composer itself stays usable outside an in-flight submission. There
 * is no Start chat badge, no Preview button and no per-row Why control.
 */
export function HomeSuggestionsPanel({
  view,
  hiddenAll,
  frozen,
  draftRevision,
  isSubmitting,
  isBusy,
  preferencePending = false,
  selectionError,
  onSuggestionSelect,
  onEnable,
  onDisable,
  onRefresh,
  onRetry,
  onDismiss,
  onUndoDismiss,
  onHideAll,
  onRestoreAll,
}: HomeSuggestionsPanelProps) {
  const isClientMounted = useClientMounted();
  // "Show them anyway" is temporary: the next input edit pauses rows again.
  const [revealedRevision, setRevealedRevision] = useState<string | null>(null);
  const currentRevision = draftRevision ?? String(frozen);
  const showPausedAnyway = revealedRevision === currentRevision;
  const [notice, setNotice] = useState<DismissNotice | null>(null);

  if (!isClientMounted) {
    return null;
  }

  const rows = view.suggestions;
  const rowsAvailable = view.status === 'ready';
  const showRows =
    rowsAvailable &&
    !hiddenAll &&
    rows.length > 0 &&
    (!frozen || showPausedAnyway);
  const allDismissed = view.status === 'dismissed';
  const paused =
    rowsAvailable && rows.length > 0 && frozen && !showPausedAnyway;

  const handleDismiss = (id: string, summary: string) => {
    setNotice({ id, summary });
    onDismiss(id, summary);
  };

  const handleUndo = () => {
    if (notice) {
      onUndoDismiss(notice.id);
      setNotice(null);
    }
  };

  const handleEnable = () => {
    setNotice(null);
    onEnable();
  };

  const handleRevealControl = () => {
    setNotice(null);
    if (view.status === 'expired') {
      onRefresh();
    } else {
      onRetry();
    }
  };

  return (
    <section
      className="w-full"
      aria-label="Suggested from your recent conversations"
      aria-busy={isBusy || undefined}
    >
      {showRows ? (
        <div className="flex w-full flex-col gap-2">
          <div className="flex items-center justify-between gap-2 px-1">
            <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
              Suggested from your recent conversations
            </p>
            <button
              type="button"
              data-testid="hide-personal-suggestions"
              className="min-h-touch rounded-lg px-2 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-secondary)]"
              onClick={() => {
                setNotice(null);
                onHideAll();
              }}
            >
              Hide personal suggestions
            </button>
          </div>
          <ul className="flex w-full flex-col gap-2">
            {rows.map((suggestion) => (
              <SuggestionRow
                key={suggestion.id}
                suggestion={suggestion}
                disabled={isSubmitting}
                onSelect={onSuggestionSelect}
                onDismiss={handleDismiss}
              />
            ))}
          </ul>
        </div>
      ) : null}
      {notice && !hiddenAll ? (
        <div
          role="status"
          data-testid="dismiss-notice"
          className="flex items-center justify-between gap-2 rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border-primary)] px-3 py-2 text-xs text-[var(--color-text-secondary)]"
        >
          <span>Dismissed “{notice.summary}”. Nothing was deleted.</span>
          <button
            type="button"
            data-testid="undo-dismiss"
            className="min-h-touch min-w-touch rounded-md px-2 text-xs font-medium text-[var(--color-text-accent)] hover:bg-[var(--color-bg-hover)]"
            onClick={handleUndo}
          >
            Undo
          </button>
        </div>
      ) : null}
      {allDismissed && !hiddenAll ? (
        <p
          role="status"
          data-testid="all-dismissed-note"
          className="rounded-lg bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-muted)] text-center"
        >
          You dismissed every suggestion on this screen. Type in the composer
          above. Dismissal never deletes the source conversation.{' '}
          <button
            type="button"
            data-testid="restore-suggestions"
            className="min-h-touch min-w-touch rounded-md px-2 text-xs font-medium text-[var(--color-text-accent)] hover:bg-[var(--color-bg-hover)]"
            onClick={() => {
              setNotice(null);
              onRestoreAll();
            }}
          >
            Restore suggestions
          </button>
        </p>
      ) : null}
      {hiddenAll ? (
        <p
          role="status"
          data-testid="personal-hidden-note"
          className="rounded-lg bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-muted)] text-center"
        >
          Personal suggestions are hidden on this screen. Generation remains
          enabled.{' '}
          <button
            type="button"
            data-testid="show-personal-suggestions"
            className="min-h-touch min-w-touch rounded-md px-2 text-xs font-medium text-[var(--color-text-accent)] hover:bg-[var(--color-bg-hover)]"
            onClick={onRestoreAll}
          >
            Show personal suggestions
          </button>
        </p>
      ) : null}
      {paused && !hiddenAll ? (
        <p
          role="status"
          data-testid="paused-while-typing-note"
          className="rounded-lg bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-muted)] text-center"
        >
          Suggestions are paused while you type. Your draft is kept.{' '}
          <button
            type="button"
            data-testid="show-suggestions-anyway"
            className="min-h-touch min-w-touch rounded-md px-2 text-xs font-medium text-[var(--color-text-accent)] hover:bg-[var(--color-bg-hover)]"
            onClick={() => setRevealedRevision(currentRevision)}
          >
            Show them anyway
          </button>
        </p>
      ) : null}
      {view.status === 'disabled' && !hiddenAll ? (
        <div
          data-testid="personal-off-note"
          className="rounded-lg bg-[var(--color-bg-secondary)] border border-[var(--color-border-primary)] px-4 py-3 text-sm text-[var(--color-text-secondary)]"
        >
          <p>
            Personal suggestions are off. Nothing is derived from your
            conversations until you turn them on.
          </p>
          <button
            type="button"
            data-testid="enable-personal-suggestions"
            disabled={isBusy || isSubmitting || preferencePending}
            className="mt-2 rounded-lg border border-[var(--color-border-primary)] px-3 py-1.5 text-sm font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-bg-hover)] disabled:opacity-60"
            onClick={handleEnable}
          >
            Turn on suggestions
          </button>
        </div>
      ) : null}
      {(view.status === 'unavailable' ||
        view.status === 'expired' ||
        view.status === 'error') &&
      !hiddenAll ? (
        <p
          role="status"
          data-testid={`suggestion-status-${view.status}`}
          className="rounded-lg bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-muted)] text-center"
        >
          {view.status === 'unavailable'
            ? (view.message ?? 'Suggestions are unavailable right now.')
            : null}
          {view.status === 'expired'
            ? (view.message ??
              'Suggestions expired. Refresh to build new ones.')
            : null}
          {view.status === 'error'
            ? (view.message ?? 'Suggestions failed to load.')
            : null}
          {(view.status === 'error' || view.status === 'unavailable') && (
            <Link
              href="/settings/profile"
              className="inline-flex min-h-touch items-center px-2 text-[var(--color-text-accent)]"
            >
              Manage suggestions in Settings
            </Link>
          )}
          {(view.status === 'expired' ||
            view.status === 'error' ||
            view.status === 'unavailable') && (
            <button
              type="button"
              data-testid={
                view.status === 'expired'
                  ? 'refresh-suggestions'
                  : 'retry-suggestions'
              }
              disabled={isBusy || isSubmitting}
              className="min-h-touch min-w-touch rounded-md px-2 text-xs font-medium text-[var(--color-text-accent)] hover:bg-[var(--color-bg-hover)] disabled:opacity-60"
              onClick={handleRevealControl}
            >
              {view.status === 'expired' ? 'Refresh' : 'Retry'}
            </button>
          )}
        </p>
      ) : null}
      {view.status === 'generating' ? (
        <p
          role="status"
          data-testid="suggestion-status-generating"
          className="rounded-lg bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-muted)] text-center"
        >
          {view.message ??
            'Building suggestions from your recent conversations…'}
        </p>
      ) : null}
      {view.status === 'loading' ? (
        <p
          role="status"
          data-testid="suggestion-status-loading"
          className="rounded-lg bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-muted)] text-center"
        >
          Loading suggestions…
        </p>
      ) : null}
      {showRows && (
        <div className="flex justify-center gap-3 text-xs mt-2">
          <button
            type="button"
            className="min-h-touch px-2"
            disabled={isBusy || isSubmitting || preferencePending}
            onClick={onRefresh}
          >
            Refresh suggestions
          </button>
          <button
            type="button"
            className="min-h-touch px-2"
            disabled={isSubmitting || preferencePending}
            onClick={onDisable}
          >
            Turn off suggestions
          </button>
        </div>
      )}
      {selectionError ? (
        <p
          role="alert"
          data-testid="selection-error"
          className="mt-2 rounded-lg bg-[var(--color-status-error-bg)] px-3 py-2 text-xs text-[var(--color-status-error)]"
        >
          {selectionError}
        </p>
      ) : null}
    </section>
  );
}

export default HomeSuggestionsPanel;
