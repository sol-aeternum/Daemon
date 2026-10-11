import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AuthProvider } from '../components/AuthProvider';
import * as auth from '../lib/auth';
import { createAuthTestStorage } from './helpers/authCookieEnvironment';
import { AuthCookieTestLocks } from './helpers/authCookieLocks';

vi.mock('next/navigation', () => ({
  usePathname: () => '/settings/devices',
}));
vi.mock('../lib/auth-config', () => {
  const config = {
    mode: 'hosted',
    email: { enabled: true },
    google: { enabled: false },
  };
  return {
    AUTH_CONFIG_CACHE_TTL_MS: 60_000,
    getCachedAuthConfig: () => config,
    getCachedAuthConfigAgeMs: () => 0,
    fetchAuthConfig: async () => ({ status: 'resolved', config }),
    refreshAuthConfig: async () => ({ status: 'resolved', config }),
    subscribeAuthConfig: () => () => {},
  };
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// Fresh module lifetime, real auth/coordinator/provider; no peer mutation or
// module/generation reset. A thrown transport failure is not an authoritative 401.
it('thrown unbound transport failure preserves the URL and offers explicit recovery without another refresh', async () => {
  vi.stubGlobal('localStorage', createAuthTestStorage());
  vi.stubGlobal('sessionStorage', createAuthTestStorage());
  vi.stubGlobal('isSecureContext', true);
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      pathname: '/settings/devices',
      origin: 'http://localhost:3000',
      href: 'http://localhost:3000/settings/devices?from=deep-link',
    },
  });
  const locks = new AuthCookieTestLocks();
  vi.stubGlobal('navigator', { locks });
  const fetchMock = vi.fn((input: unknown) => {
    if (String(input).endsWith('/refresh'))
      return Promise.reject(new TypeError('Fictional network failure'));
    if (String(input).endsWith('/cache/clear'))
      return Promise.resolve(
        new Response(null, {
          status: 204,
          headers: { 'cache-control': 'no-store' },
        }),
      );
    throw new Error(`Unexpected fictional request ${String(input)}`);
  });
  vi.stubGlobal('fetch', fetchMock);
  const result = await auth.refreshAccessToken();
  expect(result).toMatchObject({
    success: false,
    error: 'Fictional network failure',
    preserveProtectedUrl: true,
  });
  expect(locks.request).toHaveBeenCalledTimes(1);

  await act(async () => {
    render(
      <AuthProvider>
        <div data-testid="protected-view">Protected content</div>
      </AuthProvider>,
    );
  });
  await waitFor(() =>
    expect(screen.getByText(/Session startup changed\./)).toBeTruthy(),
  );
  expect(window.location.href).toBe(
    'http://localhost:3000/settings/devices?from=deep-link',
  );
  expect(screen.queryByTestId('protected-view')).toBeNull();
  expect(screen.queryByTestId('auth-cache-gate-retry')).toBeNull();
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Sign out locally' })).toBeTruthy();
  expect(auth.getAuthHeader()).toBeNull();
  expect(locks.request).toHaveBeenCalledTimes(1);
  expect(
    fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/refresh')),
  ).toHaveLength(1);
  expect(locks.held).toBe(false);
  expect(locks.queue).toHaveLength(0);
});
