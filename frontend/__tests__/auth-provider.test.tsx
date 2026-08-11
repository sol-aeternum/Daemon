import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, waitFor } from '@testing-library/react';
import { useEffect } from 'react';

type ResolvedAuthConfig = {
  mode: 'hosted' | 'self_hosted';
  email: { enabled: boolean };
  google: { enabled: boolean; clientId: string };
};

const mockAuthConfig = vi.hoisted(() => ({
  state: { status: 'loading' as 'loading' | 'resolved' | 'error' },
  resolved: undefined as ResolvedAuthConfig | undefined,
  trigger: undefined as
    | ((next: ResolvedAuthConfig | undefined) => void)
    | undefined,
  inflight: undefined as Promise<unknown> | undefined,
  fetchCalls: 0,
  refreshCalls: 0,
  listeners: new Set<(s: unknown) => void>(),
}));

function fireSubscribers(result: unknown): void {
  for (const cb of mockAuthConfig.listeners) cb(result);
}

function makeFetchMock(): () => Promise<unknown> {
  return () => {
    mockAuthConfig.fetchCalls += 1;
    if (mockAuthConfig.state.status === 'resolved') {
      return Promise.resolve({
        status: 'resolved' as const,
        config: mockAuthConfig.resolved,
      });
    }
    if (mockAuthConfig.inflight) return mockAuthConfig.inflight;
    mockAuthConfig.inflight = new Promise((resolve) => {
      mockAuthConfig.trigger = (next) => {
        if (next === undefined) {
          mockAuthConfig.state = { status: 'error' };
          resolve({ status: 'error' as const });
        } else {
          mockAuthConfig.state = { status: 'resolved' };
          mockAuthConfig.resolved = next;
          resolve({ status: 'resolved' as const, config: next });
        }
      };
    });
    return mockAuthConfig.inflight;
  };
}

function makeRefreshMock(): () => Promise<unknown> {
  const fetchConfig = makeFetchMock();
  return () => {
    mockAuthConfig.refreshCalls += 1;
    return fetchConfig();
  };
}

vi.mock('../lib/auth-config', () => ({
  AUTH_CONFIG_CACHE_TTL_MS: 60_000,
  fetchAuthConfig: vi.fn(makeFetchMock()),
  refreshAuthConfig: vi.fn(makeRefreshMock()),
  subscribeAuthConfig: vi.fn((cb: (s: unknown) => void) => {
    mockAuthConfig.listeners.add(cb);
    return () => {
      mockAuthConfig.listeners.delete(cb);
    };
  }),
  getCachedAuthConfig: vi.fn(() => {
    if (mockAuthConfig.state.status === 'resolved') {
      return mockAuthConfig.resolved;
    }
    return undefined;
  }),
}));

const mockRefresh = vi.hoisted(() => vi.fn(async () => false));
const mockClearAuthState = vi.hoisted(() => vi.fn());
const mockClearLocalAuthState = vi.hoisted(() => vi.fn());
const mockLogoutCurrentSession = vi.hoisted(() =>
  vi.fn(async () => ({ success: true })),
);

vi.mock('../lib/auth', () => ({
  attemptPageLoadRefresh: mockRefresh,
  refreshAccessToken: vi.fn(async () => ({ success: false })),
  logoutCurrentSession: mockLogoutCurrentSession,
  clearAuthState: mockClearAuthState,
  clearLocalAuthState: mockClearLocalAuthState,
  getAccessToken: vi.fn(() => null),
  hasValidAccessToken: vi.fn(() => false),
  getAuthHeader: vi.fn(() => null),
  listenForAuthEvents: vi.fn(() => () => {}),
  setAccessToken: vi.fn(),
}));

