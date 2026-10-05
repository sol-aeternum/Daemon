'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  dropExpiredSuggestions,
  parseHomeSuggestionsBody,
  parseHomeSuggestionsRefreshBody,
  HOME_SUGGESTIONS_ENDPOINTS,
} from '@/lib/homeSuggestions';
import type {
  HomeSuggestion,
  HomeSuggestionsViewState,
} from '@/lib/homeSuggestions';
import {
  ensureAuthHeader,
  getAccessToken,
  getAuthGeneration,
  subscribeAuthGeneration,
} from '@/lib/auth';
import { useAuthGeneration } from './useAuthGeneration';

export type { HomeSuggestion, HomeSuggestionsViewState };

interface HookState {
  status:
    | 'unloaded'
    | 'loading'
    | 'ready'
    | 'generating'
    | 'empty'
    | 'expired'
    | 'unavailable'
    | 'error'
    | 'disabled';
  suggestions: HomeSuggestion[];
  message: string | null;
}

const INITIAL_STATE: HookState = {
  status: 'unloaded',
  suggestions: [],
  message: null,
};

const POLL_ATTEMPTS_MAX = 10;
const POLL_DELAYS_MS = [1500, 3000, 5000, 8000, 12000, 15000] as const;

const NETWORK_FAILURE_MESSAGE =
  "Couldn't reach Daemon, so suggestions didn't load.";

function mapHttpFailure(
  status: number,
  serverMessage: string | null,
): { status: HookState['status']; message: string | null } {
  switch (status) {
    case 401:
    case 403:
      return { status: 'unavailable', message: null };
    case 429:
      return {
        status: 'error',
        message: serverMessage ?? 'Suggestions were refreshed too recently.',
      };
    case 409:
      return {
        status: 'error',
        message: serverMessage ?? 'A refresh is already running.',
      };
    case 503:
      return {
        status: 'unavailable',
        message: serverMessage ?? 'Suggestions are unavailable right now.',
      };
    default:
      return {
        status: 'error',
        message: serverMessage ?? 'Suggestions could not be loaded.',
      };
  }
}

/**
 * Loads and manages the zero-to-three contextual home suggestions for the
 * current sign-in. Every async result is discarded when the auth generation
 * changes or the component unmounts: a response scoped to another account
 * never renders its private metadata and never starts a follow-up refresh.
 * Expired rows are hidden instead of served. Nothing here is persisted in
 * the browser and no request fires per keystroke.
 */
