'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuthGeneration } from '@/hooks/useAuthGeneration';
import {
  ensureAuthHeader,
  getAuthGeneration,
  subscribeAuthGeneration,
} from '@/lib/auth';
import { isSnapshotId, webSnapshotApiUrl } from '@/lib/webSnapshotPaths';
import {
  parseSnapshotListPage,
  awaitSnapshotOperation,
  readBoundedExport,
  snapshotExportFilename,
  SnapshotExportTooLargeError,
  SNAPSHOT_EXPORT_MAX_BYTES,
  SNAPSHOT_LIST_LIMIT,
  type SnapshotPage,
  type SnapshotRow,
} from '@/lib/webSnapshots';

const LIST_TIMEOUT_MS = 12_000;
const EXPORT_TIMEOUT_MS = 30_000;
const DELETE_TIMEOUT_MS = 12_000;

const LIST_UNAVAILABLE =
  'Retained sources are unavailable for this conversation.';
const LIST_LOAD_FAILED = 'Retained sources could not be loaded.';
const AUTH_UNAVAILABLE =
  'Sign in to view this conversation’s retained sources.';
const EXPORT_AUTH_NEEDED =
  'Snapshot export needs an active session. Sign in and try again.';
const EXPORT_UNAVAILABLE = 'Snapshot export is unavailable right now.';
const EXPORT_TOO_LARGE = 'Snapshot export is larger than the download limit.';
const DELETE_UNKNOWN =
  'The removal outcome is unknown. Refresh to check whether the snapshot was removed.';
const DELETE_GONE =
  'This snapshot is unavailable. Refresh to reconcile the list.';
const ROW_EXPIRED =
  'This retained snapshot has expired. Refresh to update the list.';

type RunningOp = 'list' | 'export' | 'delete' | null;

interface Props {
  conversationId: string | null;
}

/** Sources tab section for owned retained snapshots. Mounted only on the
 * Sources tab; keyed by auth generation + conversation so a sign-in change
 * never renders earlier lifetime metadata. */
export function RetainedSources({ conversationId }: Props) {
  const generation = useAuthGeneration();
  if (!conversationId || !isSnapshotId(conversationId)) {
    return (
      <p className="text-sm text-[var(--color-text-secondary)]">
        {LIST_UNAVAILABLE}
      </p>
    );
  }
  return (
    <RetainedSourcesInner
      key={`${generation}:${conversationId}`}
      conversationId={conversationId}
      generation={generation}
    />
  );
}

interface InnerProps {
  conversationId: string;
  generation: number;
}

