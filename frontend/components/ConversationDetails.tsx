'use client';

import { useEffect, useId, useRef, useSyncExternalStore } from 'react';
import { X, ArrowLeft } from 'lucide-react';
import { buildMessageCitationSources } from '@/lib/messageSources';
import {
  dedupeSources,
  pairToolExecutions,
  parseToolResultPayload,
  extractToolFailure,
} from '@/lib/toolActivity';
import {
  getConversationOutputs,
  type ConversationOutput,
  type DetailTurn,
} from '@/lib/conversationDetails';
import { FileDownloadCard } from './FileDownloadCard';
import { FilePreview } from '@/src/components/FilePreview';
import { RetainedSources } from './RetainedSources';

export type DetailsTab = 'Sources' | 'Outputs' | 'Activity';
const tabs: DetailsTab[] = ['Sources', 'Outputs', 'Activity'];
const query = '(min-width: 1100px)';
const subscribe = (callback: () => void) => {
  const media = window.matchMedia(query);
  media.addEventListener('change', callback);
  return () => media.removeEventListener('change', callback);
};
export function useWideDetails() {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
}

interface Props {
  conversationId?: string | null;
  turns: DetailTurn[];
  tab: DetailsTab;
  onTab: (tab: DetailsTab) => void;
  selected?: ConversationOutput;
  onSelect: (output?: ConversationOutput) => void;
  onClose: () => void;
  modal: boolean;
}

