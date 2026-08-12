'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import {
  attemptPageLoadRefresh,
  clearAuthState,
  clearLocalAuthState,
  getAccessToken,
  getAuthHeader,
  hasValidAccessToken,
  listenForAuthEvents,
  logoutCurrentSession,
  refreshAccessToken,
  setAccessToken,
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

function resolveLandingTarget(mode: AuthConfig['mode'] | 'unknown'): string {
  if (mode === 'hosted') return '/auth';
  return '/setup';
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [accessToken, setAccessTokenState] = useState<string | null>(null);
  const [mode, setMode] = useState<AuthConfig['mode'] | 'unknown' | 'error'>(
    () => {
      const cached = getCachedAuthConfig();
      return cached ? cached.mode : 'unknown';
    },
  );

  const updateAuthState = useCallback(() => {
    const token = getAccessToken();
    const valid = hasValidAccessToken();
    setIsAuthenticated(valid);
    setAccessTokenState(token);
  }, []);

  useEffect(() => {
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
      const target =
        resolvedMode === 'error'
          ? '/setup'
          : resolveLandingTarget(resolvedMode);
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

      const success = await attemptPageLoadRefresh({
        redirectOnExpiredSession: false,
      });
      if (!mounted) return;
      updateAuthState();

      if (success) return;

      const result = await fetchAuthConfig();
      if (!mounted) return;
      const resolvedMode =
        result.status === 'resolved' ? result.config.mode : 'error';
      decideAndRedirect(false, resolvedMode);
    }

    void init();

    const unsubscribe = listenForAuthEvents((event) => {
      if (!mounted) return;
      if (event === 'cleared') {
        clearLocalAuthState();
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
      unsubscribeConfig();
    };
  }, [updateAuthState, mode]);

  const refreshAuth = useCallback(async () => {
    const result = await refreshAccessToken();
    updateAuthState();
    return result;
  }, [updateAuthState]);

  const logout = useCallback(async () => {
    try {
      const result = await logoutCurrentSession();
      if (!result.success) {
        console.warn(result.error || 'Logout request failed');
      }
    } finally {
      clearAuthState();
      updateAuthState();
      const target = resolveLandingTarget(mode === 'error' ? 'unknown' : mode);
      if (typeof window !== 'undefined') {
        window.location.href = target;
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

  return (
    <AuthContext.Provider
      value={{
        isAuthenticated,
        accessToken,
        authHeader,
        refreshAuth,
        logout,
        setAccessToken: setToken,
      }}
    >
      {children}
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
