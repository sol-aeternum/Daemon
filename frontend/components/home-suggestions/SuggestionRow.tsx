'use client';

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from 'react';
import { createPortal } from 'react-dom';
import { useClientMounted } from '../../hooks/useClientMounted';
import { Sparkles, X } from 'lucide-react';
import { isHomeSuggestionExpired } from '../../lib/homeSuggestions';
import type { HomeSuggestion } from '../../lib/homeSuggestions';

const TOOLTIP_MAX_WIDTH = 432;
const VIEWPORT_MARGIN = 12;
const GAP_ABOVE_ROW = 8;
const TOOLTIP_MAX_HEIGHT = 240;
const TOOLTIP_ID = 'suggestion-prompt-tooltip';

/**
 * The exact detailed prompt behind a suggestion row, centred horizontally
 * over the full row and clamped inside the viewport. Rendered through a
 * fixed body portal so no ancestor overflow can clip it. Showing, focusing
 * or scrolling this preview never submits anything.
 */
export function PromptTooltip({
  prompt,
  open,
  anchor,
  rowButtonId,
  onMouseEnter,
  onMouseLeave,
}: {
  prompt: string;
  /** Set while the row is hovered or keyboard-focused. */
  open: boolean;
  /** The row's main button element, used for placement. */
  anchor: RefObject<HTMLElement | null>;
  /** Id of the row's main button, so the tooltip can name it. */
  rowButtonId: string;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}) {
  const isClientMounted = useClientMounted();
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [placement, setPlacement] = useState<{
    top: number;
    left: number;
    width: number;
  } | null>(null);

  useEffect(() => {
    if (!open || !anchor.current?.isConnected) {
      return undefined;
    }
    const measure = () => {
      if (!anchor.current) return;
      const rowRect = anchor.current.getBoundingClientRect();
      const width = Math.min(
        TOOLTIP_MAX_WIDTH,
        window.innerWidth - VIEWPORT_MARGIN * 2,
      );
      const tooltip = tooltipRef.current;
      const height = tooltip ? tooltip.offsetHeight : 96;
      const centredLeft = rowRect.left + (rowRect.width - width) / 2;
      const left = Math.max(
        VIEWPORT_MARGIN,
        Math.min(centredLeft, window.innerWidth - width - VIEWPORT_MARGIN),
      );
      let top = rowRect.top - height - GAP_ABOVE_ROW;
      // Clamp above-the-row overflow back inside the viewport.
      if (top < VIEWPORT_MARGIN) {
        top = Math.max(
          VIEWPORT_MARGIN,
          Math.min(
            rowRect.bottom + GAP_ABOVE_ROW,
            window.innerHeight - height - VIEWPORT_MARGIN,
          ),
        );
      }
      setPlacement({ top, left, width });
    };
    // Re-measure after the content's real height has applied.
    let frame = window.requestAnimationFrame(() => {
      measure();
      frame = window.requestAnimationFrame(measure);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [open, anchor, prompt]);

  if (!isClientMounted || !open || !placement) {
    return null;
  }

  return createPortal(
    <div
      id={`${TOOLTIP_ID}-${rowButtonId}`}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      role="tooltip"
      ref={tooltipRef}
      data-testid="suggestion-prompt-tooltip"
      className="fixed z-50 overflow-y-auto rounded-lg border border-[var(--color-border-muted)] bg-[var(--color-bg-secondary)] px-4 py-3 text-sm leading-relaxed text-[var(--color-text-primary)] shadow-lg"
      style={{
        top: placement.top,
        left: placement.left,
        width: placement.width,
        maxHeight: TOOLTIP_MAX_HEIGHT,
      }}
    >
      {prompt}
    </div>,
    document.body,
  );
}

interface SuggestionRowProps {
  suggestion: HomeSuggestion;
  disabled: boolean;
  onSelect: (suggestion: HomeSuggestion) => void | Promise<void>;
  onDismiss: (id: string, summary: string) => void;
}

/**
 * One contextual task summary with its compact source title and a dismiss
 * control. Activation submits the row's exact prompt in a new chat; hover
 * or keyboard focus only shows that exact prompt. There is no Start chat
 * badge, no Preview button and no Why rationale on this row.
 */
export function SuggestionRow({
  suggestion,
  disabled,
  onSelect,
  onDismiss,
}: SuggestionRowProps) {
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const rowRef = useRef<HTMLLIElement | null>(null);
  const hideTimerRef = useRef<number | null>(null);
  const [open, setOpen] = useState(false);

  const clearHideTimer = useCallback(() => {
    if (hideTimerRef.current !== null) {
      window.clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }
  }, []);

  useEffect(() => () => clearHideTimer(), [clearHideTimer]);

  const show = useCallback(() => {
    document.dispatchEvent(
      new CustomEvent('daemon-suggestion-preview', { detail: suggestion.id }),
    );
    clearHideTimer();
    setOpen(true);
  }, [clearHideTimer, suggestion.id]);

  const hideSoon = useCallback(() => {
    clearHideTimer();
    hideTimerRef.current = window.setTimeout(() => setOpen(false), 180);
  }, [clearHideTimer]);

  const hideNow = useCallback(() => {
    clearHideTimer();
    setOpen(false);
  }, [clearHideTimer]);

  useEffect(() => {
    const closeOther = (event: Event) => {
      if ((event as CustomEvent).detail !== suggestion.id) hideNow();
    };
    document.addEventListener('daemon-suggestion-preview', closeOther);
    return () =>
      document.removeEventListener('daemon-suggestion-preview', closeOther);
  }, [hideNow, suggestion.id]);

  // Escape dismisses the open prompt without submitting anything.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      hideNow();
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open, hideNow]);

  // Scroll or resize invalidates the placement; hide rather than let the
  // private prompt float over unrelated content.
  useEffect(() => {
    if (!open) return undefined;
    let scheduled = 0;
    const hideUnlessInTooltip = (event: Event) => {
      const target = event.target as Element | null;
      if (
        target &&
        typeof target.closest === 'function' &&
        target.closest('[role="tooltip"]')
      ) {
        return;
      }
      hideNow();
    };
    const onResize = () => {
      window.cancelAnimationFrame(scheduled);
      scheduled = window.requestAnimationFrame(hideNow);
    };
    window.addEventListener('resize', onResize);
    document.addEventListener('scroll', hideUnlessInTooltip, true);
    return () => {
      window.cancelAnimationFrame(scheduled);
      window.removeEventListener('resize', onResize);
      document.removeEventListener('scroll', hideUnlessInTooltip, true);
    };
  }, [open, hideNow]);

  return (
    <li
      ref={rowRef}
      data-testid={`suggestion-row-${suggestion.id}`}
      className="relative flex items-stretch gap-0 overflow-hidden rounded-xl bg-[var(--color-bg-secondary)] border border-[var(--color-border-primary)] transition-colors duration-200 hover:border-[var(--color-accent-primary)] focus-within:border-[var(--color-accent-primary)]"
    >
      <button
        ref={buttonRef}
        id={`suggestion-row-button-${suggestion.id}`}
        type="button"
        aria-describedby={
          open
            ? `${TOOLTIP_ID}-suggestion-row-button-${suggestion.id}`
            : undefined
        }
        disabled={disabled}
        className="min-h-touch flex flex-1 flex-col items-start gap-1 px-4 py-3 text-left"
        onMouseEnter={show}
        onMouseLeave={hideSoon}
        onFocus={show}
        onBlur={hideNow}
        onClick={() => {
          hideNow();
          if (isHomeSuggestionExpired(suggestion)) return;
          void onSelect(suggestion);
        }}
      >
        <span className="text-sm font-medium text-[var(--color-text-primary)]">
          {suggestion.summary}
        </span>
        <span className="flex w-full items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
          <Sparkles
            className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-accent)]"
            aria-hidden="true"
          />
          <span className="truncate">From “{suggestion.source.title}”</span>
        </span>
      </button>
      <PromptTooltip
        prompt={suggestion.prompt}
        open={open}
        anchor={rowRef}
        onMouseEnter={show}
        onMouseLeave={hideSoon}
        rowButtonId={`suggestion-row-button-${suggestion.id}`}
      />
      <button
        type="button"
        aria-label={`Dismiss suggestion: ${suggestion.summary}`}
        disabled={disabled}
        className="min-h-touch min-w-touch shrink-0 px-2 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-secondary)]"
        onClick={() => {
          hideNow();
          onDismiss(suggestion.id, suggestion.summary);
        }}
      >
        <X className="h-4 w-4" aria-hidden="true" />
      </button>
    </li>
  );
}