export function ConversationDetails({
  conversationId = null,
  turns,
  tab,
  onTab,
  selected,
  onSelect,
  onClose,
  modal,
}: Props) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const previewHeading = useRef<HTMLHeadingElement>(null);
  const previousSelection = useRef<string | undefined>(undefined);
  const sources = dedupeSources(
    turns.flatMap((turn) => buildMessageCitationSources(turn.events)),
  );
  const outputs = [
    ...new Map(
      turns
        .flatMap((turn) => getConversationOutputs(turn.events))
        .map((output) => [output.fileUrl, output]),
    ).values(),
  ];
  useEffect(() => {
    if (modal) dialog.current?.showModal();
    closeButton.current?.focus({ preventScroll: true });
    const element = dialog.current;
    return () => element?.close();
  }, [modal]);
  useEffect(() => {
    if (modal) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      // A nested modal gets the first Escape; the conversation remains running.
      if (
        document.querySelector(
          'dialog[open], [role="dialog"][aria-modal="true"]',
        )
      )
        return;
      event.preventDefault();
      onClose();
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [modal, onClose]);
  useEffect(() => {
    const previous = previousSelection.current;
    previousSelection.current = selected?.fileUrl;
    if (selected?.fileUrl)
      previewHeading.current?.focus({ preventScroll: true });
    else if (previous) {
      const buttons = document.querySelectorAll<HTMLButtonElement>(
        `[data-output-preview]`,
      );
      [...buttons]
        .find((button) => button.dataset.outputPreview === previous)
        ?.focus({ preventScroll: true });
    }
  }, [selected?.fileUrl]);

  const content = (
    <>
      <header className="flex items-center justify-between gap-2 border-b border-[var(--color-border-primary)] px-4 py-2">
        <h2 id={`${id}-title`} className="font-semibold">
          Conversation details
        </h2>
        <button
          ref={closeButton}
          type="button"
          onClick={onClose}
          aria-label="Close conversation details"
          className="min-h-touch min-w-touch flex items-center justify-center rounded-lg hover:bg-[var(--color-bg-hover)]"
        >
          <X size={20} />
        </button>
      </header>
      <div
        role="tablist"
        aria-label="Conversation details"
        className="flex border-b border-[var(--color-border-primary)] px-2"
      >
        {tabs.map((name, index) => (
          <button
            key={name}
            id={`${id}-${name}`}
            role="tab"
            type="button"
            aria-selected={tab === name}
            aria-controls={`${id}-panel`}
            tabIndex={tab === name ? 0 : -1}
            className={`min-h-touch flex-1 rounded-t-lg px-2 text-sm ${tab === name ? 'bg-[var(--color-accent-subtle)] text-[var(--color-text-primary)]' : 'text-[var(--color-text-secondary)]'}`}
            onClick={() => {
              onSelect(undefined);
              onTab(name);
            }}
            onKeyDown={(event) => {
              const next =
                event.key === 'ArrowRight'
                  ? (index + 1) % 3
                  : event.key === 'ArrowLeft'
                    ? (index + 2) % 3
                    : event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? 2
                        : -1;
              if (next < 0) return;
              event.preventDefault();
              onSelect(undefined);
              onTab(tabs[next]);
              document.getElementById(`${id}-${tabs[next]}`)?.focus();
            }}
          >
            {name}
          </button>
        ))}
      </div>
      <div
        id={`${id}-panel`}
        role="tabpanel"
        aria-labelledby={`${id}-${tab}`}
        tabIndex={0}
        className="min-h-0 flex-1 overflow-auto p-4 space-y-4"
      >
        {tab === 'Sources' && (
          <>
            <p className="text-sm text-[var(--color-text-secondary)]">
              Sources returned in this conversation. Links do not imply that
              every claim was verified.
            </p>
            {sources.length ? (
              sources.map((source) => (
                <a
                  key={source.url}
                  href={source.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block min-h-touch rounded-xl border border-[var(--color-border-primary)] p-3 hover:bg-[var(--color-bg-hover)] break-words"
                >
                  <span className="block text-sm font-medium">
                    {source.title || source.domain}
                  </span>
                  <span className="text-xs text-[var(--color-text-secondary)]">
                    {source.domain}
                  </span>
                </a>
              ))
            ) : (
              <p className="text-sm">No returned sources yet.</p>
            )}
            <div className="border-t border-[var(--color-border-primary)] pt-4">
              <RetainedSources conversationId={conversationId} />
            </div>
          </>
        )}
        {tab === 'Outputs' && (
          <>
            <p className="text-sm text-[var(--color-text-secondary)]">
              Files returned in this conversation are temporary and may become
              unavailable.
            </p>
            {selected ? (
              <>
                <button
                  type="button"
                  className="min-h-touch inline-flex items-center gap-2 text-sm"
                  onClick={() => onSelect(undefined)}
                >
                  <ArrowLeft size={16} />
                  All outputs
                </button>
                <h3
                  ref={previewHeading}
                  tabIndex={-1}
                  className="font-medium break-all"
                >
                  {selected.filename}
                </h3>
                <FileDownloadCard key={selected.fileUrl} {...selected} />
                <FilePreview
                  fileUrl={selected.fileUrl}
                  filename={selected.filename}
                  format={selected.fileType || ''}
                  fileSize={selected.fileSize}
                />
              </>
            ) : outputs.length ? (
              outputs.map((output) => (
                <FileDownloadCard
                  key={output.fileUrl}
                  {...output}
                  trailingAction={
                    <button
                      type="button"
                      data-output-preview={output.fileUrl}
                      className="min-h-touch rounded-lg border border-[var(--color-border-primary)] px-3 text-sm"
                      onClick={() => onSelect(output)}
                    >
                      Preview<span className="sr-only"> {output.filename}</span>
                    </button>
                  }
                />
              ))
            ) : (
              <p className="text-sm">No completed files returned yet.</p>
            )}
          </>
        )}
        {tab === 'Activity' && (
          <>
            <p className="text-sm text-[var(--color-text-secondary)]">
              Reported tool activity, not durable task status. Missing results
              remain unknown when a response ends.
            </p>
            {turns.some((turn) => pairToolExecutions(turn.events).length) ? (
              turns.map((turn, index) => {
                const executions = pairToolExecutions(turn.events);
                if (!executions.length) return null;
                return (
                  <section key={turn.id} className="space-y-2">
                    <h3 className="text-sm font-semibold">
                      Response {index + 1}
                      {turn.stopped ? ' · stopped' : ''}
                    </h3>
                    {executions.map(({ call, result }, i) => {
                      if (call.type !== 'tool_call') return null;
                      const error =
                        result?.type === 'tool_result'
                          ? extractToolFailure(
                              parseToolResultPayload(result.result),
                            )
                          : null;
                      const status = error
                        ? 'Reported an issue'
                        : result
                          ? 'Result returned'
                          : turn.running
                            ? 'Running'
                            : 'Outcome unknown';
                      return (
                        <div
                          key={`${call.id || call.name}-${i}`}
                          className="rounded-xl border border-[var(--color-border-primary)] p-3"
                        >
                          <p className="text-sm font-medium break-words">
                            {call.name}
                          </p>
                          <p className="text-sm text-[var(--color-text-secondary)]">
                            {status}
                          </p>
                          {error && (
                            <p className="text-sm break-words">{error}</p>
                          )}
                        </div>
                      );
                    })}
                  </section>
                );
              })
            ) : (
              <p className="text-sm">No tool activity recorded.</p>
            )}
          </>
        )}
      </div>
    </>
  );
  const keyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape' && !event.defaultPrevented) {
      event.preventDefault();
      event.stopPropagation();
      onClose();
    }
    if (!modal || event.key !== 'Tab') return;
    const controls = [
      ...event.currentTarget.querySelectorAll<HTMLElement>(
        'button:not([disabled]):not([tabindex="-1"]), a[href], [tabindex="0"]',
      ),
    ].filter((element) => element.getClientRects().length > 0);
    const first = controls[0],
      last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  };
  return modal ? (
    <dialog
      ref={dialog}
      aria-labelledby={`${id}-title`}
      data-stop-shortcut-block="true"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onKeyDown={keyDown}
      className="fixed inset-0 m-0 h-dvh max-h-none w-screen max-w-none border-0 bg-[var(--color-bg-primary)] text-[var(--color-text-primary)] p-0 open:flex open:flex-col backdrop:bg-[var(--color-bg-overlay)]"
    >
      {content}
    </dialog>
  ) : (
    <aside
      aria-labelledby={`${id}-title`}
      data-stop-shortcut-block="true"
      onKeyDown={keyDown}
      className="flex h-full min-h-0 flex-col bg-[var(--color-bg-primary)]"
    >
      {content}
    </aside>
  );
}
