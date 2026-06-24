'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Coins,
  ExternalLink,
  Loader2,
  RefreshCw,
  TriangleAlert,
} from 'lucide-react';
import { ensureAuthHeader } from '@/lib/auth';

export interface CreditBalanceProps {
  mode: 'compact' | 'expanded';
  userId: string;
  refreshInterval?: number;
}

type BalanceResponse = {
  balance?: number;
};

const DEFAULT_REFRESH_INTERVAL_MS = 30_000;

function getApiBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_URL || '';
  if (fromEnv.trim().length > 0) {
    return fromEnv.replace(/\/$/, '');
  }
  if (process.env.NODE_ENV === 'development') {
    return 'http://localhost:8000';
  }
  return '';
}

export function CreditBalance({
  mode,
  userId,
  refreshInterval,
}: CreditBalanceProps) {
  const [balance, setBalance] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const mountedRef = useRef(true);

  const apiBaseUrl = useMemo(() => getApiBaseUrl(), []);
  const effectiveRefreshInterval =
    refreshInterval ?? DEFAULT_REFRESH_INTERVAL_MS;

  const fetchBalance = useCallback(
    async (silent = false) => {
      if (!userId) {
        if (mountedRef.current) {
          setBalance(null);
          setError('Missing user ID');
          setIsLoading(false);
        }
        return;
      }

      if (mountedRef.current) {
        if (silent) {
          setIsRefreshing(true);
        } else {
          setIsLoading(true);
        }
        setError(null);
      }

      const query = new URLSearchParams({ user_id: userId });
      const proxyPath = `/api/video-credits/balance?${query.toString()}`;
      const directPath = `/video-credits/balance?${query.toString()}`;
      const candidates = apiBaseUrl
        ? [proxyPath, `${apiBaseUrl}${directPath}`, directPath]
        : [proxyPath, directPath];

      let lastError: unknown = null;

      for (let index = 0; index < candidates.length; index += 1) {
        const candidate = candidates[index];
        try {
          const authHeader = await ensureAuthHeader();
          const response = await fetch(candidate, {
            headers: {
              ...(authHeader ? { Authorization: authHeader } : {}),
            },
            cache: 'no-store',
          });

          if (response.status === 404 && index < candidates.length - 1) {
            continue;
          }

          if (!response.ok) {
            throw new Error(`Balance request failed (${response.status})`);
          }

          const data = (await response.json()) as BalanceResponse;
          const value = typeof data.balance === 'number' ? data.balance : 0;

          if (mountedRef.current) {
            setBalance(value);
            setError(null);
          }
          return;
        } catch (fetchError) {
          lastError = fetchError;
          if (index === candidates.length - 1 && mountedRef.current) {
            setError(
              fetchError instanceof Error
                ? fetchError.message
                : 'Failed to fetch balance',
            );
          }
        }
      }

      if (mountedRef.current && lastError) {
        setBalance((previous) => (previous === null ? 0 : previous));
      }
    },
    [apiBaseUrl, userId],
  );

  useEffect(() => {
    mountedRef.current = true;
    void fetchBalance(false).finally(() => {
      if (mountedRef.current) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    });

    return () => {
      mountedRef.current = false;
    };
  }, [fetchBalance]);

  useEffect(() => {
    if (effectiveRefreshInterval <= 0) {
      return;
    }

    const timer = window.setInterval(() => {
      void fetchBalance(true).finally(() => {
        if (mountedRef.current) {
          setIsRefreshing(false);
        }
      });
    }, effectiveRefreshInterval);

    return () => {
      window.clearInterval(timer);
    };
  }, [effectiveRefreshInterval, fetchBalance]);

  useEffect(() => {
    const onRefresh = () => {
      void fetchBalance(true).finally(() => {
        if (mountedRef.current) {
          setIsRefreshing(false);
        }
      });
    };

    window.addEventListener('video-credits:refresh', onRefresh);
    return () => {
      window.removeEventListener('video-credits:refresh', onRefresh);
    };
  }, [fetchBalance]);

  const handleViewTransactions = useCallback(async () => {
    if (!userId) return;
    const authHeader = await ensureAuthHeader();
    const query = new URLSearchParams({
      user_id: userId,
      limit: '50',
      offset: '0',
    });
    const proxyPath = `/api/video-credits/transactions?${query.toString()}`;
    const directPath = `/video-credits/transactions?${query.toString()}`;
    const candidates = apiBaseUrl
      ? [proxyPath, `${apiBaseUrl}${directPath}`, directPath]
      : [proxyPath, directPath];

    for (const candidate of candidates) {
      try {
        const response = await fetch(candidate, {
          headers: {
            ...(authHeader ? { Authorization: authHeader } : {}),
          },
          cache: 'no-store',
        });
        if (response.ok) {
          const json = await response.json();
          const text = JSON.stringify(json, null, 2);
          const blob = new Blob([text], { type: 'text/plain' });
          const url = URL.createObjectURL(blob);
          window.open(url, '_blank', 'noopener,noreferrer');
          setTimeout(() => URL.revokeObjectURL(url), 10000);
          return;
        }
      } catch (_err) {}
    }
  }, [apiBaseUrl, userId]);

  const renderedBalance = balance ?? 0;

  if (mode === 'compact') {
    return (
      <div className="inline-flex items-center gap-2 rounded-full border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)]">
        <Coins className="h-3.5 w-3.5 text-[var(--color-accent-primary)]" />
        {isLoading ? 'Loading credits...' : `${renderedBalance} credits`}
        {isRefreshing && (
          <Loader2 className="h-3 w-3 animate-spin text-[var(--color-text-muted)]" />
        )}
      </div>
    );
  }

  return (
    <section className="rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-wide text-[var(--color-text-muted)]">
            Video credits
          </p>
          <div className="mt-1 flex items-center gap-2">
            <Coins className="h-4 w-4 text-[var(--color-accent-primary)]" />
            <p className="text-xl font-semibold text-[var(--color-text-primary)]">
              {isLoading ? '--' : renderedBalance}
              <span className="ml-2 text-sm font-normal text-[var(--color-text-secondary)]">
                available
              </span>
            </p>
          </div>
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">
            Balance refreshes automatically and after generation events.
          </p>
        </div>

        <button
          type="button"
          onClick={() => {
            void fetchBalance(true).finally(() => {
              if (mountedRef.current) {
                setIsRefreshing(false);
              }
            });
          }}
          className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border-primary)] px-2.5 py-1.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
          disabled={isRefreshing}
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`}
          />
          Refresh
        </button>
      </div>

      {error && (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-[var(--color-status-warning)]/40 bg-[var(--color-status-warning-bg)]/35 p-2 text-xs text-[var(--color-text-secondary)]">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 text-[var(--color-status-warning)]" />
          <span>{error}</span>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between border-t border-[var(--color-border-primary)] pt-3">
        <span className="text-xs text-[var(--color-text-muted)]">
          Need more detail?
        </span>
        <button
          type="button"
          onClick={handleViewTransactions}
          className="inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent-primary)] hover:text-[var(--color-accent-hover)]"
        >
          View transactions
          <ExternalLink className="h-3.5 w-3.5" />
        </button>
      </div>
    </section>
  );
}
