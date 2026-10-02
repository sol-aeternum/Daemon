'use client';

import type { RoutingFallback } from '../lib/events';

const FALLBACK_COPY: Record<string, string> = {
  capability_unavailable:
    "Answered with standard reasoning. Deeper reasoning isn't included in your current plan.",
  budget_exceeded:
    "Answered with standard reasoning. Deeper reasoning didn't fit your remaining compute budget.",
};

/**
 * Discloses that a reply needing deeper reasoning was answered on the standard
 * (routine) profile instead of being refused. Renders nothing without a fallback.
 */
export function RoutingNotice({ fallback }: { fallback?: RoutingFallback }) {
  if (!fallback) return null;
  const message =
    FALLBACK_COPY[fallback.cause] ??
    "Answered with standard reasoning. Deeper reasoning wasn't available for this reply.";
  return (
    <p
      role="note"
      data-testid="routing-fallback-notice"
      className="text-xs text-[var(--color-text-secondary)]"
    >
      {message}
    </p>
  );
}