export function useHomeSuggestions() {
  const generation = useAuthGeneration();
  const stateGenerationRef = useRef(generation);
  const [state, setState] = useState<HookState>(INITIAL_STATE);
  // Session-local only: a reload shows rows again, no server row changes
  // and nothing is written to browser storage.
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(new Set());
  const [hiddenAll, setHiddenAll] = useState(false);

  const requestIdRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const pollAttemptRef = useRef(0);
  const preferencePendingRef = useRef(false);
  const [preferencePending, setPreferencePending] = useState(false);

  const clearPollTimer = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    pollAttemptRef.current = 0;
  }, []);

  const settleChain = useCallback(() => {
    requestIdRef.current += 1;
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    clearPollTimer();
  }, [clearPollTimer]);

  /**
   * A request may settle itself only if no newer chain started and the
   * sign-in that sent it is still the current one.
   */
  const isCurrent = useCallback(
    (request: number, generationAtRequest: number) =>
      request === requestIdRef.current &&
      generationAtRequest === getAuthGeneration(),
    [],
  );

  const stopIfStale = useCallback(
    (
      request: number,
      generationAtRequest: number,
      controller: AbortController,
    ) => {
      if (request === requestIdRef.current && abortRef.current === controller) {
        abortRef.current = null;
      }
    },
    [],
  );

  /** One GET /home-suggestions read. Returns true when still generating. */
  const loadOnce = useCallback(async (): Promise<boolean> => {
    const generationAtRequest = getAuthGeneration();
    // A fresh tab starts at generation zero with no in-memory token: permit
    // existing cookie-session restoration via ensureAuthHeader. Revoked sign-in
    // lifetimes are nonzero and must not initiate a fresh read after logout.
    if (!getAccessToken() && generationAtRequest !== 0) return false;
    const request = ++requestIdRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setState((previous) => {
      if (previous.status === 'ready') return previous;
      return { status: 'loading', suggestions: [], message: null };
    });
    try {
      const header = await ensureAuthHeader();
      if (!header && isCurrent(request, generationAtRequest)) {
        setState({
          status: 'unavailable',
          suggestions: [],
          message: 'Sign in to load suggestions.',
        });
      }
      if (
        !header ||
        generationAtRequest !== getAuthGeneration() ||
        controller.signal.aborted ||
        request !== requestIdRef.current
      ) {
        return false;
      }
      const response = await fetch(HOME_SUGGESTIONS_ENDPOINTS.list, {
        method: 'GET',
        headers: { Authorization: header },
        // Never serve cached private suggestion payloads from an HTTP cache.
        cache: 'no-store',
        credentials: 'include',
        signal: controller.signal,
      });
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        body = null;
      }
      if (!isCurrent(request, generationAtRequest)) return false;
      if (!response.ok) {
        const mapped = mapHttpFailure(response.status, null);
        setState({
          status: mapped.status,
          suggestions: [],
          message: mapped.message,
        });
        return false;
      }
      const parsed = parseHomeSuggestionsBody(body);
      if (!parsed) {
        setState({
          status: 'unavailable',
          suggestions: [],
          message: 'Suggestions sent an unexpected response.',
        });
        return false;
      }
      if (parsed.enabled === false || parsed.status === 'disabled') {
        setState({
          status: 'disabled',
          suggestions: [],
          message: parsed.message,
        });
        return false;
      }
      if (parsed.status === 'generating') {
        setState({
          status: 'generating',
          suggestions: [],
          message: parsed.message,
        });
        return true;
      }
      if (parsed.status === 'error' || parsed.status === 'unavailable') {
        setState({
          status: parsed.status,
          suggestions: [],
          message: parsed.message,
        });
        return false;
      }
      const live = dropExpiredSuggestions(parsed.suggestions);
      setState({
        status:
          parsed.status === 'expired' ||
          (parsed.suggestions.length > 0 && live.length === 0)
            ? 'expired'
            : live.length === 0
              ? 'empty'
              : 'ready',
        suggestions: live,
        message: parsed.message,
      });
      return false;
    } catch (error) {
      if (
        controller.signal.aborted ||
        !isCurrent(request, generationAtRequest)
      ) {
        return false;
      }
      setState({
        status: 'error',
        suggestions: [],
        message:
          error instanceof DOMException && error.name === 'AbortError'
            ? 'Suggestions took too long to load. Try again.'
            : NETWORK_FAILURE_MESSAGE,
      });
      return false;
    } finally {
      stopIfStale(request, generationAtRequest, controller);
    }
  }, [isCurrent, stopIfStale]);

  const schedulePoll = useCallback(() => {
    const generationAtPoll = getAuthGeneration();
    if (!getAccessToken()) return;
    if (pollAttemptRef.current >= POLL_ATTEMPTS_MAX) {
      setState({
        status: 'unavailable',
        suggestions: [],
        message:
          'Suggestions are taking longer than expected. Refresh to try again.',
      });
      return;
    }
    const attempt = pollAttemptRef.current;
    pollAttemptRef.current = attempt + 1;
    const delay = POLL_DELAYS_MS[Math.min(attempt, POLL_DELAYS_MS.length - 1)];
    pollTimerRef.current = window.setTimeout(() => {
      pollTimerRef.current = null;
      void loadOnce().then((stillGenerating) => {
        if (
          stillGenerating &&
          pollTimerRef.current === null &&
          getAuthGeneration() === generationAtPoll
        ) {
          schedulePoll();
        }
      });
    }, delay);
  }, [loadOnce]);

  /** View-triggered load: one GET, then bounded polling while generating. */
  const load = useCallback(async () => {
    settleChain();
    const stillGenerating = await loadOnce();
    if (stillGenerating && pollTimerRef.current === null) {
      pollAttemptRef.current = 0;
      schedulePoll();
    }
  }, [loadOnce, schedulePoll, settleChain]);

  const refreshRef = useRef<() => Promise<void>>(async () => undefined);

  /** POST /home-suggestions/refresh. Never fired by a mere page visit. */
  const refresh = useCallback(async () => {
    const generationAtRequest = getAuthGeneration();
    if (!getAccessToken()) return;
    settleChain();
    const request = requestIdRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const header = await ensureAuthHeader();
      if (
        !header ||
        generationAtRequest !== getAuthGeneration() ||
        controller.signal.aborted
      ) {
        return;
      }
      const response = await fetch(HOME_SUGGESTIONS_ENDPOINTS.refresh, {
        method: 'POST',
        headers: {
          Authorization: header,
          'Content-Type': 'application/json',
        },
        cache: 'no-store',
        credentials: 'include',
        body: JSON.stringify({}),
        signal: controller.signal,
      });
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        body = null;
      }
      if (!isCurrent(request, generationAtRequest)) return;
      const parsedOk = parseHomeSuggestionsRefreshBody(body);
      const serverMessage = parsedOk?.message ?? null;
      if (response.status === 202 && parsedOk?.status === 'queued') {
        setState((previous) => ({
          status: 'generating',
          suggestions: previous.suggestions,
          message: serverMessage ?? previous.message,
        }));
        pollAttemptRef.current = 0;
        schedulePoll();
        return;
      }
      if (!response.ok) {
        // A throttle or refusal keeps any still-live rows visible and adds
        // a truthful message; it never invents replacements.
        const mapped = mapHttpFailure(response.status, serverMessage);
        setState((previous) => ({
          status:
            previous.status === 'ready' || previous.status === 'generating'
              ? previous.status
              : mapped.status,
          suggestions: previous.suggestions,
          message: mapped.message ?? previous.message,
        }));
        return;
      }
      const parsed = parseHomeSuggestionsRefreshBody(body) ?? parsedOk;
      if (!parsed) {
        setState({
          status: 'unavailable',
          suggestions: [],
          message: 'The refresh reply was unexpected. Try again.',
        });
        return;
      }
      switch (parsed.status) {
        case 'queued':
          setState((previous) => ({
            status: 'generating',
            suggestions: previous.suggestions,
            message: serverMessage ?? previous.message,
          }));
          pollAttemptRef.current = 0;
          schedulePoll();
          break;
        case 'unchanged':
          await loadOnce();
          break;
        case 'disabled':
          setState({
            status: 'disabled',
            suggestions: [],
            message: serverMessage,
          });
          break;
        case 'unavailable':
          setState({
            status: 'unavailable',
            suggestions: [],
            message: serverMessage ?? 'Suggestions are unavailable right now.',
          });
          break;
      }
    } catch (error) {
      if (
        controller.signal.aborted ||
        !isCurrent(request, generationAtRequest)
      ) {
        return;
      }
      setState({
        status: 'error',
        suggestions: [],
        message:
          error instanceof DOMException && error.name === 'AbortError'
            ? 'The refresh timed out. Try again.'
            : NETWORK_FAILURE_MESSAGE,
      });
    } finally {
      stopIfStale(request, generationAtRequest, controller);
    }
  }, [isCurrent, loadOnce, schedulePoll, settleChain, stopIfStale]);

  useEffect(() => {
    refreshRef.current = refresh;
  }, [refresh]);

  const refreshViaRef = useCallback(() => refreshRef.current(), []);

  /**
   * Explicit one-time enable: PATCH the strict-boolean preference, then ask
   * the server for exactly one refresh. No auto-enable, and no refresh is
   * sent when the preference update itself did not succeed.
   */
  const enable = useCallback(async (): Promise<boolean> => {
    const generationAtRequest = getAuthGeneration();
    if (!getAccessToken() || preferencePendingRef.current) return false;
    preferencePendingRef.current = true;
    setPreferencePending(true);
    settleChain();
    const request = requestIdRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const header = await ensureAuthHeader();
      if (
        !header ||
        generationAtRequest !== getAuthGeneration() ||
        controller.signal.aborted
      ) {
        return false;
      }
      const response = await fetch(HOME_SUGGESTIONS_ENDPOINTS.settings, {
        method: 'PATCH',
        headers: {
          Authorization: header,
          'Content-Type': 'application/json',
        },
        cache: 'no-store',
        credentials: 'include',
        // Strict boolean is required by the settings contract; the shared
        // settings endpoint owns how preferences merge server-side.
        body: JSON.stringify({
          preferences: { home_suggestions_enabled: true },
        }),
        signal: controller.signal,
      });
      if (!isCurrent(request, generationAtRequest)) return false;
      if (!response.ok) {
        setState({
          status: response.status === 503 ? 'unavailable' : 'error',
          suggestions: [],
          message:
            response.status === 503
              ? 'Daemon is unavailable, so the feature was not enabled. Try again shortly.'
              : "Couldn't enable suggestions. Try again.",
        });
        return false;
      }
      // One explicit refresh kicks off the first generation; polling
      // continues only while the server reports generation in progress.
      await refresh();
      return true;
    } catch {
      if (
        controller.signal.aborted ||
        !isCurrent(request, generationAtRequest)
      ) {
        return false;
      }
      setState({
        status: 'error',
        suggestions: [],
        message: NETWORK_FAILURE_MESSAGE,
      });
      return false;
    } finally {
      stopIfStale(request, generationAtRequest, controller);
      if (generationAtRequest === getAuthGeneration()) {
        preferencePendingRef.current = false;
        setPreferencePending(false);
      }
    }
  }, [refresh, settleChain, stopIfStale, isCurrent]);

  /**
   * Immediate disable: hide rows here and abort every in-flight chain
   * before the server flag changes. Then persist the strict false
   * preference. Disabling stops future generation, not only rendering.
   */
  const disable = useCallback(async (): Promise<boolean> => {
    const generationAtRequest = getAuthGeneration();
    if (!getAccessToken() || preferencePendingRef.current) return false;
    preferencePendingRef.current = true;
    setPreferencePending(true);
    settleChain();
    const request = requestIdRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ status: 'disabled', suggestions: [], message: null });
    try {
      const header = await ensureAuthHeader();
      if (
        !header ||
        !isCurrent(request, generationAtRequest) ||
        controller.signal.aborted
      ) {
        return false;
      }
      const response = await fetch(HOME_SUGGESTIONS_ENDPOINTS.settings, {
        method: 'PATCH',
        headers: {
          Authorization: header,
          'Content-Type': 'application/json',
        },
        cache: 'no-store',
        credentials: 'include',
        signal: controller.signal,
        body: JSON.stringify({
          preferences: { home_suggestions_enabled: false },
        }),
      });
      if (!isCurrent(request, generationAtRequest)) return false;
      if (!response.ok) {
        setState({
          status: 'error',
          suggestions: [],
          message:
            'Suggestions are hidden, but turning them off was not confirmed. Use Turn off suggestions to retry.',
        });
        return false;
      }
      return true;
    } catch {
      if (!isCurrent(request, generationAtRequest) || controller.signal.aborted)
        return false;
      setState({
        status: 'error',
        suggestions: [],
        message:
          'Suggestions are hidden on this screen, but the connection to Daemon failed while confirming the change.',
      });
      return false;
    } finally {
      stopIfStale(request, generationAtRequest, controller);
      if (generationAtRequest === getAuthGeneration()) {
        preferencePendingRef.current = false;
        setPreferencePending(false);
      }
    }
  }, [settleChain, isCurrent, stopIfStale]);

  const dismiss = useCallback((id: string) => {
    setDismissedIds((previous) => {
      if (previous.has(id)) return previous;
      const next = new Set(previous);
      next.add(id);
      return next;
    });
  }, []);

  /** Session-local undo: works only while the row is still live. */
  const undoDismiss = useCallback((id: string) => {
    setDismissedIds((previous) => {
      if (!previous.has(id)) return previous;
      const next = new Set(previous);
      next.delete(id);
      return next;
    });
  }, []);

  const restoreAll = useCallback(() => {
    setDismissedIds(new Set());
    setHiddenAll(false);
  }, []);

  const dismissAll = useCallback(() => {
    setHiddenAll(true);
  }, []);

  // A sign-in change discards every in-flight request, response, poll and
  // session-local dismissal.
  useEffect(
    () =>
      subscribeAuthGeneration(() => {
        settleChain();
        stateGenerationRef.current = getAuthGeneration();
        setState(INITIAL_STATE);
        preferencePendingRef.current = false;
        setPreferencePending(false);
        setDismissedIds(new Set());
        setHiddenAll(false);
      }),
    [settleChain],
  );

  // One read per mount/sign-in generation; GET never generates.
  useEffect(() => {
    if (!getAccessToken() && generation !== 0) {
      settleChain();
      setState(INITIAL_STATE);
      return undefined;
    }
    settleChain();
    void loadOnce().then((generating) => {
      if (generating) {
        pollAttemptRef.current = 0;
        schedulePoll();
      }
    });
    return () => {
      settleChain();
    };
  }, [generation, loadOnce, schedulePoll, settleChain]);

  useEffect(() => {
    if (!state.suggestions.length) return;
    const deadline = Math.min(
      ...state.suggestions.map((s) => Date.parse(s.expiresAt)),
    );
    const timer = window.setTimeout(
      () => {
        setState((previous) => {
          const live = dropExpiredSuggestions(previous.suggestions);
          return {
            ...previous,
            suggestions: live,
            status: live.length ? previous.status : 'expired',
          };
        });
      },
      Math.max(0, Math.min(deadline - Date.now(), 2_147_483_647)),
    );
    return () => window.clearTimeout(timer);
  }, [state.suggestions]);

  // Mask synchronously on the first render after revocation, before effects.
  const scopedState =
    stateGenerationRef.current === generation ? state : INITIAL_STATE;
  const serverSuggestions = dropExpiredSuggestions(scopedState.suggestions);

  const view: HomeSuggestionsViewState = {
    status:
      serverSuggestions.length > 0 &&
      serverSuggestions.every((s) => dismissedIds.has(s.id))
        ? 'dismissed'
        : scopedState.status,
    suggestions: serverSuggestions.filter((s) => !dismissedIds.has(s.id)),
    message: scopedState.message,
  };

  return {
    view,
    preferencePending,
    /** Everything currently live from the server, including dismissed rows. */
    allSuggestions: serverSuggestions,
    dismissedIds,
    /** Session-local control to re-show every row after a dismiss/hide. */
    hiddenAll,
    dismissAll,
    dismiss,
    restoreAll,
    undoDismiss,
    load,
    refresh: refreshViaRef,
    enable,
    disable,
  };
}

export default useHomeSuggestions;
