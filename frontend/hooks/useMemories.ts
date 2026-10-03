'use client';

import { useCallback, useEffect, useState } from 'react';
import { ensureAuthHeader, getAuthGeneration } from '@/lib/auth';

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

export interface TrailItem {
  id: string;
  memory_id: string;
  content: string;
  category: string;
  changed_by: string;
  changed_at: string;
  change_type: string;
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
  created: number;
  merged: number;
  superseded: number;
  /** Entries the server received before any failure. */
  processed: number;
  total: number;
  /** Set when the import stopped early; counts above are what was saved. */
  error?: string;
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
    async (path: string, init: RequestInit = {}, timeoutMs = 12000) => {
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
          if (external?.aborted || index === candidates.length - 1) {
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

  const fetchMemories = useCallback(
    async (params: FetchMemoriesParams = {}) => {
      setLoading(true);
      setError(null);

      try {
        const queryParams = new URLSearchParams();
        if (params.category) queryParams.set('category', params.category);
        if (params.source_type)
          queryParams.set('source_type', params.source_type);
        if (params.status) queryParams.set('status', params.status);
        if (params.search) queryParams.set('search', params.search);
        if (params.limit) queryParams.set('limit', params.limit.toString());
        if (params.offset) queryParams.set('offset', params.offset.toString());

        const queryString = queryParams.toString();
        const url = `/memories${queryString ? `?${queryString}` : ''}`;

        const response = await apiFetch(url, {
          headers: await getAuthHeaders(),
        });

        if (!response.ok) {
          throw new Error(`Failed to fetch memories: ${response.status}`);
        }

        const data: { memories: Memory[]; total: number } =
          await response.json();

        if (params.offset && params.offset > 0) {
          // Append for pagination
          setMemories((prev) => [...prev, ...data.memories]);
        } else {
          // Replace for initial fetch
          setMemories(data.memories);
        }

        setTotal(data.total);
        setHasMore(
          data.memories.length > 0 &&
            data.memories.length >= (params.limit || 20),
        );
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          return;
        }
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    },
    [apiFetch, getAuthHeaders],
  );

  const loadMore = useCallback(
    async (params: FetchMemoriesParams = {}) => {
      const currentParams = {
        ...params,
        limit: params.limit || 20,
        offset: params.offset ?? memories.length,
      };
      await fetchMemories(currentParams);
    },
    [fetchMemories, memories.length],
  );

  const deleteMemory = useCallback(
    async (id: string): Promise<boolean> => {
      // Optimistic update
      const previousMemories = memories;
      setMemories((prev) => prev.filter((mem) => mem.id !== id));
      setTotal((prev) => Math.max(0, prev - 1));

      try {
        const response = await apiFetch(`/memories/${id}`, {
          method: 'DELETE',
          headers: await getAuthHeaders(),
        });

        if (!response.ok) {
          // Revert on error
          setMemories(previousMemories);
          setError('Failed to delete memory');
          return false;
        }

        return true;
      } catch {
        // Revert on error
        setMemories(previousMemories);
        setError('Failed to delete memory');
        return false;
      }
    },
    [apiFetch, getAuthHeaders, memories],
  );

  const correctMemory = useCallback(
    async (
      id: string,
      content: string,
      category?: string,
    ): Promise<Memory | null> => {
      const previousMemories = memories;

      // Optimistic update
      setMemories((prev) =>
        prev.map((mem) =>
          mem.id === id
            ? { ...mem, content, category: category || mem.category }
            : mem,
        ),
      );

      try {
        const response = await apiFetch(`/memories/${id}/correct`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(await getAuthHeaders()),
          },
          body: JSON.stringify({ content, category }),
        });

        if (!response.ok) {
          // Revert on error
          setMemories(previousMemories);
          setError('Failed to correct memory');
          return null;
        }

        const correctedMemory: Memory = await response.json();

        // Replace with corrected version
        setMemories((prev) =>
          prev.map((mem) => (mem.id === id ? correctedMemory : mem)),
        );

        return correctedMemory;
      } catch {
        // Revert on error
        setMemories(previousMemories);
        setError('Failed to correct memory');
        return null;
      }
    },
    [apiFetch, getAuthHeaders, memories],
  );

  const fetchTrail = useCallback(
    async (id: string): Promise<TrailItem[]> => {
      try {
        const response = await apiFetch(`/memories/${id}/trail`, {
          headers: await getAuthHeaders(),
        });

        if (!response.ok) {
          throw new Error(`Failed to fetch trail: ${response.status}`);
        }

        const trail: TrailItem[] = await response.json();
        return trail;
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          return [];
        }
        setError(err instanceof Error ? err.message : 'Failed to fetch trail');
        return [];
      }
    },
    [apiFetch, getAuthHeaders],
  );

  // Initial fetch and polling every 30 seconds
  useEffect(() => {
    fetchMemories();
    const interval = setInterval(fetchMemories, 30000);
    return () => clearInterval(interval);
  }, [fetchMemories]);

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
        const response = await apiFetch('/memories', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(await getAuthHeaders()),
          },
          body: JSON.stringify({ content: trimmed, category }),
          signal: options.signal,
        });
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
      };
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
          );
        } catch {
          assertCurrent(generation, options.signal);
          return {
            ...result,
            error: "Couldn't reach Daemon. The import stopped.",
          };
        }
        const data = (await response.json().catch(() => ({}))) as Record<
          string,
          unknown
        >;
        assertCurrent(generation, options.signal);
        const counts = (response.ok ? data : (data.detail ?? {})) as Record<
          string,
          unknown
        >;
        const n = (key: string) =>
          typeof counts[key] === 'number' ? (counts[key] as number) : 0;
        result.created += n('created');
        result.merged += n('merged');
        result.superseded += n('superseded');
        if (!response.ok) {
          result.processed += n('processed');
          return {
            ...result,
            error:
              response.status === 503
                ? 'The import stopped because a memory service was unavailable.'
                : response.status === 422
                  ? 'Daemon rejected part of this file.'
                  : "Couldn't finish the import.",
          };
        }
        result.processed += chunk.length;
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
    deleteMemory,
    correctMemory,
    fetchTrail,
    createMemory,
    exportMemories,
    importMemories,
  };
}
