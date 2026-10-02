'use client';

import { useCallback, useEffect, useState } from 'react';
import { ensureAuthHeader } from '@/lib/auth';

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

export function toMemoryExport(
  rows: Array<Record<string, unknown>>,
  now: Date = new Date(),
): MemoryExport {
  const text = (value: unknown) => (typeof value === 'string' ? value : null);
  return {
    format: 'daemon-memories',
    version: 1,
    exported_at: now.toISOString(),
    status: 'active',
    memories: rows
      .filter((row) => typeof row.content === 'string' && row.content)
      .map((row) => ({
        content: row.content as string,
        category: text(row.category) ?? 'fact',
        created_at: text(row.created_at),
        updated_at: text(row.updated_at),
      })),
  };
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

          if (response.status === 404 && index < candidates.length - 1) {
            continue;
          }

          return response;
        } catch (error) {
          clearTimeout(timeoutId);
          lastError = error;
          if (index === candidates.length - 1) {
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
    ): Promise<{ ok: true; id: string } | { ok: false; error: string }> => {
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
        });
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
        return { ok: true, id: String(data.id ?? '') };
      } catch {
        return {
          ok: false,
          error: "Couldn't save the memory. Please try again.",
        };
      }
    },
    [apiFetch, getAuthHeaders],
  );

  /** Active memories in a portable shape, for download by the person. */
  const exportMemories = useCallback(async (): Promise<MemoryExport> => {
    const response = await apiFetch(
      '/memories/export',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(await getAuthHeaders()),
        },
        body: JSON.stringify({ status: 'active' }),
      },
      30000,
    );
    if (!response.ok) throw new Error(`Export failed: ${response.status}`);
    const data: { memories?: Array<Record<string, unknown>> } =
      await response.json();
    return toMemoryExport(data.memories ?? []);
  }, [apiFetch, getAuthHeaders]);

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
  };
}
