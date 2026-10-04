'use client';

import { History } from 'lucide-react';

/**
 * Daemon does not store memory revisions yet, so there is no history to show.
 * Say so plainly instead of requesting an endpoint that does not exist and
 * presenting the failure as an empty or broken history.
 */
export function TrailView() {
  return (
    <section aria-labelledby="memory-history-heading">
      <h3
        id="memory-history-heading"
        className="flex items-center gap-2 text-sm font-medium text-text-secondary"
      >
        <History className="w-4 h-4" />
        Edit history
      </h3>
      <p className="mt-2 text-sm text-text-muted">
        Edit history isn&apos;t available yet. Daemon keeps only the current
        version of each memory.
      </p>
    </section>
  );
}
