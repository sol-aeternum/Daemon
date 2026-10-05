'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ensureAuthHeader, getAuthGeneration } from '@/lib/auth';
import { useAuthGeneration } from '@/hooks/useAuthGeneration';
import { HOME_SUGGESTIONS_ENDPOINTS } from '@/lib/homeSuggestions';

type Preference = boolean | null;
type View = {
  generation: number;
  enabled: Preference;
  pending: boolean;
  unconfirmed: boolean;
};

function readPreference(value: unknown): Preference {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const preferences = (value as { preferences?: unknown }).preferences;
  if (
    !preferences ||
    typeof preferences !== 'object' ||
    Array.isArray(preferences)
  )
    return null;
  const enabled = (preferences as { home_suggestions_enabled?: unknown })
    .home_suggestions_enabled;
  return enabled === undefined
    ? false
    : typeof enabled === 'boolean'
      ? enabled
      : null;
}

/** Preference-only control: never reads candidates or starts generation. */
export function HomeSuggestionsPreference() {
  const generation = useAuthGeneration();
  const [view, setView] = useState<View>({
    generation,
    enabled: null,
    pending: true,
    unconfirmed: false,
  });
  const active = useRef<AbortController | null>(null);

  const run = useCallback(async (enabled?: boolean) => {
    if (active.current) return;
    const owner = new AbortController();
    active.current = owner;
    const lifetime = getAuthGeneration();
    const current = () =>
      active.current === owner &&
      !owner.signal.aborted &&
      lifetime === getAuthGeneration();
    setView((previous) => ({
      generation: lifetime,
      enabled: previous.generation === lifetime ? previous.enabled : null,
      pending: true,
      unconfirmed: false,
    }));

    // Bound auth refresh, response headers and JSON parsing as well as fetch.
    const request = async (method: 'GET' | 'PATCH') => {
      const controller = new AbortController();
      const cancel = () => controller.abort();
      owner.signal.addEventListener('abort', cancel, { once: true });
      const timer = window.setTimeout(
        cancel,
        method === 'PATCH' ? 10000 : 5000,
      );
      let rejectAbort: () => void = () => {};
      const aborted = new Promise<never>((_, reject) => {
        rejectAbort = () => reject(new Error('Preference request cancelled'));
        controller.signal.addEventListener('abort', rejectAbort, {
          once: true,
        });
      });
      const assertCurrent = () => {
        if (!current() || controller.signal.aborted)
          throw new Error('Stale preference request');
      };
      try {
        return await Promise.race([
          aborted,
          (async () => {
            assertCurrent();
            const header = await ensureAuthHeader();
            assertCurrent();
            if (!header) throw new Error('Sign in required');
            const response = await fetch(HOME_SUGGESTIONS_ENDPOINTS.settings, {
              method,
              headers: {
                Authorization: header,
                'Content-Type': 'application/json',
              },
              credentials: 'include',
              cache: 'no-store',
              signal: controller.signal,
              ...(method === 'PATCH'
                ? {
                    body: JSON.stringify({
                      preferences: { home_suggestions_enabled: enabled },
                    }),
                  }
                : {}),
            });
            assertCurrent();
            if (!response.ok) throw new Error('Preference request failed');
            const result =
              method === 'GET'
                ? readPreference(await response.json())
                : enabled!;
            assertCurrent();
            return result;
          })(),
        ]);
      } finally {
        window.clearTimeout(timer);
        owner.signal.removeEventListener('abort', cancel);
        controller.signal.removeEventListener('abort', rejectAbort);
      }
    };

    try {
      const result = await request(enabled === undefined ? 'GET' : 'PATCH');
      if (current())
        setView({
          generation: lifetime,
          enabled: result,
          pending: false,
          unconfirmed: false,
        });
    } catch {
      if (!current()) return;
      // A failed write may have committed. Keep retry-off even if the stored
      // flag reads false: Redis synchronization was not acknowledged.
      setView({
        generation: lifetime,
        enabled: null,
        pending: enabled !== undefined,
        unconfirmed: enabled !== undefined,
      });
      if (enabled !== undefined) {
        let result: Preference = null;
        try {
          result = await request('GET');
        } catch {
          /* Remain unknown. */
        }
        if (current())
          setView({
            generation: lifetime,
            enabled: result,
            pending: false,
            unconfirmed: true,
          });
      }
    } finally {
      if (active.current === owner) active.current = null;
    }
  }, []);

  useEffect(() => {
    void run();
    return () => {
      active.current?.abort();
      active.current = null;
    };
  }, [generation, run]);

  const scoped =
    view.generation === generation
      ? view
      : { enabled: null, pending: true, unconfirmed: false };
  return (
    <section
      aria-labelledby="home-suggestions-preference"
      className="mt-8 border-t border-border-primary pt-6 space-y-3"
    >
      <h3
        id="home-suggestions-preference"
        className="text-sm font-medium text-text-primary"
      >
        Personal suggestions
      </h3>
      <p className="text-sm text-text-secondary">
        Suggest next tasks from your recent cloud conversations.
      </p>
      <p role="status" className="text-xs text-text-muted">
        {scoped.pending
          ? 'Checking suggestion preference…'
          : scoped.unconfirmed
            ? `The change could not be confirmed. ${scoped.enabled === null ? 'Suggestions may be enabled.' : `Saved preference: ${scoped.enabled ? 'on' : 'off'}; activation or deactivation is unconfirmed.`} You can retry turning them off.`
            : scoped.enabled === null
              ? 'Suggestion preference is unavailable; suggestions may be enabled.'
              : `Personal suggestions are ${scoped.enabled ? 'on' : 'off'}.`}
      </p>
      <button
        type="button"
        disabled={scoped.pending}
        className="min-h-touch rounded-md border border-border-primary px-3 py-2 text-sm text-text-primary disabled:opacity-50"
        onClick={() => {
          void run(scoped.enabled === false && !scoped.unconfirmed);
        }}
      >
        {scoped.enabled === false && !scoped.unconfirmed
          ? 'Turn on suggestions'
          : 'Turn off suggestions'}
      </button>
    </section>
  );
}
