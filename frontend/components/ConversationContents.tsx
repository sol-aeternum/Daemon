'use client';

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type RefObject,
} from 'react';
import { List, X } from 'lucide-react';
import { currentPrompt, messageElements } from '@/lib/messageNavigation';

export type ContentsEntry = { id: string; label: string };

const desktopQuery = '(min-width: 768px)';
const subscribe = (callback: () => void) => {
  const media = window.matchMedia(desktopQuery);
  media.addEventListener('change', callback);
  return () => media.removeEventListener('change', callback);
};

function ContentsList({
  entries,
  scrollContainerRef,
  onSelect,
  enabled,
}: {
  entries: ContentsEntry[];
  scrollContainerRef: RefObject<HTMLElement | null>;
  onSelect: (id: string) => void;
  enabled: boolean;
}) {
  const [activeId, setActiveId] = useState<string>();
  const listRef = useRef<HTMLOListElement>(null);
  // Assistant token updates do not recreate observers or traverse messages.
  const ids = JSON.stringify(entries.map((entry) => entry.id));
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!enabled || !container) return;
    const wanted = new Set<string>(JSON.parse(ids));
    const elements = messageElements(container).filter((element) =>
      wanted.has(element.dataset.messageId!),
    );
    let frame: number | undefined;
    const update = () => {
      frame = undefined;
      const current = currentPrompt(
        elements,
        container.getBoundingClientRect().top + 24,
      );
      setActiveId(current?.dataset.messageId);
    };
    const schedule = () => {
      if (frame === undefined) frame = requestAnimationFrame(update);
    };
    schedule();
    container.addEventListener('scroll', schedule, { passive: true });
    const observer =
      typeof ResizeObserver === 'undefined'
        ? undefined
        : new ResizeObserver(schedule);
    observer?.observe(container);
    if (container.firstElementChild)
      observer?.observe(container.firstElementChild);
    return () => {
      container.removeEventListener('scroll', schedule);
      observer?.disconnect();
      if (frame !== undefined) cancelAnimationFrame(frame);
    };
  }, [enabled, ids, scrollContainerRef]);

  useEffect(() => {
    const list = listRef.current;
    const button = list?.querySelector<HTMLElement>(
      '[aria-current="location"]',
    );
    if (!enabled || !list || !button) return;
    // Only scroll this list, never the page or the conversation.
    const bounds = list.getBoundingClientRect();
    const item = button.getBoundingClientRect();
    if (item.top < bounds.top) list.scrollTop += item.top - bounds.top;
    else if (item.bottom > bounds.bottom)
      list.scrollTop += item.bottom - bounds.bottom;
  }, [activeId, enabled]);

  return (
    <nav
      aria-label="Conversation contents"
      className="flex min-h-0 flex-1 flex-col"
    >
      <p className="px-4 py-2 text-xs text-[var(--color-text-muted)]">
        Loaded portion only · {entries.length}{' '}
        {entries.length === 1 ? 'prompt' : 'prompts'}
      </p>
      <ol
        ref={listRef}
        className="min-h-0 overflow-y-auto overscroll-y-contain px-2 pb-4"
      >
        {entries.map((entry, index) => (
          <li key={entry.id}>
            <button
              type="button"
              data-contents-message-id={entry.id}
              aria-current={activeId === entry.id ? 'location' : undefined}
              className="flex min-h-touch w-full items-start gap-2 rounded-lg px-2 py-3 text-left text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] aria-[current=location]:bg-[var(--color-accent-subtle)] aria-[current=location]:text-[var(--color-accent-primary)]"
              onClick={() => onSelect(entry.id)}
              onKeyDown={(event) => {
                const next =
                  event.key === 'ArrowDown'
                    ? Math.min(entries.length - 1, index + 1)
                    : event.key === 'ArrowUp'
                      ? Math.max(0, index - 1)
                      : event.key === 'Home'
                        ? 0
                        : event.key === 'End'
                          ? entries.length - 1
                          : -1;
                if (next < 0) return;
                event.preventDefault();
                listRef.current?.querySelectorAll('button')[next]?.focus();
              }}
            >
              <span className="shrink-0 tabular-nums text-[var(--color-text-muted)]">
                {index + 1}.
              </span>
              <span className="min-w-0 truncate">{entry.label}</span>
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}

function ContentsDialog({
  entries,
  scrollContainerRef,
  onNavigate,
  onClose,
}: {
  entries: ContentsEntry[];
  scrollContainerRef: RefObject<HTMLElement | null>;
  onNavigate: (id: string) => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current!;
    const opener = document.activeElement;
    dialog.showModal();
    const media = window.matchMedia(desktopQuery);
    const resize = () => {
      if (media.matches) onClose();
    };
    media.addEventListener('change', resize);
    return () => {
      media.removeEventListener('change', resize);
      dialog.close();
      if (opener instanceof HTMLElement && opener.isConnected)
        opener.focus({ preventScroll: true });
    };
  }, [onClose]);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      data-stop-shortcut-block="true"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onKeyDown={(event) => {
        if (event.key === 'Tab') {
          // Explicitly wrap at the ends: native modal inertness prevents
          // background focus, but Chromium can still Tab into browser chrome.
          const buttons =
            event.currentTarget.querySelectorAll<HTMLButtonElement>(
              'button:not(:disabled)',
            );
          const first = buttons[0];
          const last = buttons[buttons.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
          }
          return;
        }
        if (event.key !== 'Escape') return;
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }}
      className="fixed inset-x-0 bottom-0 top-auto m-0 h-4/5 max-h-full w-full max-w-none rounded-t-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] p-0 text-[var(--color-text-primary)] open:flex open:flex-col backdrop:bg-[var(--color-bg-overlay)]"
    >
      <header className="flex shrink-0 items-center justify-between border-b border-[var(--color-border-primary)] px-4 py-2">
        <h2 id={titleId} className="font-semibold">
          Contents
        </h2>
        <button
          type="button"
          aria-label="Close conversation contents"
          className="flex min-h-touch min-w-touch items-center justify-center rounded-lg hover:bg-[var(--color-bg-hover)]"
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </header>
      <ContentsList
        entries={entries}
        scrollContainerRef={scrollContainerRef}
        enabled
        onSelect={(id) => {
          // Close/restore the opener before the navigation frame focuses the
          // revealed article outside the native modal's inert subtree.
          dialogRef.current?.close();
          onClose();
          onNavigate(id);
        }}
      />
    </dialog>
  );
}

export function ConversationContents({
  entries,
  scrollContainerRef,
  onNavigate,
  mobile = false,
}: {
  entries: ContentsEntry[];
  scrollContainerRef: RefObject<HTMLElement | null>;
  onNavigate: (id: string) => void;
  mobile?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const desktop = useSyncExternalStore(
    subscribe,
    () => window.matchMedia(desktopQuery).matches,
    () => false,
  );
  // Stable callback: streaming renders must not close/reopen the native dialog.
  const close = useCallback(() => setOpen(false), []);
  const titleId = useId();
  if (!entries.length) return null;
  return mobile ? (
    <>
      <button
        type="button"
        aria-label="Open conversation contents"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
        className="flex min-h-touch min-w-touch items-center justify-center rounded-lg hover:bg-[var(--color-bg-hover)] md:hidden"
      >
        <List size={20} />
      </button>
      {open && !desktop && (
        <ContentsDialog
          entries={entries}
          scrollContainerRef={scrollContainerRef}
          onNavigate={onNavigate}
          onClose={close}
        />
      )}
    </>
  ) : (
    <aside
      aria-labelledby={titleId}
      className="hidden w-56 shrink-0 min-h-0 flex-col border-l border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] md:flex"
    >
      <h2 id={titleId} className="px-4 pt-4 text-sm font-semibold">
        Contents
      </h2>
      <ContentsList
        entries={entries}
        scrollContainerRef={scrollContainerRef}
        onSelect={onNavigate}
        enabled={desktop}
      />
    </aside>
  );
}
