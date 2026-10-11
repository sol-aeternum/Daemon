'use client';

import { subscribeAuthCoordination } from '@/lib/authCookieCoordinator';
import { usePathname } from 'next/navigation';
import {
  DeviceActionOwnerContext,
  PublicCredentialOwnerContext,
  useOwnedDeviceAction,
} from './AuthActionOwners';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { Loader2, LogOut, ShieldAlert } from 'lucide-react';
import {
  ensureBrowserCacheCleared,
  getBrowserCacheState,
  startBrowserCacheClearAttempt,
  subscribeBrowserCacheState,
  type BrowserCacheClearStatus,
} from '@/lib/browserCache';
import {
  clearAuthState,
  getAccessToken,
  getAuthGeneration,
  getAuthHeader,
  getSessionCoordinationProblem,
  hasValidAccessToken,
  isAuthResultCurrent,
  listenForAuthEvents,
  logoutCurrentSession,
  refreshAccessToken,
  setAccessToken,
  subscribeAuthGeneration,
  type RefreshResult,
} from '@/lib/auth';
import {
  AUTH_CONFIG_CACHE_TTL_MS,
  fetchAuthConfig,
  getCachedAuthConfig,
  getCachedAuthConfigAgeMs,
  refreshAuthConfig,
  subscribeAuthConfig,
  type AuthConfig,
  type AuthConfigResult,
} from '@/lib/auth-config';
import { clearLegacyPiiStorage } from '@/lib/privacy-storage';

