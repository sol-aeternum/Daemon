'use client';

import { useState, useEffect, useCallback } from 'react';
import { TrailItem, useMemories } from '@/hooks/useMemories';
import { formatRelativeTime } from '@/lib/format';
import {
  ChevronDown,
  ChevronRight,
  History,
  Trash2,
  XCircle,
  CheckCircle2,
} from 'lucide-react';

interface TrailViewProps {
  memoryId: string;
}

// Extended TrailItem that might include additional fields from the API
type TrailNode = TrailItem & {
  status?: 'active' | 'superseded' | 'rejected' | 'deleted';
  valid_from?: string;
  valid_to?: string | null;
};

const statusConfig = {
  active: {
    label: 'Current',
    icon: CheckCircle2,
    color: 'text-status-success',
    bgColor: 'bg-status-success-bg',
    borderColor: 'border-status-success/30',
  },
  superseded: {
    label: 'Superseded',
    icon: History,
    color: 'text-text-muted',
    bgColor: 'bg-bg-tertiary',
    borderColor: 'border-border-primary',
  },
  rejected: {
    label: 'Rejected',
    icon: XCircle,
    color: 'text-status-error',
    bgColor: 'bg-status-error-bg',
    borderColor: 'border-status-error/30',
  },
  deleted: {
    label: 'Deleted',
    icon: Trash2,
    color: 'text-status-error',
    bgColor: 'bg-status-error-bg',
    borderColor: 'border-status-error/30',
  },
} as const;

function getStatusConfig(
  status: string | undefined,
  isLast: boolean,
): (typeof statusConfig)[keyof typeof statusConfig] {
  if (status && status in statusConfig) {
    return statusConfig[status as keyof typeof statusConfig];
  }
  // Fallback: last item is active, others are superseded
  return isLast ? statusConfig.active : statusConfig.superseded;
}

function truncateContent(content: string, maxLength: number = 120): string {
  if (content.length <= maxLength) return content;
  return content.slice(0, maxLength).trim() + '...';
}

export function TrailView({ memoryId }: TrailViewProps) {
  const [trail, setTrail] = useState<TrailNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const { fetchTrail } = useMemories();

  const loadTrail = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const data = await fetchTrail(memoryId);
      // Cast to TrailNode to handle potential additional fields from API
      setTrail(data as TrailNode[]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load trail');
    } finally {
      setLoading(false);
    }
  }, [fetchTrail, memoryId]);

  useEffect(() => {
    loadTrail();
  }, [loadTrail]);

  // Auto-collapse if only 1 version
  useEffect(() => {
    if (trail.length <= 1) {
      setIsExpanded(false);
    }
  }, [trail.length]);

  const toggleExpanded = () => {
    setIsExpanded((prev) => !prev);
  };

  if (loading) {
    return (
      <div className="mt-4 pt-4 border-t border-border-primary">
        <div className="flex items-center gap-2 text-text-muted animate-pulse">
          <History className="w-4 h-4" />
          <span className="text-sm">Loading history...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mt-4 pt-4 border-t border-border-primary">
        <div className="flex items-center gap-2 text-status-error">
          <XCircle className="w-4 h-4" />
          <span className="text-sm">{error}</span>
        </div>
      </div>
    );
  }

  if (trail.length === 0) {
    return null;
  }

  const currentVersion = trail[trail.length - 1];
  const hasHistory = trail.length > 1;

  return (
    <div className="mt-4 pt-4 border-t border-border-primary">
      {/* Toggle button */}
      <button
        type="button"
        onClick={toggleExpanded}
        className="flex items-center gap-2 text-sm text-text-secondary hover:text-text-primary transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus/50 rounded"
      >
        {isExpanded ? (
          <ChevronDown className="w-4 h-4" />
        ) : (
          <ChevronRight className="w-4 h-4" />
        )}
        <History className="w-4 h-4" />
        <span>
          {isExpanded
            ? 'Hide history'
            : hasHistory
              ? `Show history (${trail.length} versions)`
              : 'Show history'}
        </span>
      </button>

      {/* Trail content */}
      {isExpanded && (
        <div className="mt-4 space-y-0">
          {/* Vertical timeline line */}
          <div className="relative">
            {/* Connecting line */}
            <div className="absolute left-[15px] top-6 bottom-6 w-px bg-border-primary" />

            {/* Trail nodes */}
            <div className="space-y-4">
              {trail.map((node, index) => {
                const isLast = index === trail.length - 1;
                const config = getStatusConfig(node.status, isLast);
                const StatusIcon = config.icon;
                const isTombstone =
                  node.status === 'deleted' || node.status === 'rejected';

                // Determine valid time range display
                const validFrom = node.changed_at;
                const validTo = node.valid_to;
                const timeRange = validTo
                  ? `${formatRelativeTime(validFrom)} → ${formatRelativeTime(validTo)}`
                  : `${formatRelativeTime(validFrom)} → current`;

                return (
                  <div
                    key={node.id}
                    className={`relative flex items-start gap-4 ${
                      isLast ? '' : 'opacity-70'
                    }`}
                  >
                    {/* Status indicator dot */}
                    <div className="relative z-10 flex-shrink-0">
                      <div
                        className={`w-8 h-8 rounded-full ${config.bgColor} ${config.borderColor} border-2 flex items-center justify-center`}
                      >
                        <StatusIcon className={`w-4 h-4 ${config.color}`} />
                      </div>
                    </div>

                    {/* Content card */}
                    <div
                      className={`flex-1 min-w-0 p-3 rounded-lg border ${
                        isLast
                          ? 'bg-bg-secondary border-border-primary'
                          : 'bg-bg-tertiary border-border-primary'
                      } ${isTombstone ? 'border-status-error/20' : ''}`}
                    >
                      {/* Content snippet */}
                      <p
                        className={`text-sm ${
                          isTombstone
                            ? 'text-text-muted line-through'
                            : 'text-text-primary'
                        }`}
                      >
                        {truncateContent(node.content)}
                      </p>

                      {/* Meta row */}
                      <div className="flex flex-wrap items-center gap-2 mt-2">
                        {/* Status badge */}
                        <span
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${config.bgColor} ${config.color}`}
                        >
                          <StatusIcon className="w-3 h-3" />
                          {config.label}
                        </span>

                        {/* Category badge */}
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-bg-tertiary text-text-muted">
                          {node.category}
                        </span>

                        {/* Time range */}
                        <span className="text-xs text-text-muted">
                          {timeRange}
                        </span>
                      </div>

                      {/* Tombstone message */}
                      {isTombstone && (
                        <p className="mt-2 text-xs text-status-error">
                          Removed by user
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Collapsed summary */}
      {!isExpanded && hasHistory && (
        <div className="mt-3 text-xs text-text-muted">
          Current version from {formatRelativeTime(currentVersion.changed_at)}
          {trail.length > 1 && (
            <span className="ml-2">
              ({trail.length - 1} previous{' '}
              {trail.length - 1 === 1 ? 'version' : 'versions'})
            </span>
          )}
        </div>
      )}
    </div>
  );
}