function RetainedSourcesInner({ conversationId, generation }: InnerProps) {
  const [page, setPage] = useState<SnapshotPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState<'loading' | 'ready' | 'auth' | 'error'>(
    'loading',
  );
  const [notice, setNotice] = useState<string | null>(null);
  const [running, setRunning] = useState<RunningOp>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [needsReconcile, setNeedsReconcile] = useState(false);

  const mountedRef = useRef(true);
  const runningRef = useRef<RunningOp>(null);
  const listAbortRef = useRef<AbortController | null>(null);
  const opAbortRef = useRef<AbortController | null>(null);
  const rootRef = useRef<HTMLElement | null>(null);
  const pendingFocusRef = useRef<string | null>(null);
  const cancelButtonRef = useRef<HTMLButtonElement | null>(null);
  const current = useCallback(
    () => mountedRef.current && generation === getAuthGeneration(),
    [generation],
  );

  const setRunningSafe = useCallback((value: RunningOp) => {
    runningRef.current = value;
    if (mountedRef.current) setRunning(value);
  }, []);

  // Pending list/export/delete operations all abort on global auth change
  // (a new sign-in lifetime) and again on unmount, which also happens on
  // scope/generation key change.
  useEffect(() => {
    const abortPending = () => {
      listAbortRef.current?.abort();
      opAbortRef.current?.abort();
    };
    const unsubscribe = subscribeAuthGeneration(abortPending);
    return () => {
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      listAbortRef.current?.abort();
      opAbortRef.current?.abort();
      listAbortRef.current = null;
      opAbortRef.current = null;
      runningRef.current = null;
    };
  }, []);

  // Return focus to the invoking Remove button after cancelling (the button
  // only reappears after the confirmation unmounts).
  useEffect(() => {
    if (confirmId) {
      cancelButtonRef.current?.focus({ preventScroll: true });
    } else if (pendingFocusRef.current) {
      rootRef.current
        ?.querySelector<HTMLButtonElement>(
          `[data-snapshot-id="${pendingFocusRef.current}"] [data-removal-trigger]`,
        )
        ?.focus({ preventScroll: true });
      pendingFocusRef.current = null;
    }
  }, [confirmId]);

  const load = useCallback(
    async (targetOffset: number): Promise<void> => {
      if (opAbortRef.current || listAbortRef.current || runningRef.current)
        return; // busy guard: no concurrent operations
      const controller = new AbortController();
      listAbortRef.current = controller;
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, LIST_TIMEOUT_MS);
      setRunningSafe('list');
      setConfirmId(null);
      setNotice(null);
      setStatus('loading');
      setPage(null);
      try {
        const authHeader = await awaitSnapshotOperation(
          ensureAuthHeader(),
          controller.signal,
        );
        if (controller.signal.aborted || !current()) return;
        if (!authHeader) {
          setStatus('auth');
          setPage(null);
          return;
        }
        if (controller.signal.aborted || !current()) return;
        let url: URL;
        try {
          url = new URL(webSnapshotApiUrl(conversationId));
        } catch {
          setStatus('error');
          setPage(null);
          return;
        }
        url.searchParams.set('limit', String(SNAPSHOT_LIST_LIMIT));
        url.searchParams.set('offset', String(targetOffset));
        const response = await awaitSnapshotOperation(
          fetch(url.href, {
            method: 'GET',
            headers: { Authorization: authHeader },
            cache: 'no-store',
            credentials: 'omit',
            redirect: 'error',
            signal: controller.signal,
          }),
          controller.signal,
        );
        if (controller.signal.aborted || !current()) return;
        if (!response.ok) {
          setStatus(
            response.status === 401 || response.status === 403
              ? 'auth'
              : 'error',
          );
          return;
        }
        let body: unknown;
        try {
          body = await awaitSnapshotOperation(
            response.json(),
            controller.signal,
          );
        } catch {
          if (controller.signal.aborted)
            throw new DOMException('Aborted', 'AbortError');
          setStatus('error');
          return;
        }
        if (controller.signal.aborted || !current()) return;
        let parsed: SnapshotPage;
        try {
          parsed = parseSnapshotListPage(body, conversationId, targetOffset);
        } catch {
          setStatus('error');
          return;
        }
        if (controller.signal.aborted || !current()) return;
        setPage(parsed);
        setNeedsReconcile(false);
        setOffset(targetOffset);
        setStatus('ready');
      } catch {
        if (!current() || (controller.signal.aborted && !timedOut)) return; // external abort (unmount/auth change): leave state alone
        if (mountedRef.current) {
          setStatus('error');
          setPage(null);
        }
      } finally {
        clearTimeout(timer);
        if (listAbortRef.current === controller) {
          listAbortRef.current = null;
          setRunningSafe(null);
        }
      }
    },
    [conversationId, current, setRunningSafe],
  );

  useEffect(() => {
    void load(0);
    return () => {
      listAbortRef.current?.abort();
    };
  }, [load]);

  const confirmRemove = useCallback(
    async (row: SnapshotRow): Promise<void> => {
      if (
        !current() ||
        confirmId !== row.id ||
        !page?.rows.includes(row) ||
        opAbortRef.current ||
        runningRef.current
      )
        return;
      if (row.expiresAt <= Date.now()) {
        setConfirmId(null);
        setNotice(ROW_EXPIRED);
        return;
      }
      const controller = new AbortController();
      opAbortRef.current = controller;
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, DELETE_TIMEOUT_MS);
      setRunningSafe('delete');
      try {
        const authHeader = await awaitSnapshotOperation(
          ensureAuthHeader(),
          controller.signal,
        );
        if (controller.signal.aborted || !current()) return;
        if (!authHeader) {
          setNotice(AUTH_UNAVAILABLE);
          return;
        }
        if (row.expiresAt <= Date.now()) {
          setNotice(ROW_EXPIRED);
          return;
        }
        let url: string;
        try {
          url = webSnapshotApiUrl(conversationId, row.id);
        } catch {
          setNotice(EXPORT_UNAVAILABLE);
          return;
        }
        const response = await awaitSnapshotOperation(
          fetch(url, {
            method: 'DELETE',
            headers: { Authorization: authHeader },
            cache: 'no-store',
            credentials: 'omit',
            redirect: 'error',
            signal: controller.signal,
          }),
          controller.signal,
        );
        if (controller.signal.aborted || !current()) return;
        if (response.ok) {
          let body: unknown;
          try {
            body = await awaitSnapshotOperation(
              response.json(),
              controller.signal,
            );
          } catch {
            if (!current() || (controller.signal.aborted && !timedOut)) return;
            setNeedsReconcile(true);
            setNotice(DELETE_UNKNOWN);
            return;
          }
          if (controller.signal.aborted || !current()) return;
          if (
            body &&
            typeof body === 'object' &&
            (body as { status?: unknown }).status === 'deleted'
          ) {
            // Success: reconcile by refetching the safe current page, moving
            // back one full page when this page only held the removed row.
            const safeOffset =
              page && page.rows.length === 1 && offset > 0
                ? Math.max(0, offset - SNAPSHOT_LIST_LIMIT)
                : offset;
            if (opAbortRef.current === controller) opAbortRef.current = null;
            setRunningSafe(null);
            setConfirmId(null);
            await load(safeOffset);
            return;
          }
          setNotice(DELETE_UNKNOWN);
          setNeedsReconcile(true);
          return;
        }
        if (response.status === 404) {
          // Wrong owner / wrong conversation / unknown id / expired are all
          // indistinguishable 404s: never claim a reason.
          setNotice(DELETE_GONE);
          setNeedsReconcile(true);
          return;
        }
        if (response.status === 401 || response.status === 403) {
          setPage(null);
          setStatus('auth');
          setNotice(AUTH_UNAVAILABLE);
        } else {
          setNotice(DELETE_UNKNOWN);
          setNeedsReconcile(true);
        }
      } catch {
        // Timeout, network failure, or auth-change/abort after the request:
        // the outcome is unknown. No optimistic success, no automatic retry.
        if (!current() || (controller.signal.aborted && !timedOut)) return;
        setNotice(DELETE_UNKNOWN);
        setNeedsReconcile(true);
      } finally {
        clearTimeout(timer);
        if (opAbortRef.current === controller) {
          opAbortRef.current = null;
          setRunningSafe(null);
        }
        if (current()) setConfirmId(null);
      }
    },
    [conversationId, offset, page, confirmId, current, load, setRunningSafe],
  );

  const downloadExport = useCallback(
    async (row: SnapshotRow): Promise<void> => {
      if (!current() || opAbortRef.current || runningRef.current) return;
      if (row.expiresAt <= Date.now()) {
        setNotice(ROW_EXPIRED);
        return;
      }
      const controller = new AbortController();
      opAbortRef.current = controller;
      let exportTimedOut = false;
      const timer = setTimeout(() => {
        exportTimedOut = true;
        controller.abort();
      }, EXPORT_TIMEOUT_MS);
      setRunningSafe('export');
      setNotice(null);
      setConfirmId(null);
      let objectUrl: string | null = null;
      let link: HTMLAnchorElement | null = null;
      try {
        const authHeader = await awaitSnapshotOperation(
          ensureAuthHeader(),
          controller.signal,
        );
        if (controller.signal.aborted || !current()) return;
        if (!authHeader) {
          setNotice(EXPORT_AUTH_NEEDED);
          return;
        }
        if (row.expiresAt <= Date.now()) {
          setNotice(ROW_EXPIRED);
          return;
        }
        let url: string;
        try {
          url = webSnapshotApiUrl(conversationId, row.id, true);
        } catch {
          setNotice(EXPORT_UNAVAILABLE);
          return;
        }
        const response = await awaitSnapshotOperation(
          fetch(url, {
            method: 'GET',
            headers: { Authorization: authHeader },
            cache: 'no-store',
            credentials: 'omit',
            redirect: 'error',
            signal: controller.signal,
          }),
          controller.signal,
        );
        if (controller.signal.aborted || !current()) return;
        if (!response.ok) {
          if (response.status === 413) setNotice(EXPORT_TOO_LARGE);
          else if (response.status === 401 || response.status === 403) {
            setPage(null);
            setStatus('auth');
            setNotice(EXPORT_UNAVAILABLE);
          } else setNotice(EXPORT_UNAVAILABLE);
          return;
        }
        const contentType = response.headers.get('content-type') ?? '';
        if (!/^application\/json\b/i.test(contentType.trim())) {
          setNotice(EXPORT_UNAVAILABLE);
          return;
        }
        const text = await readBoundedExport(
          response,
          SNAPSHOT_EXPORT_MAX_BYTES,
          controller.signal,
        );
        if (controller.signal.aborted || !current()) return;
        // Confirm the export contract without displaying any retained text.
        let documentJson: unknown;
        try {
          documentJson = JSON.parse(text);
        } catch {
          setNotice(EXPORT_UNAVAILABLE);
          return;
        }
        const isObject = !!documentJson && typeof documentJson === 'object';
        const snapshotIdValid =
          isObject &&
          (documentJson as { snapshot_id?: unknown }).snapshot_id === row.id;
        if (!snapshotIdValid) {
          setNotice(EXPORT_UNAVAILABLE);
          return;
        }
        if (
          !current() ||
          opAbortRef.current !== controller ||
          controller.signal.aborted
        )
          return;
        const blob = new Blob([text], { type: 'application/json' });
        objectUrl = URL.createObjectURL(blob);
        link = document.createElement('a');
        link.href = objectUrl;
        link.download = snapshotExportFilename(row.id);
        document.body.appendChild(link);
        link.click();
      } catch (error) {
        if (!current()) return;
        if (controller.signal.aborted) {
          if (exportTimedOut) setNotice(EXPORT_UNAVAILABLE);
          return;
        }
        setNotice(
          error instanceof SnapshotExportTooLargeError
            ? EXPORT_TOO_LARGE
            : EXPORT_UNAVAILABLE,
        );
      } finally {
        clearTimeout(timer);
        link?.remove();
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        if (opAbortRef.current === controller) {
          opAbortRef.current = null;
          setRunningSafe(null);
        }
      }
    },
    [conversationId, current, setRunningSafe],
  );

  const cancelConfirm = useCallback(() => {
    // The invoking Remove button reappears after this re-render; the effect
    // returns focus once it is connected again.
    pendingFocusRef.current = confirmId;
    setConfirmId(null);
  }, [confirmId]);

  const rows = page?.rows ?? [];
  const hasMore =
    page !== null &&
    page.rows.length > 0 &&
    offset + page.rows.length < page.total;
  const busy = running !== null;

  return (
    <section ref={rootRef} aria-label="Retained sources" className="space-y-3">
      <h3 className="text-sm font-semibold">Retained sources</h3>
      <p className="text-sm text-[var(--color-text-secondary)]">
        Retained sources kept for this conversation. Saved page copies can be
        exported or removed by you; removal never affects the original website
        or this transcript. Returned citation links stay separate.
      </p>
      {notice && (
        <p role="status" className="text-sm break-words">
          {notice}
        </p>
      )}
      {status === 'auth' && <p className="text-sm">{AUTH_UNAVAILABLE}</p>}
      {status === 'error' && (
        <>
          <p className="text-sm">{LIST_LOAD_FAILED}</p>
          <button
            type="button"
            disabled={busy}
            className="min-h-touch rounded-lg border border-[var(--color-border-primary)] px-3 text-sm"
            onClick={() => void load(offset)}
          >
            Refresh
          </button>
        </>
      )}
      {status === 'loading' && !page && (
        <p className="text-sm">Loading retained sources…</p>
      )}
      {status === 'ready' && rows.length === 0 && (
        <>
          <p className="text-sm">
            {page && page.total > 0
              ? 'No retained sources on this page.'
              : 'No retained sources yet.'}
          </p>
          {offset > 0 && (
            <button
              type="button"
              className="min-h-touch rounded-lg border border-[var(--color-border-primary)] px-3 text-sm"
              disabled={busy}
              onClick={() =>
                void load(Math.max(0, offset - SNAPSHOT_LIST_LIMIT))
              }
            >
              Previous
            </button>
          )}
          <button
            type="button"
            className="min-h-touch rounded-lg border border-[var(--color-border-primary)] px-3 text-sm"
            disabled={busy}
            onClick={() => void load(offset)}
          >
            Refresh
          </button>
        </>
      )}
      {page && rows.length > 0 && (
        <>
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-[var(--color-text-secondary)]">
              {offset + 1}–{offset + rows.length} of {page.total}
            </p>
            <button
              type="button"
              disabled={busy}
              className="min-h-touch rounded-lg border border-[var(--color-border-primary)] px-3 text-sm"
              onClick={() => void load(offset)}
            >
              Refresh
            </button>
          </div>
          <ul className="space-y-2">
            {rows.map((row) => (
              <li
                key={row.id}
                data-snapshot-id={row.id}
                className="rounded-xl border border-[var(--color-border-primary)] p-3 space-y-2"
              >
                <div className="space-y-1">
                  {row.title && (
                    <p className="text-sm font-medium break-words">
                      {row.title}
                    </p>
                  )}
                  {row.linkHref && (
                    <a
                      href={row.linkHref}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs underline break-all"
                    >
                      Open original
                      <span className="sr-only">
                        : {row.title || row.linkHref}
                      </span>
                    </a>
                  )}
                  <p className="text-xs text-[var(--color-text-secondary)]">
                    {row.retrievedLabel} · {row.expiresLabel} ·{' '}
                    {row.contentChars.toLocaleString()} characters
                  </p>
                  {row.expired && (
                    <p className="text-xs">
                      Expired snapshot. Refresh to update the list.
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    data-snapshot-export={row.id}
                    disabled={busy || row.expired}
                    className="min-h-touch rounded-lg border border-[var(--color-border-primary)] px-3 text-sm"
                    onClick={() => void downloadExport(row)}
                  >
                    Export<span className="sr-only"> snapshot JSON</span>
                  </button>
                  {confirmId === row.id ? (
                    <div
                      role="group"
                      aria-label="Confirm snapshot removal"
                      onKeyDown={(event) => {
                        if (event.key !== 'Escape' || event.defaultPrevented)
                          return;
                        // Inline confirmation owns Escape before any stream
                        // Stop shortcut or details-close handler.
                        event.preventDefault();
                        event.stopPropagation();
                        cancelConfirm();
                      }}
                      className="w-full space-y-2 rounded-lg border border-[var(--color-border-primary)] p-2"
                    >
                      <p className="text-xs break-words">
                        Removing permanently deletes only this retained copy
                        held by Daemon. The original website and this
                        conversation transcript are unchanged.
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <button
                          ref={cancelButtonRef}
                          type="button"
                          data-snapshot-cancel={row.id}
                          disabled={running === 'delete'}
                          className="min-h-touch rounded-lg border border-[var(--color-border-primary)] px-3 text-sm"
                          onClick={cancelConfirm}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          data-snapshot-remove={row.id}
                          disabled={running === 'delete'}
                          className="min-h-touch rounded-lg border border-[var(--color-border-primary)] px-3 text-sm"
                          onClick={() => void confirmRemove(row)}
                        >
                          Remove snapshot
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      data-removal-trigger="true"
                      disabled={busy || row.expired || needsReconcile}
                      className="min-h-touch rounded-lg border border-[var(--color-border-primary)] px-3 text-sm"
                      onClick={() => {
                        setNotice(null);
                        setConfirmId(row.id);
                      }}
                    >
                      Remove<span className="sr-only"> retained snapshot</span>
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy || offset === 0}
              className="min-h-touch rounded-lg border border-[var(--color-border-primary)] px-3 text-sm"
              onClick={() =>
                void load(Math.max(0, offset - SNAPSHOT_LIST_LIMIT))
              }
            >
              Previous
            </button>
            <button
              type="button"
              disabled={busy || !hasMore}
              className="min-h-touch rounded-lg border border-[var(--color-border-primary)] px-3 text-sm"
              onClick={() => void load(offset + SNAPSHOT_LIST_LIMIT)}
            >
              Next
            </button>
          </div>
        </>
      )}
    </section>
  );
}