interface AuthContextValue {
  isAuthenticated: boolean;
  accessToken: string | null;
  authHeader: string | null;
  refreshAuth: () => Promise<RefreshResult>;
  logout: () => Promise<void>;
  setAccessToken: (token: string, expiresAtMs: number) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

interface AuthProviderProps {
  children: ReactNode;
}

const PUBLIC_AUTH_PATHS = new Set(['/setup', '/auth']);

function resolveLandingTarget(
  mode: AuthConfig['mode'] | 'unknown' | 'error',
): string {
  if (mode === 'self_hosted') return '/setup';
  return '/auth';
}

interface CacheGateProps {
  status: BrowserCacheClearStatus;
  error: string | null;
  onRetry: () => void;
  onSignOut: () => void;
  sessionProblem?: boolean;
}

/**
 * Startup/failure gate. Protected children never mount before the trusted
 * endpoint's cache-clear acknowledgement for the current auth lifetime; a
 * failed attempt stays fail-closed with Retry and local Sign out available.
 * Wording is deliberately an acknowledgement/readiness claim under the
 * qualified browser contract — never a universal cache-erasure guarantee.
 */
function AuthCacheGate({
  status,
  error,
  onRetry,
  onSignOut,
  sessionProblem = false,
}: CacheGateProps) {
  const failed = status === 'failed';
  return (
    <div
      data-testid="auth-cache-gate"
      role={failed ? 'alert' : 'status'}
      aria-live="polite"
      className="flex min-h-screen flex-col items-center justify-center gap-3 px-4 text-center"
    >
      {failed ? (
        <ShieldAlert className="h-6 w-6" aria-hidden="true" />
      ) : (
        <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
      )}
      <p className="text-sm text-[var(--color-text-muted)]">
        {sessionProblem
          ? 'Protected content stays unavailable until safe session coordination succeeds.'
          : failed
            ? 'The browser cache clear could not be acknowledged. Protected content stays unavailable until it succeeds.'
            : 'Finishing session setup.'}
      </p>
      {failed && error ? (
        <p className="text-xs text-[var(--color-text-muted)]">{error}</p>
      ) : null}
      <div className="flex items-center gap-2">
        {failed ? (
          <button
            type="button"
            data-testid="auth-cache-gate-retry"
            onClick={onRetry}
            className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-sm"
          >
            Retry
          </button>
        ) : null}
        <button
          type="button"
          data-testid="auth-cache-gate-signout"
          onClick={onSignOut}
          className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-sm"
        >
          Sign out locally
        </button>
      </div>
      <p className="text-xs text-[var(--color-text-muted)]">
        Local sign-out does not confirm server session revocation.
      </p>
    </div>
  );
}

// Readiness changes hide/reopen protected content without destroying the
// current sign-in lifetime. Local-only auth changes still arrive via generation.
function subscribeAuthState(listener: () => void): () => void {
  const unsubscribeGeneration = subscribeAuthGeneration(listener);
  const unsubscribeCoordination = subscribeAuthCoordination(listener);
  return () => {
    unsubscribeCoordination();
    unsubscribeGeneration();
  };
}

export function AuthProvider({ children }: AuthProviderProps) {
  const pathname =
    usePathname() ??
    (typeof window !== 'undefined' ? (window.location?.pathname ?? '') : '');
  const publicPath = PUBLIC_AUTH_PATHS.has(pathname);
  const [credentialOwnerPath, setCredentialOwnerPath] = useState<string | null>(
    null,
  );
  const registerCredentialOwner = useCallback(() => {
    if (!PUBLIC_AUTH_PATHS.has(pathname)) return () => {};
    setCredentialOwnerPath(pathname);
    return () => setCredentialOwnerPath(null);
  }, [pathname]);
  const deviceAction = useOwnedDeviceAction(pathname);
  const cancelDeviceAction = deviceAction.cancel;
  const previousPath = useRef(pathname);
  useEffect(() => {
    if (previousPath.current !== pathname) {
      previousPath.current = pathname;
      cancelDeviceAction();
    }
  }, [pathname, cancelDeviceAction]);
  const [signingOutGeneration, setSigningOutGeneration] = useState<
    number | null
  >(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [accessToken, setAccessTokenState] = useState<string | null>(null);
  const [mode, setMode] = useState<AuthConfig['mode'] | 'unknown' | 'error'>(
    () => {
      const cached = getCachedAuthConfig();
      return cached ? cached.mode : 'unknown';
    },
  );
  const cacheState = useSyncExternalStore(
    subscribeBrowserCacheState,
    getBrowserCacheState,
    getBrowserCacheState,
  );
  const coordinationProblem = useSyncExternalStore(
    subscribeAuthState,
    getSessionCoordinationProblem,
    () => null,
  );

  const updateAuthState = useCallback(() => {
    const token = getAccessToken();
    const valid = hasValidAccessToken();
    setIsAuthenticated(valid);
    setAccessTokenState(token);
  }, []);

  useEffect(() => {
    clearLegacyPiiStorage();
    let mounted = true;

    function applyConfig(result: AuthConfigResult): void {
      if (!mounted) return;
      if (result.status === 'resolved') {
        setMode(result.config.mode);
      } else {
        setMode('error');
      }
    }

    const cached = getCachedAuthConfig();
    if (!cached) {
      void fetchAuthConfig().then(applyConfig);
    }
    const unsubscribeConfig = subscribeAuthConfig(applyConfig);
    // Align the first refresh with the cached response's remaining TTL so a
    // remount with a 59-second-old cache refreshes within 1s instead of up
    // to 119s (Codex P2 on AuthProvider.tsx). Re-schedule after each refresh
    // so a cache populated by another mount path also aligns.
    let refreshConfigTimer: ReturnType<typeof setTimeout> | null = null;
    const scheduleNextConfigRefresh = (): void => {
      if (!mounted) return;
      const freshCached = getCachedAuthConfig();
      const ageMs = freshCached ? getCachedAuthConfigAgeMs() : undefined;
      const delay =
        ageMs !== undefined
          ? Math.max(0, AUTH_CONFIG_CACHE_TTL_MS - ageMs)
          : AUTH_CONFIG_CACHE_TTL_MS;
      refreshConfigTimer = setTimeout(() => {
        void refreshAuthConfig().finally(() => {
          scheduleNextConfigRefresh();
        });
      }, delay);
    };
    scheduleNextConfigRefresh();

    function doRedirect(target: string): void {
      if (typeof window !== 'undefined') {
        window.location.href = target;
      }
    }

    function decideAndRedirect(
      refreshSucceeded: boolean,
      resolvedMode: AuthConfig['mode'] | 'unknown' | 'error',
    ): void {
      if (refreshSucceeded) return;
      if (resolvedMode === 'unknown') return;
      if (typeof window === 'undefined') return;
      if (PUBLIC_AUTH_PATHS.has(window.location.pathname)) return;
      const target = resolveLandingTarget(resolvedMode);
      doRedirect(target);
    }

    async function init() {
      if (
        typeof window !== 'undefined' &&
        PUBLIC_AUTH_PATHS.has(window.location.pathname)
      ) {
        updateAuthState();
        return;
      }

      const refresh = await refreshAccessToken();
      if (!mounted || !isAuthResultCurrent(refresh)) return;
      const generation = getAuthGeneration();
      updateAuthState();

      if (refresh.success) return;

      const result = await fetchAuthConfig();
      if (!mounted || generation !== getAuthGeneration()) return;
      const resolvedMode =
        result.status === 'resolved' ? result.config.mode : 'error';
      decideAndRedirect(false, resolvedMode);
    }

    void init();

    // Reflect every module-level auth mutation in the provider state —
    // including low-level auth.setAccessToken()/clearAuthState() calls made
    // directly by auth helpers or test/fixture harnesses, not only flows that
    // pass through the useAuth() wrapper. The module notifies this listener
    // only AFTER the cache barrier has been invalidated, so the gate re-arms
    // before this state update is visible.
    const unsubscribeGeneration = subscribeAuthState(updateAuthState);

    const unsubscribe = listenForAuthEvents((event) => {
      if (!mounted) return;
      if (event === 'cleared') {
        // The module already applied the scoped advisory event exactly once.
        updateAuthState();
      } else if (event === 'refreshed') {
        if (!hasValidAccessToken()) {
          void refreshAccessToken().then(() => {
            if (mounted) updateAuthState();
          });
        } else {
          updateAuthState();
        }
      }
    });

    return () => {
      mounted = false;
      if (refreshConfigTimer !== null) {
        clearTimeout(refreshConfigTimer);
        refreshConfigTimer = null;
      }
      unsubscribe();
      unsubscribeGeneration();
      unsubscribeConfig();
    };
  }, [updateAuthState, mode]);

  // Barrier driver: 'idle' means the current auth lifetime has no cache-clear
  // attempt yet (fresh boot or a lifecycle invalidation). Protected children
  // stay unmounted while the barrier is not acknowledged; a failed attempt is
  // surfaced by the gate UI with Retry available, never auto-looped.
  useEffect(() => {
    if (cacheState.status !== 'idle') return;
    void ensureBrowserCacheCleared({
      reason: cacheState.generation === 1 ? 'initialization' : 'revalidate',
    }).catch(() => {
      // Failure is exposed via the barrier subscription (the gate renders
      // it); never silently accepted as cleared.
    });
  }, [cacheState.status, cacheState.generation]);

  const retryCacheClear = useCallback(() => {
    void startBrowserCacheClearAttempt({ reason: 'retry' });
  }, []);

  // Local sign-out from the gate: the (bounded) cache-clear attempt is
  // retried opportunistically in parallel, but local sign-out NEVER waits on
  // the network and is never blocked by a clear failure. The invalidation in
  // clearAuthState() also prevents any in-flight attempt completion from
  // releasing the newer auth lifetime.
  const signOutFromGate = useCallback(() => {
    cancelDeviceAction();
    void logoutCurrentSession({ allowRefresh: false });
    clearAuthState();
    void startBrowserCacheClearAttempt({ reason: 'sign-out' });
    updateAuthState();
    const target = resolveLandingTarget(mode === 'error' ? 'unknown' : mode);
    if (typeof window !== 'undefined') {
      window.location.href = target;
    }
  }, [updateAuthState, mode, cancelDeviceAction]);

  const refreshAuth = useCallback(async () => {
    const result = await refreshAccessToken();
    updateAuthState();
    return result;
  }, [updateAuthState]);

  const logout = useCallback(async () => {
    // Preserve the old lifetime long enough for bounded refresh-and-revoke.
    // A separate gate stays closed even when parallel cache cleanup completes.
    const generation = getAuthGeneration();
    setSigningOutGeneration(generation);
    const revocation = logoutCurrentSession();
    void startBrowserCacheClearAttempt({
      reason: 'sign-out',
    });
    let result: RefreshResult | undefined;
    try {
      result = await revocation;
      if (!result.success) {
        console.warn(result.error || 'Logout request failed');
      }
    } finally {
      // A local-only escape, remote sign-out or new sign-in supersedes this
      // operation. Never clear or redirect that newer lifetime.
      if (
        result
          ? isAuthResultCurrent(result)
          : generation === getAuthGeneration()
      ) {
        clearAuthState();
        updateAuthState();
        setSigningOutGeneration(null);
        const target = resolveLandingTarget(
          mode === 'error' ? 'unknown' : mode,
        );
        if (typeof window !== 'undefined') {
          window.location.href = target;
        }
      }
    }
  }, [updateAuthState, mode]);

  const setToken = useCallback(
    (token: string, expiresAtMs: number) => {
      setAccessToken(token, expiresAtMs);
      updateAuthState();
    },
    [updateAuthState],
  );

  const authHeader = getAuthHeader();

  // Full-children gate, not just isAuthenticated: nothing mounts until the
  // current auth lifetime's cache-clear acknowledgement (or the qualified
  // gate's failure UI with Retry and local Sign out).
  let gatedChildren = children;
  if (
    signingOutGeneration !== null &&
    signingOutGeneration === getAuthGeneration()
  ) {
    gatedChildren = (
      <AuthCacheGate
        status="pending"
        error={null}
        onRetry={retryCacheClear}
        onSignOut={signOutFromGate}
      />
    );
  } else if (cacheState.status === 'failed') {
    gatedChildren = (
      <AuthCacheGate
        status="failed"
        error={cacheState.error}
        onRetry={retryCacheClear}
        onSignOut={signOutFromGate}
      />
    );
  } else if (cacheState.status !== 'ready') {
    gatedChildren = (
      <AuthCacheGate
        status={cacheState.status}
        error={null}
        onRetry={retryCacheClear}
        onSignOut={signOutFromGate}
      />
    );
  } else if (coordinationProblem && !publicPath) {
    gatedChildren = (
      <AuthCacheGate
        status={
          coordinationProblem === 'Finishing session coordination.'
            ? 'pending'
            : 'failed'
        }
        error={coordinationProblem}
        onRetry={() => {
          void refreshAuth();
        }}
        onSignOut={signOutFromGate}
        sessionProblem
      />
    );
  }

  const privacyGated = gatedChildren !== children;
  const releaseDeviceView = useCallback(() => {
    // A route/tab exit with no privacy gate still cancels. Temporary cache or
    // coordination replacement must not abort the action which closed it.
    if (
      getBrowserCacheState().status === 'ready' &&
      !getSessionCoordinationProblem()
    )
      cancelDeviceAction();
  }, [cancelDeviceAction]);
  const preserveCredentialOwner =
    publicPath &&
    credentialOwnerPath === pathname &&
    signingOutGeneration === null;
  const protectedReady =
    cacheState.status === 'ready' && !coordinationProblem && !privacyGated;

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated: protectedReady && isAuthenticated,
        accessToken: protectedReady ? accessToken : null,
        authHeader: protectedReady ? authHeader : null,
        refreshAuth,
        logout,
        setAccessToken: setToken,
      }}
    >
      <PublicCredentialOwnerContext.Provider value={registerCredentialOwner}>
        <DeviceActionOwnerContext.Provider
          value={{ ...deviceAction, releaseView: releaseDeviceView }}
        >
          {preserveCredentialOwner ? children : gatedChildren}
          {preserveCredentialOwner && privacyGated ? gatedChildren : null}
        </DeviceActionOwnerContext.Provider>
      </PublicCredentialOwnerContext.Provider>
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
