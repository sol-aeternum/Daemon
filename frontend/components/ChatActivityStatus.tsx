'use client';

import type { ChatEvent } from '../lib/events';
import { useAgentStatus } from '../hooks/useAgentStatus';

export function ChatActivityStatus({
  events,
  isLoading,
}: {
  events: ChatEvent[];
  isLoading: boolean;
}) {
  const agents = useAgentStatus(events);
  if (!isLoading) return null;

  const routing = events.findLast((event) => event.type === 'routing');
  const activeCount = agents.filter(
    (agent) => agent.status === 'pending' || agent.status === 'running',
  ).length;
  const modelName =
    routing?.type === 'routing' ? routing.model.split('/').at(-1) : undefined;

  return (
    <span
      role="status"
      className="min-w-0 rounded-full border border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] px-3 py-1 text-xs text-[var(--color-text-secondary)]"
    >
      <span
        className="block max-w-xs truncate font-mono"
        title={routing?.type === 'routing' ? routing.model : undefined}
      >
        {modelName || 'Choosing model…'}
      </span>
      <span>
        {activeCount > 0
          ? `orchestrating · ${activeCount} active`
          : 'responding'}
      </span>
    </span>
  );
}
