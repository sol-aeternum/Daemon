'use client';
import { useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { getAuthGeneration, subscribeAuthGeneration } from '@/lib/auth';
import { useAuthGeneration } from '@/hooks/useAuthGeneration';
import {
  isResourceUUID,
  resourceJSON,
  resourcePath,
  resourceResponse,
  type PinnedEnvelope,
  type ResourceItem,
  type ResourcePage,
} from '@/lib/resources';

export function ResourceCard({ item }: { item: ResourceItem }) {
  const generation = useAuthGeneration();
  const [preview, setPreview] = useState<{
    generation: number;
    url?: string;
    text?: string;
  } | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const lifetime = useRef<{ controller: AbortController; url?: string } | null>(
    null,
  );
  useEffect(() => {
    const clear = () => {
      lifetime.current?.controller.abort();
      if (lifetime.current?.url) URL.revokeObjectURL(lifetime.current.url);
      lifetime.current = null;
      setPreview(null);
      setBusy(false);
      setError(false);
    };
    const unsubscribe = subscribeAuthGeneration(clear);
    return () => {
      unsubscribe();
      clear();
    };
  }, []);
  const load = async (download: boolean) => {
    lifetime.current?.controller.abort();
    if (lifetime.current?.url) URL.revokeObjectURL(lifetime.current.url);
    const controller = new AbortController();
    lifetime.current = { controller };
    setBusy(true);
    setError(false);
    setPreview(null);
    const current = () =>
      !controller.signal.aborted && getAuthGeneration() === generation;
    try {
      const response = await resourceResponse(
        `${resourcePath(item.resource_id, item.version_id)}/${download ? 'content' : 'preview'}`,
        controller.signal,
        generation,
      );
      if (!current()) return;
      if (!download && ['text/plain', 'text/csv'].includes(item.mime)) {
        const text = await response.text();
        if (current()) setPreview({ generation, text });
      } else {
        const blob = await response.blob();
        if (!current()) return;
        const url = URL.createObjectURL(blob);
        lifetime.current = { controller, url };
        if (download) {
          const link = document.createElement('a');
          link.href = url;
          const extension =
            item.mime === 'image/png'
              ? '.png'
              : item.mime === 'text/csv'
                ? '.csv'
                : item.mime === 'text/plain'
                  ? '.txt'
                  : '.bin';
          link.download =
            (item.display.name || 'resource')
              .replace(/[^\p{L}\p{N}._ -]/gu, '_')
              .replace(/\.[^.]*$/, '') + extension;
          link.click();
        } else setPreview({ generation, url });
      }
    } catch {
      if (current()) setError(true);
    } finally {
      if (current()) setBusy(false);
    }
  };
  const shown = preview?.generation === generation ? preview : null;
  return (
    <article className="rounded-xl border border-[var(--color-border-primary)] p-3 min-w-0">
      <p className="font-medium break-words">
        {item.display.name || item.display.title || 'Retained resource'}
      </p>
      <p className="text-xs text-[var(--color-text-secondary)]">
        {item.representation.replaceAll('_', ' ')} ·{' '}
        {item.measured_bytes === null
          ? 'Original bytes not received'
          : `${item.measured_bytes} bytes`}
      </p>
      <p className="text-xs break-all">Version {item.version_id}</p>
      {item.reference?.source_deleted && (
        <p>Source chat deleted; resource retained.</p>
      )}
      {item.availability !== 'available' ? (
        <p role="status">
          {item.availability === 'original_not_received'
            ? 'Only submitted metadata was received.'
            : 'Resource unavailable or verification uncertain.'}
        </p>
      ) : (
        <div className="flex gap-3 mt-2">
          {['image/png', 'text/plain', 'text/csv'].includes(item.mime) && (
            <button
              type="button"
              disabled={busy}
              className="min-h-touch underline"
              onClick={() => void load(false)}
            >
              Preview
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            className="min-h-touch underline"
            onClick={() => void load(true)}
          >
            Download
          </button>
        </div>
      )}
      {busy && <p role="status">Loading verified content…</p>}
      {error && (
        <p role="alert">
          Could not read this version. Retry using Preview or Download.
        </p>
      )}
      {shown?.text !== undefined && (
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words">
          {shown.text}
        </pre>
      )}
      {shown?.url && (
        <Image
          src={shown.url}
          unoptimized
          width={640}
          height={480}
          alt={item.display.name || 'Submitted image'}
          className="max-h-80 object-contain"
        />
      )}
    </article>
  );
}

export function TaskResources({
  taskId,
  revision,
}: {
  taskId: string;
  revision?: string;
}) {
  const generation = useAuthGeneration();
  const [state, setState] = useState<{
    taskId: string;
    generation: number;
    items: ResourceItem[];
    error: boolean;
    more: string | null;
  } | null>(null);
  const [cursor, setCursor] = useState<{
    taskId: string;
    value: string;
  } | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!isResourceUUID(taskId)) return;
    const controller = new AbortController();
    const clear = () => {
      controller.abort();
      setState(null);
      setCursor(null);
    };
    const unsubscribe = subscribeAuthGeneration(clear);
    async function load() {
      try {
        const inputs = await resourceJSON<ResourcePage>(
          `/tasks/${taskId}/inputs${cursor?.taskId === taskId ? `?cursor=${encodeURIComponent(cursor.value)}` : ''}`,
          controller.signal,
          generation,
        );
        const presentations = await resourceJSON<{ items: PinnedEnvelope[] }>(
          `/tasks/${taskId}/presentations`,
          controller.signal,
          generation,
        );
        const items = [...inputs.items];
        for (const envelope of presentations.items) {
          if (!Array.isArray(envelope.items) || envelope.items.length > 100)
            continue;
          for (const pin of envelope.items) {
            if (
              !isResourceUUID(pin.resource_id) ||
              !isResourceUUID(pin.version_id) ||
              items.some((i) => i.version_id === pin.version_id)
            )
              continue;
            const result = await resourceJSON<{ item: ResourceItem }>(
              resourcePath(pin.resource_id, pin.version_id),
              controller.signal,
              generation,
            );
            items.push(result.item);
          }
        }
        if (!controller.signal.aborted && getAuthGeneration() === generation)
          setState((previous) => ({
            taskId,
            generation,
            items:
              cursor?.taskId === taskId &&
              previous?.taskId === taskId &&
              previous.generation === generation
                ? [
                    ...previous.items,
                    ...items.filter(
                      (i) =>
                        !previous.items.some(
                          (p) => p.version_id === i.version_id,
                        ),
                    ),
                  ]
                : items,
            error: false,
            more: inputs.next_cursor,
          }));
      } catch {
        if (!controller.signal.aborted && getAuthGeneration() === generation)
          setState({ taskId, generation, items: [], error: true, more: null });
      }
    }
    void load();
    return () => {
      unsubscribe();
      controller.abort();
    };
  }, [taskId, revision, generation, retry, cursor]);
  const current =
    state?.generation === generation && state.taskId === taskId ? state : null;
  if (!current || (!current.error && !current.items.length)) return null;
  return (
    <section aria-label="Retained task resources" className="grid gap-2 my-3">
      {current.error ? (
        <p>
          Retained resources unavailable.{' '}
          <button
            type="button"
            className="underline min-h-touch"
            onClick={() => {
              setCursor(null);
              setRetry((n) => n + 1);
            }}
          >
            Retry
          </button>
        </p>
      ) : (
        current.items.map((item) => (
          <ResourceCard key={item.version_id} item={item} />
        ))
      )}
      {current.more && (
        <button
          type="button"
          className="min-h-touch underline"
          onClick={() => setCursor({ taskId, value: current.more! })}
        >
          Load more inputs
        </button>
      )}
    </section>
  );
}