import { AuthProvider, useAuth } from '../components/AuthProvider';

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  mockAuthConfig.state = { status: 'loading' };
  mockAuthConfig.resolved = undefined;
  mockAuthConfig.trigger = undefined;
  mockAuthConfig.inflight = undefined;
  mockAuthConfig.fetchCalls = 0;
  mockAuthConfig.refreshCalls = 0;
  mockAuthConfig.listeners.clear();
  mockRefresh.mockClear();
  mockClearAuthState.mockClear();
  mockClearLocalAuthState.mockClear();
  mockLogoutCurrentSession.mockClear();
  delete (window as { location?: unknown }).location;
});

function setLocation(pathname: string): void {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { pathname, href: '' },
  });
}

function setupHrefSpy(pathname: string = '/'): {
  hrefSetter: ReturnType<typeof vi.fn>;
} {
  const hrefSetter = vi.fn();
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      pathname,
      get href() {
        return '';
      },
      set href(_v: string) {
        hrefSetter(_v);
      },
    },
  });
  return { hrefSetter };
}

function renderProvider(): void {
  render(
    <AuthProvider>
      <Consumer />
    </AuthProvider>,
  );
}

function Consumer(): null {
  const auth = useAuth();
  useEffect(() => {
    void auth;
  }, [auth]);
  return null;
}

function resolveConfig(next: ResolvedAuthConfig | undefined): void {
  mockAuthConfig.trigger?.(next);
  if (next === undefined) {
    fireSubscribers({ status: 'error' });
  } else {
    fireSubscribers({ status: 'resolved', config: next });
  }
}

async function flush(): Promise<void> {
  await waitFor(() => {
    expect(mockAuthConfig.fetchCalls).toBeGreaterThanOrEqual(1);
  });
}

