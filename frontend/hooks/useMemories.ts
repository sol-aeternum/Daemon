'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ensureAuthHeader,
  getAuthGeneration,
  subscribeAuthGeneration,
} from '@/lib/auth';

export interface Memory {
  id: string;
  content: string;
  category: string;
  status: string;
  source_type: string;
  conversation_id: string | null;
  created_at: string;
  updated_at: string;
  confirmed: boolean;
  metadata?: Record<string, unknown>;
}

export interface FetchMemoriesParams {
  category?: string;
  source_type?: string;
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}

/** Categories a person may assign by hand; `summary` is system-generated. */
export const USER_MEMORY_CATEGORIES = [
  'fact',
  'preference',
  'project',
  'correction',
] as const;
export type UserMemoryCategory = (typeof USER_MEMORY_CATEGORIES)[number];
export const MAX_USER_MEMORY_LENGTH = 2000;

/** Portable export record: no IDs, embeddings or internal bookkeeping. */
export interface ExportedMemory {
  content: string;
  category: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface MemoryExport {
  format: 'daemon-memories';
  version: 1;
  exported_at: string;
  status: 'active';
  memories: ExportedMemory[];
}

/** The account or view that started a request is gone; drop its result. */
export class MemoryRequestSupersededError extends Error {
  constructor() {
    super('Memory request superseded');
    this.name = 'MemoryRequestSupersededError';
  }
}

/** The server answered, but not with a usable memory export. */
export class MemoryExportFormatError extends Error {
  constructor() {
    super('Unexpected memory export response');
    this.name = 'MemoryExportFormatError';
  }
}

function isOptionalDate(value: unknown): boolean {
  return value === undefined || value === null || typeof value === 'string';
}

/**
 * Validates the export payload before anything is offered for download. An
 * empty array is a valid (empty) export; a missing, null or malformed list is
 * not.
 */
export function toMemoryExport(
  rows: unknown,
  now: Date = new Date(),
): MemoryExport {
  if (!Array.isArray(rows)) throw new MemoryExportFormatError();
  const memories: ExportedMemory[] = rows.map((row: unknown) => {
    if (!row || typeof row !== 'object') throw new MemoryExportFormatError();
    const record = row as Record<string, unknown>;
    if (
      typeof record.content !== 'string' ||
      !record.content ||
      typeof record.category !== 'string' ||
      !isOptionalDate(record.created_at) ||
      !isOptionalDate(record.updated_at)
    ) {
      throw new MemoryExportFormatError();
    }
    return {
      content: record.content,
      category: record.category,
      created_at: (record.created_at as string | null | undefined) ?? null,
      updated_at: (record.updated_at as string | null | undefined) ?? null,
    };
  });
  return {
    format: 'daemon-memories',
    version: 1,
    exported_at: now.toISOString(),
    status: 'active',
    memories,
  };
}

/** Caller-owned cancellation: abort when the view or sign-in goes away. */
export interface MemoryRequestOptions {
  signal?: AbortSignal;
}

function assertCurrent(generation: number, signal?: AbortSignal): void {
  if (signal?.aborted || generation !== getAuthGeneration()) {
    throw new MemoryRequestSupersededError();
  }
}

/** Categories the import route accepts; unknown ones are imported as facts. */
export const IMPORT_MEMORY_CATEGORIES = [
  'fact',
  'preference',
  'project',
  'summary',
  'correction',
] as const;
export const MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024;
export const IMPORT_REQUEST_SIZE = 500;

export interface ImportableMemory {
  content: string;
  category: string;
}

export interface ParsedMemoryImport {
  memories: ImportableMemory[];
  /** Entries left out, by reason, so the preview can say why. */
  skipped: { empty: number; tooLong: number; duplicate: number };
  /** Entries whose unknown category was imported as `fact`. */
  recategorized: number;
}

export interface MemoryImportResult {
  /** Confirmed by the server: each counted entry is saved. */
  created: number;
  merged: number;
  superseded: number;
  /** Entries the server confirmed it processed (all saved). */
  processed: number;
  total: number;
  /**
   * Entries sent whose outcome Daemon never confirmed (lost or malformed
   * acknowledgement). Some of them may have been saved.
   */
  unconfirmed: number;
  /** Set when the import stopped early. */
  error?: string;
}

interface ImportCounts {
  processed: number;
  created: number;
  merged: number;
  superseded: number;
}

function count(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
    ? value
    : null;
}

/**
 * Accepts an import acknowledgement only if it is internally consistent for
 * the chunk that was sent; otherwise the chunk's outcome is unknown.
 */
export function readImportCounts(
  body: unknown,
  sent: number,
  complete: boolean,
): ImportCounts | null {
  if (!body || typeof body !== 'object') return null;
  const record = body as Record<string, unknown>;
  const received = count(record.received);
  const processed = count(record.processed);
  const created = count(record.created);
  const merged = count(record.merged);
  const superseded = count(record.superseded);
  if (
    received === null ||
    processed === null ||
    created === null ||
    merged === null ||
    superseded === null ||
    received !== sent ||
    processed > received ||
    (complete && processed !== received) ||
    // Every processed entry is created, merged or superseded; nothing else.
    created + merged + superseded !== processed
  ) {
    return null;
  }
  return { processed, created, merged, superseded };
}

/**
 * Accepts a Daemon export ({format: 'daemon-memories', memories: [...]}) or a
 * plain array of {content, category}. Throws a person-readable Error for files
 * that are not JSON or have no memories array.
 */
export function parseMemoryImport(text: string): ParsedMemoryImport {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error("That file isn't valid JSON.");
  }
  const rows = Array.isArray(data)
    ? data
    : data &&
        typeof data === 'object' &&
        Array.isArray((data as { memories?: unknown }).memories)
      ? (data as { memories: unknown[] }).memories
      : null;
  if (!rows) {
    throw new Error(
      'No memories found. Use a Daemon memory export or a JSON array of {"content", "category"}.',
    );
  }
  const allowed = new Set<string>(IMPORT_MEMORY_CATEGORIES);
  const seen = new Set<string>();
  const parsed: ParsedMemoryImport = {
    memories: [],
    skipped: { empty: 0, tooLong: 0, duplicate: 0 },
    recategorized: 0,
  };
  for (const row of rows) {
    const record =
      typeof row === 'string'
        ? { content: row }
        : row && typeof row === 'object'
          ? (row as Record<string, unknown>)
          : {};
    const content =
      typeof record.content === 'string' ? record.content.trim() : '';
    if (!content) {
      parsed.skipped.empty += 1;
      continue;
    }
    if (content.length > MAX_USER_MEMORY_LENGTH) {
      parsed.skipped.tooLong += 1;
      continue;
    }
    let category =
      typeof record.category === 'string'
        ? record.category.trim().toLowerCase()
        : 'fact';
    if (!allowed.has(category)) {
      category = 'fact';
      parsed.recategorized += 1;
    }
    const key = `${category}\u0000${content}`;
    if (seen.has(key)) {
      parsed.skipped.duplicate += 1;
      continue;
    }
    seen.add(key);
    parsed.memories.push({ content, category });
  }
  return parsed;
}

