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
  refreshAccessToken,
  setAccessToken,
  type RefreshResult,
} from '@/lib/auth';

interface AuthContextValue {
  isAuthenticated: boolean;
  accessToken: string | null;
  authHeader: string | null;
  refreshAuth: () => Promise<RefreshResult>;
  logout: () => void;
  setAccessToken: (token: string, expiresAtMs: number) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [accessToken, setAccessTokenState] = useState<string | null>(null);

  const updateAuthState = useCallback(() => {
    const token = getAccessToken();
    const valid = hasValidAccessToken();
    setIsAuthenticated(valid);
    setAccessTokenState(token);
  }, []);

  useEffect(() => {
    let mounted = true;

    async function init() {
      if (
        typeof window !== 'undefined' &&
        window.location.pathname === '/setup'
      ) {
        updateAuthState();
        return;
      }

      const success = await attemptPageLoadRefresh();
      if (!mounted) return;
      updateAuthState();

      if (!success) {
        if (typeof window !== 'undefined') {
          window.location.href = '/setup';
        }
        return;
      }
    }

    init();

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
      unsubscribe();
    };
  }, [updateAuthState]);

  const refreshAuth = useCallback(async () => {
    const result = await refreshAccessToken();
    updateAuthState();
    return result;
  }, [updateAuthState]);

  const logout = useCallback(() => {
    clearAuthState();
    updateAuthState();
    if (typeof window !== 'undefined') {
      window.location.href = '/setup';
    }
  }, [updateAuthState]);

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
