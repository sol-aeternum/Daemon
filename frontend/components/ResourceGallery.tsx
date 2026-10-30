'use client';
import { useEffect, useState } from 'react';
import { getAuthGeneration, subscribeAuthGeneration } from '@/lib/auth';
import { useAuthGeneration } from '@/hooks/useAuthGeneration';
import {
  resourceJSON,
  ResourceReadError,
  type ResourceItem,
  type ResourcePage,
} from '@/lib/resources';
import { ResourceCard } from './ResourceCards';
export function ResourceGallery() {
  const generation = useAuthGeneration();
  const [kind, setKind] = useState('');
  const [role, setRole] = useState('');
  const [availability, setAvailability] = useState('');
  const [extra, setExtra] = useState({
    mime: '',
    task_id: '',
    job_id: '',
    created_after: '',
    created_before: '',
  });
  const [state, setState] = useState<{
    generation: number;
    key: string;
    items: ResourceItem[];
    cursor: string | null;
    error?: string;
  } | null>(null);
  const [page, setPage] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const key = `${kind}|${role}|${availability}|${JSON.stringify(extra)}`;
  const visible =
    state?.generation === generation && state.key === key ? state : null;
  useEffect(() => {
    const controller = new AbortController();
    const unsubscribe = subscribeAuthGeneration(() => {
      controller.abort();
      setState(null);
      setPage(null);
    });
    const query = new URLSearchParams();
    if (kind) query.set('kind', kind);
    if (role) query.set('role', role);
    if (availability) query.set('availability', availability);
    for (const [field, value] of Object.entries(extra))
      if (value) query.set(field, value);
    if (page) query.set('cursor', page);
    void resourceJSON<ResourcePage>(
      `/resources?${query}`,
      controller.signal,
      generation,
    )
      .then((result) => {
        if (controller.signal.aborted || getAuthGeneration() !== generation)
          return;
        setState((previous) => ({
          generation,
          key,
          items:
            page && previous?.generation === generation && previous.key === key
              ? [...previous.items, ...result.items]
              : result.items,
          cursor: result.next_cursor,
        }));
      })
      .catch((error) => {
        if (controller.signal.aborted || getAuthGeneration() !== generation)
          return;
        setState({
          generation,
          key,
          items: [],
          cursor: null,
          error:
            error instanceof ResourceReadError ? error.code : 'read_failed',
        });
      });
    return () => {
      unsubscribe();
      controller.abort();
    };
  }, [kind, role, availability, extra, key, page, retry, generation]);
  const filter = (setter: (value: string) => void, value: string) => {
    setPage(null);
    setState(null);
    setter(value);
  };
  return (
    <section aria-label="All retained artifact versions" className="mb-8">
      <h2 className="text-xl font-semibold">All retained resources</h2>
      <p className="text-sm text-[var(--color-text-secondary)] mb-3">
        Accepted representations and generated files across all chats. Versions
        stay pinned.
      </p>
      <div className="flex flex-wrap gap-3 mb-4">
        <label>
          Kind{' '}
          <select
            value={kind}
            onChange={(e) => filter(setKind, e.target.value)}
          >
            <option value="">All</option>
            <option value="image">Image</option>
            <option value="text">Text</option>
            <option value="file">File</option>
          </select>
        </label>
        <label>
          Role{' '}
          <select
            value={role}
            onChange={(e) => filter(setRole, e.target.value)}
          >
            <option value="">All</option>
            <option value="input">Input</option>
            <option value="output">Output</option>
          </select>
        </label>
        <label>
          Availability{' '}
          <select
            value={availability}
            onChange={(e) => filter(setAvailability, e.target.value)}
          >
            <option value="">All</option>
            <option value="available">Available</option>
            <option value="original_not_received">Original not received</option>
            <option value="uncertain">Uncertain</option>
          </select>
        </label>
      </div>
      <details className="mb-4">
        <summary className="min-h-touch cursor-pointer">
          More structured filters
        </summary>
        <div className="grid gap-2 md:grid-cols-2">
          <label>
            MIME{' '}
            <select
              value={extra.mime}
              onChange={(e) => {
                setPage(null);
                setExtra({ ...extra, mime: e.target.value });
              }}
            >
              <option value="">All</option>
              {[
                'image/png',
                'text/plain',
                'text/csv',
                'application/octet-stream',
              ].map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          {(
            ['task_id', 'job_id', 'created_after', 'created_before'] as const
          ).map((field) => (
            <label key={field}>
              {field.replaceAll('_', ' ')}
              <input
                className="w-full min-h-touch border rounded p-2"
                value={extra[field]}
                placeholder={
                  field.endsWith('_id')
                    ? 'Exact UUID'
                    : 'UTC timestamp, e.g. 2026-01-01T00:00:00Z'
                }
                onChange={(e) => {
                  setPage(null);
                  setExtra({ ...extra, [field]: e.target.value });
                }}
              />
            </label>
          ))}
        </div>
      </details>
      {!visible && <p role="status">Loading catalog…</p>}
      {visible?.error && (
        <p role="status">
          {visible.error === 'restart_required'
            ? 'Catalog changed. Restart to load a consistent page.'
            : 'Durable catalog unavailable; no resources were regenerated.'}{' '}
          <button
            type="button"
            className="underline min-h-touch"
            onClick={() => {
              setPage(null);
              setState(null);
              setRetry((n) => n + 1);
            }}
          >
            {visible.error === 'restart_required' ? 'Restart' : 'Retry'}
          </button>
        </p>
      )}
      {visible && !visible.error && !visible.items.length && (
        <p>No retained versions match these filters.</p>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {visible?.items.map((item) => (
          <ResourceCard key={item.version_id} item={item} />
        ))}
      </div>
      {visible?.cursor && (
        <button
          type="button"
          className="min-h-touch underline mt-4"
          onClick={() => setPage(visible.cursor)}
        >
          Load more
        </button>
      )}
    </section>
  );
}