export const MEMORY_PAGE_SIZE = 20;
const MEMORY_REFRESH_PAGE_SIZE = 100;

/** Filters the list API accepts; status "all" means every non-deleted row. */
export interface ListFilters {
  category?: string;
  source_type?: string;
  status?: string;
  search?: string;
}

export interface MemoryPage {
  memories: Memory[];
  total: number;
  has_more: boolean;
}

/** Validates GET /memories; the total is the filtered count, not the page. */
export function parseMemoryPage(body: unknown): MemoryPage {
  if (!body || typeof body !== 'object') {
    throw new Error('Unexpected memory list response');
  }
  const record = body as Record<string, unknown>;
  if (
    !Array.isArray(record.memories) ||
    typeof record.total !== 'number' ||
    !Number.isInteger(record.total) ||
    record.total < record.memories.length ||
    typeof record.has_more !== 'boolean'
  ) {
    throw new Error('Unexpected memory list response');
  }
  return {
    memories: record.memories as Memory[],
    total: record.total,
    has_more: record.has_more,
  };
}

export type CorrectMemoryResult =
  | { ok: true; memory: Memory }
  | { ok: false; error: string; status?: number };

function correctionError(status: number): string {
  switch (status) {
    case 404:
      return 'This memory no longer exists, so the edit was not saved.';
    case 409:
      return 'Another memory already says exactly this. Change the wording or delete the other one.';
    case 422:
      return `Write something to remember, under ${MAX_USER_MEMORY_LENGTH.toLocaleString()} characters.`;
    case 503:
      return 'Memory is unavailable right now, so the edit was not saved. Try again shortly.';
    default:
      return "Couldn't save this edit. Your draft is still here.";
  }
}

