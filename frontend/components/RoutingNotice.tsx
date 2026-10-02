'use client';

import type { RoutingFallback } from '../lib/events';

const FALLBACK_COPY: Readonly<Record<string, string>> = {
  capability_unavailable:
    "Answered with standard reasoning. Deeper reasoning isn't included in your current plan.",
  budget_exceeded:
    "Answered with standard reasoning. Deeper reasoning didn't fit your remaining compute budget.",
};

const DEFAULT_FALLBACK_COPY =
  "Answered with standard reasoning. Deeper reasoning wasn't available for this reply.";

const BUDGET_FITTED_COPY =
  'This reply was sized to fit your remaining compute budget. If it stops early, ask me to continue.';

/**
 * Discloses routing that changed what the user gets: a reply needing deeper
 * reasoning answered on the standard (routine) profile instead of being refused,
 * and a reply whose length was fitted to the remaining budget. Renders nothing
 * when neither applies.
 */
export function RoutingNotice({
  fallback,
  reasonCodes,
}: {
  fallback?: RoutingFallback;
  reasonCodes?: string[];
}) {
  const fallbackMessage = fallback
    ? Object.hasOwn(FALLBACK_COPY, fallback.cause)
      ? FALLBACK_COPY[fallback.cause]
      : DEFAULT_FALLBACK_COPY
    : undefined;
  const budgetFitted = reasonCodes?.includes('budget_fitted_output') ?? false;
  if (!fallbackMessage && !budgetFitted) return null;
  return (
    <div className="space-y-1">
      {fallbackMessage && (
        <p
          role="note"
          data-testid="routing-fallback-notice"
          className="text-xs text-[var(--color-text-secondary)]"
        >
          {fallbackMessage}
        </p>
      )}
      {budgetFitted && (
        <p
          role="note"
          data-testid="routing-budget-fitted-notice"
          className="text-xs text-[var(--color-text-secondary)]"
        >
          {BUDGET_FITTED_COPY}
        </p>
      )}
    </div>
  );
}
