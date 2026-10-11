import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AuthProvider } from '../components/AuthProvider';
import * as auth from '../lib/auth';
import { AUTH_COORDINATION_KEY } from '../lib/authCookieCoordinator';
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

// A fresh module lifetime with genuinely absent credentials/metadata. No module
// or generation reset manufactures retry eligibility or clears the latched fence.
it('an exhausted startup race offers explicit recovery without a dead Retry or another transaction', async () => {
  vi.stubGlobal('localStorage', createAuthTestStorage());
  vi.stubGlobal('sessionStorage', createAuthTestStorage());
  vi.stubGlobal('isSecureContext', true);
  const locks = new AuthCookieTestLocks();
  locks.held = true;
  vi.stubGlobal('navigator', { locks });
  const originalRequest = locks.request;
  let admissions = 0;
  const request = vi.fn((...args: Parameters<typeof originalRequest>) => {
    admissions += 1;
    if (admissions === 2) {
      localStorage.setItem(
        AUTH_COORDINATION_KEY,
        JSON.stringify({ version: 1, epoch: 'second-peer', status: 'stable' }),
      );
    }
    return originalRequest(...args);
  });
  vi.stubGlobal('navigator', { locks: { request } });
  const fetchMock = vi.fn((input: unknown) => {
    if (!String(input).endsWith('/cache/clear'))
      throw new Error(`Unexpected fictional request ${String(input)}`);
    return Promise.resolve(
      new Response(null, {
        status: 204,
        headers: { 'cache-control': 'no-store' },
      }),
    );
  });
  vi.stubGlobal('fetch', fetchMock);

  const startup = auth.refreshAccessToken();
  expect(locks.queue).toHaveLength(1);
  localStorage.setItem(
    AUTH_COORDINATION_KEY,
    JSON.stringify({ version: 1, epoch: 'first-peer', status: 'stable' }),
  );
  locks.held = false;
  locks.drain();
  expect(await startup).toMatchObject({
    success: false,
    preserveProtectedUrl: true,
  });
  expect(request).toHaveBeenCalledTimes(2);
  expect(fetchMock).not.toHaveBeenCalled();

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
  expect(screen.queryByTestId('auth-cache-gate-retry')).toBeNull();
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Sign out locally' })).toBeTruthy();
  expect(screen.queryByTestId('protected-view')).toBeNull();
  expect(auth.getAuthHeader()).toBeNull();
  expect(request).toHaveBeenCalledTimes(2);
  expect(
    fetchMock.mock.calls.every(([url]) => String(url).endsWith('/cache/clear')),
  ).toBe(true);
  expect(locks.held).toBe(false);
  expect(locks.queue).toHaveLength(0);
});
