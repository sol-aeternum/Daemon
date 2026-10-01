'use client';

import { useEffect, useRef, useState, useId } from 'react';
import { createPortal } from 'react-dom';
import { Copy, Check } from 'lucide-react';

export function CopyResponseButton({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  const [fallback, setFallback] = useState<string | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const mounted = useRef(true);
  const id = useId();
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);
  useEffect(() => {
    if (fallback === null) return;
    const node = dialog.current;
    node?.showModal();
    textarea.current?.focus();
    textarea.current?.select();
    return () => node?.close();
  }, [fallback]);
  const close = () => {
    setFallback(null);
    trigger.current?.focus({ preventScroll: true });
  };
  const copy = async () => {
    const text = content;
    try {
      if (!navigator.clipboard?.writeText)
        throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text);
      if (mounted.current) setCopied(true);
    } catch {
      if (mounted.current) setFallback(text);
    }
  };
  return (
    <>
      <button
        ref={trigger}
        type="button"
        onClick={() => void copy()}
        disabled={!content.trim()}
        className="inline-flex min-h-touch items-center gap-2 rounded-lg px-2 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
        aria-label="Copy response"
      >
        {copied ? <Check size={16} /> : <Copy size={16} />}
        <span role="status">{copied ? 'Copied' : 'Copy response'}</span>
      </button>
      {fallback !== null &&
        createPortal(
          <dialog
            ref={dialog}
            aria-labelledby={`${id}-title`}
            data-stop-shortcut-block="true"
            onCancel={(event) => {
              event.preventDefault();
              close();
            }}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                close();
              }
              if (event.key !== 'Tab') return;
              const controls =
                event.currentTarget.querySelectorAll<HTMLElement>(
                  'textarea, button',
                );
              if (event.shiftKey && document.activeElement === controls[0]) {
                event.preventDefault();
                controls[controls.length - 1]?.focus();
              } else if (
                !event.shiftKey &&
                document.activeElement === controls[controls.length - 1]
              ) {
                event.preventDefault();
                controls[0]?.focus();
              }
            }}
            className="w-full max-w-lg rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] text-[var(--color-text-primary)] p-4 backdrop:bg-[var(--color-bg-overlay)]"
          >
            <h2 id={`${id}-title`} className="font-semibold">
              Copy response manually
            </h2>
            <p className="my-3 text-sm text-[var(--color-text-secondary)]">
              Clipboard access was unavailable. Copy the selected response with
              your device’s copy command.
            </p>
            <textarea
              ref={textarea}
              aria-label="Response to copy"
              readOnly
              value={fallback}
              rows={8}
              className="w-full rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-input)] p-3 text-sm"
            />
            <button
              type="button"
              onClick={close}
              className="mt-3 min-h-touch rounded-lg border border-[var(--color-border-primary)] px-4"
            >
              Close
            </button>
          </dialog>,
          document.body,
        )}
    </>
  );
}
