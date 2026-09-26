'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ensureAuthHeader } from '@/lib/auth';
import {
  hasCapability,
  parseEntitlements,
  type Capability,
  type Entitlements,
  type EntitlementsParseResult,
} from '@/lib/entitlements';

/** Same-origin proxy so the browser never talks to the backend directly. */
export const ENTITLEMENTS_ENDPOINT = '/api/entitlements';

export type EntitlementsStatus = 'loading' | 'ready' | 'error';

export interface EntitlementsResult {
  /** Null until the server confirms a plan; never defaulted to a paid plan. */
  entitlements: Entitlements | null;
  status: EntitlementsStatus;
  error: string | null;
  plan: Entitlements['plan'] | null;
  can: (capability: Capability) => boolean;
  refresh: () => Promise<void>;
}

function describeFailure(status: number): string {
  if (status === 401) {
    return 'Sign in to view your plan.';
  }
  if (status === 403) {
    return 'This session cannot view plan details.';
  }
  if (status === 404) {
    return 'Plan details are not available yet.';
  }
  if (status >= 500) {
    return 'Plan service is unavailable.';
  }
  return `Could not load your plan (${status}).`;
}

/**
 * Reads the authenticated account's plan from the server.
 *
 * Each consumer fetches on mount so a plan change is picked up on navigation.
 * Every failure path keeps `entitlements` null, which fails capability checks
 * closed — the backend remains the enforcement point either way.
 */
export function useEntitlements(): EntitlementsResult {
  const [entitlements, setEntitlements] = useState<Entitlements | null>(null);
  const [status, setStatus] = useState<EntitlementsStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  /**
   * Reads one snapshot. Returns null when a newer request superseded this one,
   * so a slow response can never overwrite a later answer.
   */
  const fetchSnapshot =
    useCallback(async (): Promise<EntitlementsParseResult | null> => {
      requestIdRef.current += 1;
      const requestId = requestIdRef.current;

      let result: EntitlementsParseResult;
      try {
        const authHeader = await ensureAuthHeader();
        const response = await fetch(ENTITLEMENTS_ENDPOINT, {
          credentials: 'include',
          cache: 'no-store',
          headers: authHeader ? { Authorization: authHeader } : {},
        });

        result = response.ok
          ? parseEntitlements(await response.json())
          : { ok: false, error: describeFailure(response.status) };
      } catch {
        result = { ok: false, error: 'Could not reach the plan service.' };
      }

      return requestIdRef.current === requestId ? result : null;
    }, []);

  const apply = useCallback((result: EntitlementsParseResult): void => {
    if (result.ok) {
      setEntitlements(result.entitlements);
      setStatus('ready');
      setError(null);
      return;
    }

    setEntitlements(null);
    setStatus('error');
    setError(result.error);
  }, []);

  useEffect(() => {
    // Mount already starts in `loading`, so this only settles state once the
    // request resolves; nothing is written back synchronously.
    let cancelled = false;
    void (async () => {
      const result = await fetchSnapshot();
      if (cancelled || !result) return;
      apply(result);
    })();

    return () => {
      cancelled = true;
    };
  }, [apply, fetchSnapshot]);

  const refresh = useCallback(async (): Promise<void> => {
    setStatus('loading');
    setError(null);
    const result = await fetchSnapshot();
    if (!result) return;
    apply(result);
  }, [apply, fetchSnapshot]);

  const can = useCallback(
    (capability: Capability) => hasCapability(entitlements, capability),
    [entitlements],
  );

  return {
    entitlements,
    status,
    error,
    plan: entitlements?.plan ?? null,
    can,
    refresh,
  };
}