/** The PATCH reply must be the memory that was edited. */
export function parseCorrectedMemory(body: unknown, id: string): Memory | null {
  if (!body || typeof body !== 'object') return null;
  const record = body as Record<string, unknown>;
  if (
    String(record.id) !== id ||
    typeof record.content !== 'string' ||
    typeof record.category !== 'string'
  ) {
    return null;
  }
  return { ...(record as unknown as Memory), id };
}

export function useMemories() {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(true);
  const [total, setTotal] = useState(0);

  const apiBaseUrl =
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '');

  const getAuthHeaders = useCallback(async (): Promise<
    Record<string, string>
  > => {
    const header = await ensureAuthHeader();
    if (!header) return {};
    return { Authorization: header };
  }, []);

  const apiCandidates = useCallback(
    (path: string) => {
      const normalizedPath = path.startsWith('/') ? path : `/${path}`;
      const trimmedBase = apiBaseUrl.endsWith('/')
        ? apiBaseUrl.slice(0, -1)
        : apiBaseUrl;

      if (!trimmedBase) {
        return [normalizedPath];
      }

      return [`${trimmedBase}${normalizedPath}`, normalizedPath];
    },
    [apiBaseUrl],
  );

  const apiFetch = useCallback(
    async (
      path: string,
      init: RequestInit = {},
      timeoutMs = 12000,
      // Non-idempotent writes must not be replayed on the next candidate URL
      // after a network failure: the first request may already have committed.
      { retryAfterError = true }: { retryAfterError?: boolean } = {},
    ) => {
      const candidates = apiCandidates(path);
      let lastError: unknown = null;

      for (let index = 0; index < candidates.length; index += 1) {
        const candidate = candidates[index];
        const controller = new AbortController();
        const external = init.signal ?? undefined;
        if (external?.aborted) {
          throw new DOMException('Request aborted', 'AbortError');
        }
        const forwardAbort = () => controller.abort(external?.reason);
        external?.addEventListener('abort', forwardAbort, { once: true });
        const timeoutId = setTimeout(() => {
          try {
            controller.abort(
              new DOMException('Request timed out', 'AbortError'),
            );
          } catch {
            controller.abort();
          }
        }, timeoutMs);

        try {
          const response = await fetch(candidate, {
            ...init,
            signal: controller.signal,
          });
          clearTimeout(timeoutId);
          external?.removeEventListener('abort', forwardAbort);

          if (response.status === 404 && index < candidates.length - 1) {
            continue;
          }

          return response;
        } catch (error) {
          clearTimeout(timeoutId);
          external?.removeEventListener('abort', forwardAbort);
          lastError = error;
          if (
            external?.aborted ||
            !retryAfterError ||
            index === candidates.length - 1
          ) {
            throw error;
          }
        }
      }

      if (lastError instanceof Error) {
        throw lastError;
      }
      throw new Error('Request failed');
    },
    [apiCandidates],
  );

  // The list view: current filters, request ordering and loaded span.
  // Polling and "Load more" always reuse the filters last applied, and only
  // the newest request for the current sign-in may publish results.
  const filtersRef = useRef<ListFilters>({ status: 'active' });
  const listRequest = useRef(0);
  const loadedCount = useRef(0);
  // The foreground request (filter fetch or "Load more") that owns `loading`.
  // A background refresh never retires it; it waits and runs afterwards.
  const foreground = useRef<number | null>(null);
  const refreshPending = useRef(false);
  // Which kind of foreground request owns loading, and how many deletes are
  // pending. While a delete is pending no background refresh publishes.
  const foregroundKind = useRef<'fetch' | 'loadMore' | null>(null);
  const pendingMutations = useRef(0);
  const refreshRef = useRef<() => Promise<void>>(async () => undefined);

  const beginForeground = (kind: 'fetch' | 'loadMore') => {
    const request = ++listRequest.current;
    foreground.current = request;
    foregroundKind.current = kind;
    setLoading(true);
    setError(null);
    return request;
  };

  /** Only the current foreground request settles loading. */
  const settleForeground = (request: number) => {
    if (foreground.current !== request) return;
    foreground.current = null;
    foregroundKind.current = null;
    setLoading(false);
    if (refreshPending.current && pendingMutations.current === 0) {
      refreshPending.current = false;
      void refreshRef.current();
    }
  };

  const requestPage = useCallback(
    async (
      filters: ListFilters,
      offset: number,
      limit: number,
    ): Promise<MemoryPage> => {
      const query = new URLSearchParams();
      if (filters.category) query.set('category', filters.category);
      if (filters.source_type) query.set('source_type', filters.source_type);
      query.set('status', filters.status ?? 'active');
      if (filters.search) query.set('search', filters.search);
      query.set('limit', String(limit));
      query.set('offset', String(offset));
      const response = await apiFetch(`/memories?${query}`, {
        headers: await getAuthHeaders(),
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch memories: ${response.status}`);
      }
      return parseMemoryPage(await response.json());
    },
    [apiFetch, getAuthHeaders],
  );

  const publish = useCallback((rows: Memory[], page: MemoryPage) => {
    loadedCount.current = rows.length;
    setMemories(rows);
    setTotal(page.total);
    setHasMore(page.has_more);
  }, []);

  /** Apply filters (or reapply the current ones) from the first page. */
  const fetchMemories = useCallback(
    async (params: FetchMemoriesParams = {}) => {
      const { limit: _limit, offset: _offset, ...filters } = params;
      if (Object.keys(params).length > 0) {
        filtersRef.current = { status: 'active', ...filters };
      }
      const request = beginForeground('fetch');
      const generation = getAuthGeneration();
      const current = filtersRef.current;
      try {
        const page = await requestPage(current, 0, MEMORY_PAGE_SIZE);
        if (
          request !== listRequest.current ||
          generation !== getAuthGeneration()
        )
          return;
        publish(page.memories, page);
      } catch (err) {
        if (request !== listRequest.current) return;
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        settleForeground(request);
      }
    },
    [publish, requestPage],
  );

  /** Append the next page for the current filters. */
  const loadMore = useCallback(async () => {
    const request = beginForeground('loadMore');
    const generation = getAuthGeneration();
    const current = filtersRef.current;
    const offset = loadedCount.current;
    try {
      const page = await requestPage(current, offset, MEMORY_PAGE_SIZE);
      if (request !== listRequest.current || generation !== getAuthGeneration())
        return;
      // The next offset is the server rows consumed, set now (not inside a
      // lazy state updater) so a refresh starting right after sees the span.
      loadedCount.current = offset + page.memories.length;
      setMemories((previous) => {
        const seen = new Set(previous.map((memory) => memory.id));
        return [
          ...previous,
          ...page.memories.filter((memory) => !seen.has(memory.id)),
        ];
      });
      setTotal(page.total);
      setHasMore(page.has_more);
    } catch (err) {
      if (request !== listRequest.current) return;
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      settleForeground(request);
    }
  }, [requestPage]);

  /**
   * Re-read the span already loaded (at least one page) for the current
   * filters, so polling or a save never collapses the list back to page one.
   */
  const refreshMemories = useCallback(async () => {
    if (foreground.current !== null || pendingMutations.current > 0) {
      // Never retire a foreground request, and never publish a snapshot that
      // may predate a pending delete; refresh once both settle.
      refreshPending.current = true;
      return;
    }
    const request = ++listRequest.current;
    const generation = getAuthGeneration();
    const current = filtersRef.current;
    const span = Math.max(MEMORY_PAGE_SIZE, loadedCount.current);
    try {
      const rows: Memory[] = [];
      const seen = new Set<string>();
      let last: MemoryPage | null = null;
      while (rows.length < span) {
        const limit = Math.min(MEMORY_REFRESH_PAGE_SIZE, span - rows.length);
        last = await requestPage(current, rows.length, limit);
        if (
          request !== listRequest.current ||
          generation !== getAuthGeneration()
        )
          return;
        for (const memory of last.memories) {
          if (!seen.has(memory.id)) {
            seen.add(memory.id);
            rows.push(memory);
          }
        }
        if (!last.has_more || last.memories.length === 0) break;
      }
      if (last) {
        publish(rows, {
          ...last,
          has_more: last.has_more && rows.length < last.total,
        });
      }
    } catch {
      // A failed background refresh keeps the current list.
    }
  }, [publish, requestPage]);

  useEffect(() => {
    refreshRef.current = refreshMemories;
  }, [refreshMemories]);

  // A sign-in change retires in-flight list requests and clears the list.
  useEffect(
    () =>
      subscribeAuthGeneration(() => {
        listRequest.current += 1;
        foreground.current = null;
        foregroundKind.current = null;
        refreshPending.current = false;
        loadedCount.current = 0;
        setMemories([]);
        setTotal(0);
        setHasMore(false);
        setLoading(false);
      }),
    [],
  );

  /**
   * Writes that change a listed memory take part in list ordering. Starting
   * one retires in-flight list responses (they may predate it); a retired
   * "Load more" keeps its intent by widening the span a reconcile refresh
   * reads. While any is pending, background refreshes wait.
   */
  const beginListMutation = () => {
    const retiredKind = foregroundKind.current;
    const version = ++listRequest.current;
    if (foreground.current !== null) {
      foreground.current = null;
      foregroundKind.current = null;
      setLoading(false);
      refreshPending.current = true;
    }
    pendingMutations.current += 1;
    return { version, retiredKind, generation: getAuthGeneration() };
  };

  /** Reconcile against the current filters and span once nothing is pending. */
  const reconcileList = () => {
    refreshPending.current = false;
    if (foreground.current !== null || pendingMutations.current > 0) {
      refreshPending.current = true;
    } else {
      void refreshRef.current();
    }
  };

  const deleteMemory = useCallback(
    async (id: string): Promise<boolean> => {
      // On settle a failed delete restores its snapshot only if no newer
      // list activity happened; otherwise, and whenever a refresh waited, it
      // re-reads the current filters and span.
      const previousMemories = memories;
      const wasLoaded = previousMemories.some((memory) => memory.id === id);
      const { version, retiredKind, generation } = beginListMutation();
      const previousCount = loadedCount.current;
      if (wasLoaded) {
        loadedCount.current = Math.max(0, previousCount - 1);
        setMemories((prev) => prev.filter((mem) => mem.id !== id));
        setTotal((prev) => Math.max(0, prev - 1));
      }
      if (retiredKind === 'loadMore') {
        loadedCount.current += MEMORY_PAGE_SIZE;
      }

      let ok = false;
      try {
        const response = await apiFetch(
          `/memories/${id}`,
          { method: 'DELETE', headers: await getAuthHeaders() },
          12000,
          { retryAfterError: false },
        );
        ok = response.ok;
      } catch {
        ok = false;
      }

      pendingMutations.current -= 1;
      if (generation !== getAuthGeneration()) return ok;
      const untouched = listRequest.current === version && retiredKind === null;
      if (!ok) {
        setError('Failed to delete memory');
        if (untouched && !refreshPending.current) {
          loadedCount.current = previousCount;
          setMemories(previousMemories);
          if (wasLoaded) setTotal((prev) => prev + 1);
          return false;
        }
      }
      if (!untouched || refreshPending.current || !ok) reconcileList();
      return ok;
    },
    [apiFetch, getAuthHeaders, memories],
  );

  /**
   * Save a person's correction via PATCH /memories/{id}. Not optimistic: the
   * list shows the server's acknowledged memory, so a failure never needs a
   * rollback and the caller keeps the draft open with the returned error.
   */
  const correctMemory = useCallback(
    async (
      id: string,
      content: string,
      category?: string,
      options: MemoryRequestOptions = {},
    ): Promise<CorrectMemoryResult> => {
      const trimmed = content.trim();
      if (!trimmed) return { ok: false, error: 'Write something to remember.' };
      if (trimmed.length > MAX_USER_MEMORY_LENGTH) {
        return {
          ok: false,
          error: `Keep it under ${MAX_USER_MEMORY_LENGTH.toLocaleString()} characters.`,
        };
      }
      const { version, retiredKind, generation } = beginListMutation();
      if (retiredKind === 'loadMore') loadedCount.current += MEMORY_PAGE_SIZE;
      const settle = () => {
        pendingMutations.current -= 1;
      };
      let response: Response;
      try {
        response = await apiFetch(
          `/memories/${id}`,
          {
            method: 'PATCH',
            headers: {
              'Content-Type': 'application/json',
              ...(await getAuthHeaders()),
            },
            body: JSON.stringify(
              category ? { content: trimmed, category } : { content: trimmed },
            ),
            signal: options.signal,
          },
          30000,
          { retryAfterError: false },
        );
      } catch {
        settle();
        assertCurrent(generation, options.signal);
        reconcileList();
        return {
          ok: false,
          error:
            "Couldn't reach Daemon, so this edit may not have been saved. Your draft is still here.",
        };
      }
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        body = null;
      }
      settle();
      assertCurrent(generation, options.signal);
      if (!response.ok) {
        if (listRequest.current !== version || refreshPending.current)
          reconcileList();
        return {
          ok: false,
          status: response.status,
          error: correctionError(response.status),
        };
      }
      const memory = parseCorrectedMemory(body, id);
      if (!memory) {
        reconcileList();
        return {
          ok: false,
          error:
            'Daemon sent an unexpected reply, so this edit may or may not have been saved. Reload to check.',
        };
      }
      setMemories((prev) =>
        prev.map((m) => (m.id === id ? { ...m, ...memory } : m)),
      );
      if (listRequest.current !== version || refreshPending.current)
        reconcileList();
      return { ok: true, memory };
    },
    [apiFetch, getAuthHeaders],
  );

  // Initial fetch and polling every 30 seconds
  useEffect(() => {
    void fetchMemories();
    const interval = setInterval(() => void refreshMemories(), 30000);
    return () => clearInterval(interval);
  }, [fetchMemories, refreshMemories]);

  /** Save a memory written by the person; the server may merge a duplicate. */
  const createMemory = useCallback(
    async (
      content: string,
      category: UserMemoryCategory,
      options: MemoryRequestOptions = {},
    ): Promise<{ ok: true; id: string } | { ok: false; error: string }> => {
      const generation = getAuthGeneration();
      const trimmed = content.trim();
      if (!trimmed) return { ok: false, error: 'Write something to remember.' };
      if (trimmed.length > MAX_USER_MEMORY_LENGTH) {
        return {
          ok: false,
          error: `Keep it under ${MAX_USER_MEMORY_LENGTH.toLocaleString()} characters.`,
        };
      }
      try {
        const response = await apiFetch(
          '/memories',
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...(await getAuthHeaders()),
            },
            body: JSON.stringify({ content: trimmed, category }),
            signal: options.signal,
          },
          12000,
          { retryAfterError: false },
        );
        assertCurrent(generation, options.signal);
        if (!response.ok) {
          return {
            ok: false,
            error:
              response.status === 503
                ? 'Memory is unavailable right now. Please try again later.'
                : "Couldn't save the memory. Please try again.",
          };
        }
        const data: { id?: string } = await response.json();
        assertCurrent(generation, options.signal);
        return { ok: true, id: String(data.id ?? '') };
      } catch (error) {
        if (
          error instanceof MemoryRequestSupersededError ||
          options.signal?.aborted ||
          generation !== getAuthGeneration()
        ) {
          throw new MemoryRequestSupersededError();
        }
        return {
          ok: false,
          error: "Couldn't save the memory. Please try again.",
        };
      }
    },
    [apiFetch, getAuthHeaders],
  );

  /** Active memories in a portable shape, for download by the person. */
  const exportMemories = useCallback(
    async (options: MemoryRequestOptions = {}): Promise<MemoryExport> => {
      const generation = getAuthGeneration();
      let response: Response;
      try {
        response = await apiFetch(
          '/memories/export',
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...(await getAuthHeaders()),
            },
            body: JSON.stringify({ status: 'active' }),
            signal: options.signal,
          },
          30000,
        );
      } catch (error) {
        assertCurrent(generation, options.signal);
        throw error;
      }
      assertCurrent(generation, options.signal);
      if (!response.ok) throw new Error(`Export failed: ${response.status}`);
      let data: unknown;
      try {
        data = await response.json();
      } catch {
        assertCurrent(generation, options.signal);
        throw new MemoryExportFormatError();
      }
      assertCurrent(generation, options.signal);
      const rows =
        data && typeof data === 'object'
          ? (data as { memories?: unknown }).memories
          : undefined;
      return toMemoryExport(rows);
    },
    [apiFetch, getAuthHeaders],
  );

  /**
   * Import in server-sized requests; stops at the first failure. The sign-in
   * that started the import is re-checked before every request, so a later
   * chunk can never be sent with another account's credentials.
   */
  const importMemories = useCallback(
    async (
      items: ImportableMemory[],
      options: MemoryRequestOptions = {},
    ): Promise<MemoryImportResult> => {
      const generation = getAuthGeneration();
      const result: MemoryImportResult = {
        created: 0,
        merged: 0,
        superseded: 0,
        processed: 0,
        total: items.length,
        unconfirmed: 0,
      };
      const add = (counts: ImportCounts) => {
        result.processed += counts.processed;
        result.created += counts.created;
        result.merged += counts.merged;
        result.superseded += counts.superseded;
      };
      const unknownOutcome = (chunk: ImportableMemory[], error: string) => ({
        ...result,
        unconfirmed: chunk.length,
        error,
      });
      for (let start = 0; start < items.length; start += IMPORT_REQUEST_SIZE) {
        assertCurrent(generation, options.signal);
        const chunk = items.slice(start, start + IMPORT_REQUEST_SIZE);
        let response: Response;
        try {
          response = await apiFetch(
            '/memories/import',
            {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                ...(await getAuthHeaders()),
              },
              body: JSON.stringify({ memories: chunk }),
              signal: options.signal,
            },
            120000,
            { retryAfterError: false },
          );
        } catch {
          assertCurrent(generation, options.signal);
          // The request may have reached Daemon and committed before the
          // connection failed; its outcome is unknown, not "nothing saved".
          return unknownOutcome(
            chunk,
            "Daemon didn't confirm the last part of the import.",
          );
        }
        let body: unknown;
        let parsed = true;
        try {
          body = await response.json();
        } catch {
          parsed = false;
        }
        assertCurrent(generation, options.signal);
        if (response.ok) {
          const counts = parsed
            ? readImportCounts(body, chunk.length, true)
            : null;
          if (!counts) {
            return unknownOutcome(
              chunk,
              'Daemon sent an unexpected reply to part of the import.',
            );
          }
          add(counts);
          continue;
        }
        if (response.status === 422) {
          // Validation happens before anything is stored.
          return { ...result, error: 'Daemon rejected part of this file.' };
        }
        const detail =
          parsed && body && typeof body === 'object'
            ? (body as { detail?: unknown }).detail
            : undefined;
        const counts =
          response.status === 503
            ? readImportCounts(detail, chunk.length, false)
            : null;
        if (!counts) {
          return unknownOutcome(chunk, "Couldn't finish the import.");
        }
        add(counts);
        return {
          ...result,
          error: 'The import stopped because a memory service was unavailable.',
        };
      }
      return result;
    },
    [apiFetch, getAuthHeaders],
  );

  return {
    memories,
    loading,
    error,
    hasMore,
    total,
    fetchMemories,
    loadMore,
    refreshMemories,
    deleteMemory,
    correctMemory,
    createMemory,
    exportMemories,
    importMemories,
  };
}
