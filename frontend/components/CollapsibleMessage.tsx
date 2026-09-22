'use client';

import { useId, useState, type ReactNode } from 'react';

export function CollapsibleMessage({
  messageId,
  title,
  preview,
  collapsible,
  childrenClassName,
  children,
}: {
  messageId: string;
  title: string;
  preview: string;
  collapsible: boolean;
  childrenClassName?: string;
  children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const contentId = useId();
  const showContent = !collapsible || expanded;

  return (
    <article className="mb-8" data-message-id={messageId} aria-label={title}>
      {collapsible && (
        <div className="rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-2 mb-3">
          <h2 className="text-sm font-medium text-[var(--color-text-secondary)]">
            {title}
          </h2>
          {!showContent && (
            <p className="line-clamp-2 whitespace-pre-wrap text-sm text-[var(--color-text-muted)] mt-1">
              {preview.trim().slice(0, 280) || 'Tool activity or attachments'}
            </p>
          )}
          <button
            type="button"
            aria-expanded={showContent}
            aria-controls={contentId}
            onClick={() => setExpanded((previous) => !previous)}
            className="min-h-[44px] text-sm font-medium text-[var(--color-accent-primary)]"
          >
            {showContent ? 'Show less' : 'Show more'}
          </button>
        </div>
      )}
      <div id={contentId} className={childrenClassName} hidden={!showContent}>
        {showContent && children}
      </div>
    </article>
  );
}