describe('AuthProvider mode-aware redirects', () => {
  beforeEach(() => {
    setLocation('/');
  });

  it('refreshes runtime auth config when the 60s cache TTL expires', async () => {
    vi.useFakeTimers();
    setLocation('/auth');
    mockAuthConfig.state = { status: 'resolved' };
    mockAuthConfig.resolved = {
      mode: 'hosted',
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    };

    renderProvider();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(59_999);
    });
    expect(mockAuthConfig.fetchCalls).toBe(0);
    expect(mockAuthConfig.refreshCalls).toBe(0);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(mockAuthConfig.refreshCalls).toBe(1);
    expect(mockAuthConfig.fetchCalls).toBe(1);
  });

  it('does not redirect while authConfig is still loading', async () => {
    const { hrefSetter } = setupHrefSpy();
    mockRefresh.mockResolvedValue(false);
    renderProvider();
    await flush();
    expect(hrefSetter).not.toHaveBeenCalled();
  });

  it('redirects to /auth in hosted mode when refresh fails', async () => {
    const { hrefSetter } = setupHrefSpy();
    mockRefresh.mockResolvedValue(false);
    renderProvider();
    await flush();
    expect(mockRefresh).toHaveBeenCalledWith({
      redirectOnExpiredSession: false,
    });
    resolveConfig({
      mode: 'hosted',
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    });
    await waitFor(() => {
      expect(hrefSetter).toHaveBeenCalledWith('/auth');
    });
  });

  it('redirects to /setup in self-hosted mode when refresh fails', async () => {
    const { hrefSetter } = setupHrefSpy();
    mockRefresh.mockResolvedValue(false);
    renderProvider();
    await flush();
    resolveConfig({
      mode: 'self_hosted',
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    });
    await waitFor(() => {
      expect(hrefSetter).toHaveBeenCalledWith('/setup');
    });
  });

  it('falls back to /setup when authConfig fetch errors', async () => {
    const { hrefSetter } = setupHrefSpy();
    mockRefresh.mockResolvedValue(false);
    renderProvider();
    await flush();
    resolveConfig(undefined);
    await waitFor(() => {
      expect(hrefSetter).toHaveBeenCalledWith('/setup');
    });
  });

  it('does not redirect when refresh succeeds', async () => {
    const { hrefSetter } = setupHrefSpy();
    mockRefresh.mockResolvedValue(true);
    renderProvider();
    await flush();
    resolveConfig({
      mode: 'hosted',
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    });
    await new Promise((r) => setTimeout(r, 10));
    expect(hrefSetter).not.toHaveBeenCalled();
  });

  it('does not redirect when current path is /auth even if refresh fails', async () => {
    setLocation('/auth');
    const { hrefSetter } = setupHrefSpy('/auth');
    mockRefresh.mockResolvedValue(false);
    renderProvider();
    await flush();
    resolveConfig({
      mode: 'hosted',
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    });
    await new Promise((r) => setTimeout(r, 10));
    expect(hrefSetter).not.toHaveBeenCalled();
  });

  it('does not redirect when current path is /setup even if refresh fails', async () => {
    setLocation('/setup');
    const { hrefSetter } = setupHrefSpy('/setup');
    mockRefresh.mockResolvedValue(false);
    renderProvider();
    await flush();
    resolveConfig({
      mode: 'hosted',
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    });
    await new Promise((r) => setTimeout(r, 10));
    expect(hrefSetter).not.toHaveBeenCalled();
  });

  it('logout() in hosted mode revokes the backend session before navigating to /auth', async () => {
    const { hrefSetter } = setupHrefSpy();
    mockRefresh.mockResolvedValue(true);
    renderProvider();
    await flush();
    resolveConfig({
      mode: 'hosted',
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    });

    let captured: { logout: () => Promise<void> } | null = null;
    const Probe = (): null => {
      const auth = useAuth();
      useEffect(() => {
        captured = { logout: auth.logout };
      }, [auth]);
      return null;
    };
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(captured).not.toBeNull();
    });
    await captured!.logout();
    expect(mockLogoutCurrentSession).toHaveBeenCalled();
    expect(mockClearAuthState).toHaveBeenCalled();
    expect(hrefSetter).toHaveBeenCalledWith('/auth');
  });

  it('logout() in self-hosted mode navigates to /setup', async () => {
    const { hrefSetter } = setupHrefSpy();
    mockRefresh.mockResolvedValue(true);
    renderProvider();
    await flush();
    resolveConfig({
      mode: 'self_hosted',
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    });

    let captured: { logout: () => Promise<void> } | null = null;
    const Probe = (): null => {
      const auth = useAuth();
      useEffect(() => {
        captured = { logout: auth.logout };
      }, [auth]);
      return null;
    };
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(captured).not.toBeNull();
    });
    await captured!.logout();
    expect(mockLogoutCurrentSession).toHaveBeenCalled();
    expect(hrefSetter).toHaveBeenCalledWith('/setup');
  });

  it('logout() waits for backend revoke before clearing state and redirecting', async () => {
    const { hrefSetter } = setupHrefSpy();
    let resolveLogout: (value: { success: boolean }) => void = () => {};
    mockLogoutCurrentSession.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveLogout = resolve;
      }),
    );
    mockRefresh.mockResolvedValue(true);
    renderProvider();
    await flush();
    resolveConfig({
      mode: 'hosted',
      email: { enabled: true },
      google: { enabled: false, clientId: '' },
    });

    let captured: { logout: () => Promise<void> } | null = null;
    const Probe = (): null => {
      const auth = useAuth();
      useEffect(() => {
        captured = { logout: auth.logout };
      }, [auth]);
      return null;
    };
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(captured).not.toBeNull();
    });

    const logoutPromise = captured!.logout();
    await Promise.resolve();

    expect(mockLogoutCurrentSession).toHaveBeenCalled();
    expect(mockClearAuthState).not.toHaveBeenCalled();
    expect(hrefSetter).not.toHaveBeenCalled();

    resolveLogout({ success: true });
    await logoutPromise;

    expect(mockClearAuthState).toHaveBeenCalled();
    expect(hrefSetter).toHaveBeenCalledWith('/auth');
  });
});
