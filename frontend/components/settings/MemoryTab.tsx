'use client';

import { useState, useCallback, useEffect } from 'react';
import { SkeletonLine, SkeletonBlock, SkeletonCircle } from '@/components/ui/Skeleton';
import { useMemories, Memory } from '@/hooks/useMemories';
import MemoryFilters from './memory/MemoryFilters';
import { MemoryCard } from './memory/MemoryCard';
import { MemoryDetail } from './memory/MemoryDetail';
import {
  Brain,
  Database,
  Trash2,
  Search,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Clock,
  AlertTriangle,
  MessageSquare,
} from 'lucide-react';

interface MemoryStats {
  total: number;
  memories: Array<{
    id: string;
    content: string;
    category: string;
    status: string;
    created_at: string;
  }>;
}

type ActionStatus = 'idle' | 'loading' | 'success' | 'error';

type ViewMode = 'list' | 'detail';

interface FilterState {
  category?: string;
  source_type?: string;
  status?: string;
  search?: string;
}

export default function MemoryTab() {
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [actionStatus, setActionStatus] = useState<ActionStatus>('idle');
  const [actionMessage, setActionMessage] = useState('');
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);

  // Memory browser state
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [selectedMemoryId, setSelectedMemoryId] = useState<string | null>(null);
  const [filters, setFilters] = useState<FilterState>({ status: 'active' });

  // useMemories hook for memory management
  const {
    memories,
    loading: memoriesLoading,
    error: memoriesError,
    fetchMemories,
    deleteMemory,
    correctMemory,
    total: memoriesTotal,
  } = useMemories();

  const apiBaseUrl =
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '');

  const getAuthHeaders = useCallback((): Record<string, string> => {
    const apiKey = typeof window !== 'undefined' ? localStorage.getItem('daemon_api_key') || '' : '';
    return apiKey ? { Authorization: `Bearer ${apiKey}` } : {};
  }, []);

  const apiCandidates = useCallback(
    (path: string) => {
      const normalizedPath = path.startsWith('/') ? path : `/${path}`;
      const trimmedBase = apiBaseUrl.endsWith('/') ? apiBaseUrl.slice(0, -1) : apiBaseUrl;

      if (!trimmedBase) {
        return [normalizedPath];
      }

      return [`${trimmedBase}${normalizedPath}`, normalizedPath];
    },
    [apiBaseUrl]
  );

  const fetchWithFallback = useCallback(
    async (path: string, init: RequestInit = {}, timeoutMs = 12000) => {
      const candidates = apiCandidates(path);

      for (let index = 0; index < candidates.length; index += 1) {
        const candidate = candidates[index];
        const controller = new AbortController();
        const timeoutId = setTimeout(() => {
          try {
            controller.abort(new DOMException('Memory request timed out', 'AbortError'));
          } catch {
            controller.abort();
          }
        }, timeoutMs);

        try {
          const response = await fetch(candidate, { ...init, signal: controller.signal });
          clearTimeout(timeoutId);

          if (response.status === 404 && index < candidates.length - 1) {
            continue;
          }

          return response;
        } catch (error) {
          clearTimeout(timeoutId);
          if (index === candidates.length - 1) {
            throw error;
          }
        }
      }

      throw new Error('Request failed');
    },
    [apiCandidates]
  );

  // Fetch memory count on mount
  const fetchMemoryStats = useCallback(async () => {
    try {
      const response = await fetchWithFallback('/memories?limit=1', {
        headers: getAuthHeaders(),
      });
      if (!response.ok) {
        setActionStatus('error');
        setActionMessage('Failed to load memories. Please verify API connectivity.');
        setStats({ total: 0, memories: [] });
        return;
      }
      const data = await response.json();
      setStats(data);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionStatus('error');
        setActionMessage('Memory request timed out. Please retry.');
      } else {
        setActionStatus('error');
        setActionMessage('Failed to load memories. Please retry.');
      }
    } finally {
      setIsLoading(false);
    }
  }, [fetchWithFallback, getAuthHeaders]);

  useEffect(() => {
    fetchMemoryStats();
  }, [fetchMemoryStats]);

  // Handle filter changes from MemoryFilters
  const handleFilterChange = useCallback((newFilters: FilterState) => {
    setFilters(newFilters);
    fetchMemories(newFilters);
  }, [fetchMemories]);

  // Handle memory selection - go to detail view
  const handleSelectMemory = useCallback((memoryId: string) => {
    setSelectedMemoryId(memoryId);
    setViewMode('detail');
  }, []);

  // Handle back navigation from detail view
  const handleBackToList = useCallback(() => {
    setSelectedMemoryId(null);
    setViewMode('list');
  }, []);

  // Handle memory correction
  const handleCorrectMemory = useCallback(async (id: string, content: string, category?: string) => {
    await correctMemory(id, content, category);
  }, [correctMemory]);

  // Handle memory deletion
  const handleDeleteMemory = useCallback(async (id: string) => {
    const success = await deleteMemory(id);
    if (success) {
      handleBackToList();
    }
  }, [deleteMemory, handleBackToList]);

  // Get selected memory object
  const selectedMemory: Memory | undefined = memories.find(m => m.id === selectedMemoryId);

  // Handle clear all memories
  const handleClearMemories = async () => {
    setActionStatus('loading');
    setActionMessage('');

    try {
      const response = await fetchWithFallback('/memories?confirm=true', {
        method: 'DELETE',
        headers: getAuthHeaders(),
      });

      if (!response.ok) {
        setActionStatus('error');
        setActionMessage('Failed to clear memories. Please verify API connectivity.');
        return;
      }

      const data = await response.json();
      setActionStatus('success');
      setActionMessage(`Successfully cleared ${data.deleted} memories`);
      setStats((prev) => (prev ? { ...prev, total: 0 } : null));

      // Reset status after 5 seconds
      setTimeout(() => {
        setActionStatus('idle');
        setActionMessage('');
      }, 5000);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionStatus('error');
        setActionMessage('Clear request timed out. Please retry.');
      } else {
        setActionStatus('error');
        setActionMessage('Failed to clear memories. Please try again.');
      }
    } finally {
      setShowConfirmDialog(false);
    }
  };

  // Loading skeleton
  if (isLoading) {
    return (
      <div className="animate-fade-in">
        <div className="flex items-center gap-3 mb-6">
          <SkeletonCircle size={40} />
          <div className="space-y-2">
            <SkeletonLine width={128} height={20} />
            <SkeletonLine width={192} height={16} />
          </div>
        </div>

        <div className="space-y-8">
          {/* Stats Section Skeleton */}
          <div className="space-y-4">
            <SkeletonLine width={160} height={20} />
            <div className="space-y-4 pl-4 border-l-2 border-border-primary">
              <SkeletonBlock height={96} />
            </div>
          </div>

          {/* Actions Section Skeleton */}
          <div className="space-y-4">
            <SkeletonLine width={160} height={20} />
            <div className="space-y-4 pl-4 border-l-2 border-border-primary">
              <SkeletonBlock height={64} />
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6 pb-6 border-b border-border-primary">
        <div className="w-10 h-10 rounded-full bg-accent-subtle flex items-center justify-center">
          <Brain className="w-5 h-5 text-accent-primary" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Memory</h2>
          <p className="text-sm text-text-muted">
            Manage your stored memories and conversation history
          </p>
        </div>
      </div>

      {/* Error Message */}
      {actionStatus === 'error' && (
        <div className="mb-6 p-4 rounded-lg bg-status-error-bg border border-status-error/20 flex items-start gap-3 animate-slide-up">
          <AlertCircle className="w-5 h-5 text-status-error flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-status-error">Action failed</p>
            <p className="text-sm text-text-secondary">{actionMessage}</p>
          </div>
        </div>
      )}

      {/* Success Message */}
      {actionStatus === 'success' && (
        <div className="mb-6 p-4 rounded-lg bg-status-success-bg border border-status-success/20 flex items-center gap-3 animate-slide-up">
          <CheckCircle2 className="w-5 h-5 text-status-success flex-shrink-0" />
          <p className="text-sm font-medium text-status-success">{actionMessage}</p>
        </div>
      )}

      <div className="space-y-8">
        {/* ========================================
            MEMORY STATS SECTION
            ======================================== */}
        <section className="space-y-5">
          <div className="flex items-center gap-2">
            <Database className="w-5 h-5 text-accent-primary" />
            <h3 className="text-base font-semibold text-text-primary">Memory Storage</h3>
          </div>

          <div className="space-y-5 pl-4 border-l-2 border-border-primary">
            {/* Memory Count Card */}
            <div className="p-6 bg-bg-secondary rounded-lg border border-border-primary">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <div className="w-12 h-12 rounded-lg bg-accent-subtle flex items-center justify-center">
                    <Brain className="w-6 h-6 text-accent-primary" />
                  </div>
                  <div>
                    <p className="text-3xl font-bold text-text-primary">
                      {stats?.total?.toLocaleString() ?? 0}
                    </p>
                    <p className="text-sm text-text-muted">stored memories</p>
                  </div>
                </div>
                <div className="text-right">
                  <div className="flex items-center gap-2 text-sm text-text-secondary">
                    <Clock className="w-4 h-4" />
                    <span>Last updated: just now</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Memory Browser */}
            <div className="bg-bg-secondary rounded-lg border border-border-primary">
              {/* Header */}
              <div className="p-4 border-b border-border-primary">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg bg-accent-subtle flex items-center justify-center">
                      <Search className="w-5 h-5 text-accent-primary" />
                    </div>
                    <div>
                      <h3 className="font-medium text-text-primary">Memory Browser</h3>
                      <p className="text-xs text-text-muted">
                        {memoriesTotal > 0
                          ? `${memoriesTotal} memories found`
                          : 'View, search, and manage individual memories'}
                      </p>
                    </div>
                  </div>
                  <span className="px-3 py-1 text-xs font-medium bg-status-success-bg text-status-success rounded-full">
                    Ready
                  </span>
                </div>
              </div>

              {/* Filters */}
              <div className="p-4 border-b border-border-primary bg-bg-tertiary/30">
                <MemoryFilters onFilterChange={handleFilterChange} />
              </div>

              {/* Content */}
              <div className="p-4">
                {viewMode === 'detail' && selectedMemory ? (
                  <MemoryDetail
                    memory={selectedMemory}
                    onBack={handleBackToList}
                    onCorrect={handleCorrectMemory}
                    onDelete={handleDeleteMemory}
                  />
                ) : (
                  <div className="space-y-3">
                    {memoriesLoading && memories.length === 0 ? (
                      <div className="space-y-3">
                        {[1, 2, 3].map((i) => (
                          <SkeletonBlock key={i} height={80} />
                        ))}
                      </div>
                    ) : memoriesError ? (
                      <div className="p-4 rounded-lg bg-status-error-bg border border-status-error/20">
                        <p className="text-sm text-status-error">{memoriesError}</p>
                      </div>
                    ) : memories.length === 0 ? (
                      <div className="py-8 text-center">
                        <MessageSquare className="w-8 h-8 text-text-muted mx-auto mb-2" />
                        <p className="text-sm text-text-muted">No memories found</p>
                        <p className="text-xs text-text-muted mt-1">
                          Try adjusting your filters or start a conversation
                        </p>
                      </div>
                    ) : (
                      <div className="space-y-2 max-h-[400px] overflow-y-auto pr-2">
                        {memories.map((memory) => (
                          <MemoryCard
                            key={memory.id}
                            memory={memory}
                            onSelect={handleSelectMemory}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </section>

        {/* ========================================
            DANGER ZONE SECTION
            ======================================== */}
        <section className="space-y-5">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-status-error" />
            <h3 className="text-base font-semibold text-text-primary">Danger Zone</h3>
          </div>

          <div className="space-y-5 pl-4 border-l-2 border-border-primary">
            {/* Clear All Memories */}
            <div className="flex items-center justify-between p-4 bg-bg-secondary rounded-lg border border-status-error/30">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-md bg-status-error-bg flex items-center justify-center">
                  <Trash2 className="w-4 h-4 text-status-error" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-text-primary">
                    Clear All Memories
                  </label>
                  <p className="text-xs text-text-muted">
                    Permanently delete all stored memories. This action cannot be undone.
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setShowConfirmDialog(true)}
                disabled={actionStatus === 'loading' || (stats?.total ?? 0) === 0}
                className="inline-flex items-center gap-2 px-4 py-2 bg-status-error-bg border border-status-error/50 text-status-error hover:bg-status-error hover:text-white font-medium rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-status-error/50"
              >
                {actionStatus === 'loading' ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Clearing...</span>
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" />
                    <span>Clear All</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </section>
      </div>

      {/* Confirmation Dialog */}
      {showConfirmDialog && (
        <div className="fixed inset-0 z-modal flex items-center justify-center p-4">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-bg-overlay"
            onClick={() => setShowConfirmDialog(false)}
          />

          {/* Dialog */}
          <div className="relative w-full max-w-md bg-bg-secondary rounded-xl border border-border-primary shadow-xl animate-scale">
            <div className="p-6">
              {/* Dialog Header */}
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-full bg-status-error-bg flex items-center justify-center">
                  <AlertTriangle className="w-5 h-5 text-status-error" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-text-primary">
                    Clear All Memories?
                  </h3>
                </div>
              </div>

              {/* Dialog Body */}
              <p className="text-sm text-text-secondary mb-6">
                This will permanently delete all {stats?.total?.toLocaleString() ?? 0} stored
                memories. This action cannot be undone and all learned facts, preferences, and
                conversation context will be lost.
              </p>

              {/* Dialog Actions */}
              <div className="flex items-center justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setShowConfirmDialog(false)}
                  className="px-4 py-2 text-sm font-medium text-text-secondary hover:text-text-primary hover:bg-bg-tertiary rounded-md transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus/50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleClearMemories}
                  disabled={actionStatus === 'loading'}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-status-error text-white font-medium rounded-md hover:bg-status-error/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-status-error/50"
                >
                  {actionStatus === 'loading' ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>Clearing...</span>
                    </>
                  ) : (
                    <>
                      <Trash2 className="w-4 h-4" />
                      <span>Yes, Clear All</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
