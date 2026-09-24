'use client';

import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';

// Messages shorter than this stay expanded; collapsing them costs a click
// without saving meaningful space.
export const COLLAPSE_MIN_CHARS = 1200;

const noopSubscribe = () => () => {};

// `hidden="until-found"` keeps collapsed text reachable by browser find:
// a match fires `beforematch`, which expands the message. Browsers without
// it would hide the text from find entirely, so they never collapse.
function supportsUntilFound() {
  return 'onbeforematch' in HTMLElement.prototype;
}

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
  const contentRef = useRef<HTMLDivElement>(null);
  const canCollapse = useSyncExternalStore(
    noopSubscribe,
    supportsUntilFound,
    () => false,
  );
  const showToggle = collapsible && canCollapse;
  const collapsed = showToggle && !expanded;

  // React treats `hidden` as a boolean, so the until-found value is managed
  // directly. Layout effect avoids a frame of expanded content.
  useLayoutEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    if (collapsed) content.setAttribute('hidden', 'until-found');
    else content.removeAttribute('hidden');
  }, [collapsed]);

  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const reveal = () => setExpanded(true);
    content.addEventListener('beforematch', reveal);
    return () => content.removeEventListener('beforematch', reveal);
  }, []);

  return (
    <article className="mb-8" data-message-id={messageId} aria-label={title}>
      {showToggle && (
        <div className="rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-2 mb-3">
          <h2 className="text-sm font-medium text-[var(--color-text-secondary)]">
            {title}
          </h2>
          {collapsed && (
            <p className="line-clamp-2 whitespace-pre-wrap text-sm text-[var(--color-text-muted)] mt-1">
              {preview.trim().slice(0, 280)}
            </p>
          )}
          <button
            type="button"
            aria-expanded={!collapsed}
            aria-controls={contentId}
            onClick={() => setExpanded((previous) => !previous)}
            className="min-h-touch text-sm font-medium text-[var(--color-accent-primary)]"
          >
            {collapsed ? 'Show more' : 'Show less'}
          </button>
        </div>
      )}
      <div id={contentId} ref={contentRef}>
        <div className={childrenClassName}>{children}</div>
      </div>
    </article>
  );
}
