import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useEffect } from 'react';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { AuthCookieTestLocks } from './helpers/authCookieLocks';
import { createAuthTestStorage } from './helpers/authCookieEnvironment';
import {
  AUTH_COORDINATION_KEY,
  readAuthCoordination,
} from '../lib/authCookieCoordinator';
import {
  clearLocalAuthState,
  LOCAL_SIGNOUT_KEY,
  getAccessToken,
  getAuthHeader,
  getRefreshPromise,
  setAccessToken,
} from '../lib/auth';
import {
  getBrowserCacheState,
  invalidateBrowserCache,
} from '../lib/browserCache';

const navigation = vi.hoisted(() => ({ path: '/auth', push: vi.fn() }));
vi.mock('next/navigation', () => ({
  usePathname: () => navigation.path,
  useRouter: () => ({ push: navigation.push }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('../lib/auth-config', () => {
  const config = {
    mode: 'hosted',
    email: { enabled: true },
    google: { enabled: true, clientId: 'fictional-client' },
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
import { AuthProvider, useAuth } from '../components/AuthProvider';
import AuthLanding from '../components/AuthLanding';
import DevicesTab from '../components/settings/DevicesTab';

const pendingResponses: Array<() => void> = [];
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  pendingResponses.push(() => resolve(ack() as T));
  return { promise, resolve };
}
const ack = () =>
  new Response(null, { status: 204, headers: { 'cache-control': 'no-store' } });
const identity = () =>
  new Response(
    JSON.stringify({
      access_token: 'fictional-b',
      expires_at: Date.now() / 1000 + 1800,
      expires_in: 1800,
    }),
    { status: 200 },
  );
const device = {
  id: 'other',
  display_name: 'Fictional other device',
  platform: 'web',
  created_at: '2026-10-01T00:00:00Z',
  current: false,
  revoked: false,
};
let locks: AuthCookieTestLocks;
let clearResponse: () => Promise<Response>;
let authResponse: () => Promise<Response>;
let deleteResponse: () => Promise<Response>;
let refreshResponse: () => Promise<Response>;
let calls: Array<{ url: string; init?: RequestInit }>;
let googleCallback: (response: { credential?: string }) => void;
let currentDevice = false;
const flush = async () => {
  for (let i = 0; i < 40; i++) await Promise.resolve();
};

function abortable(promise: Promise<Response>, signal?: AbortSignal | null) {
  return new Promise<Response>((resolve, reject) => {
    const abort = () =>
      reject(signal?.reason ?? new DOMException('Aborted', 'AbortError'));
    if (signal?.aborted) {
      abort();
      return;
    }
    signal?.addEventListener('abort', abort, { once: true });
    void promise.then((response) => {
      signal?.removeEventListener('abort', abort);
      resolve(response);
    }, reject);
  });
}
function setPath(path: string) {
  navigation.path = path;
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      pathname: path,
      origin: 'http://localhost:3000',
      href: `http://localhost:3000${path}`,
      replace: vi.fn(),
    },
  });
}
beforeEach(() => {
  vi.stubGlobal('sessionStorage', createAuthTestStorage());
  navigation.push.mockReset();
  setPath('/auth');
  vi.stubEnv('NEXT_PUBLIC_API_URL', '');
  const store = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    get length() {
      return store.size;
    },
    key: (index: number) => [...store.keys()][index] ?? null,
  });
  locks = new AuthCookieTestLocks();
  vi.stubGlobal('navigator', { locks });
  vi.stubGlobal(
    'BroadcastChannel',
    class {
      postMessage() {}
      addEventListener() {}
      removeEventListener() {}
    },
  );
  Object.defineProperty(window, 'isSecureContext', {
    configurable: true,
    value: true,
  });
  window.google = {
    accounts: {
      id: {
        initialize: (config) => {
          googleCallback = config.callback;
        },
        renderButton: (host) => {
          const button = document.createElement('button');
          button.textContent = 'Fictional Google button';
          host.append(button);
        },
        cancel: vi.fn(),
      },
    },
  };
  clearResponse = async () => ack();
  authResponse = async () => identity();
  deleteResponse = async () => new Response(null, { status: 204 });
  refreshResponse = async () => identity();
  calls = [];
  currentDevice = false;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: unknown, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.endsWith('/cache/clear'))
        return abortable(clearResponse(), init?.signal);
      if (url.endsWith('/refresh'))
        return abortable(refreshResponse(), init?.signal);
      if (url.endsWith('/email/start'))
        return Promise.resolve(
          new Response(
            JSON.stringify({ challenge_id: 'fictional-challenge' }),
            { status: 202 },
          ),
        );
      if (url.endsWith('/google/start'))
        return Promise.resolve(
          new Response(
            JSON.stringify({
              challenge_id: 'fictional-google',
              nonce: 'fictional-nonce',
            }),
            { status: 202 },
          ),
        );
      if (
        url.endsWith('/email/complete') ||
        url.endsWith('/google/complete') ||
        url.endsWith('/setup') ||
        url.endsWith('/enroll/complete')
      )
        return abortable(authResponse(), init?.signal);
      if (url.endsWith('/devices/other'))
        return abortable(deleteResponse(), init?.signal);
      if (url.endsWith('/devices'))
        return Promise.resolve(
          new Response(
            JSON.stringify({
              devices: [{ ...device, current: currentDevice }],
            }),
            { status: 200 },
          ),
        );
      throw new Error(`Unexpected fictional request ${url}`);
    }),
  );
  localStorage.setItem(
    AUTH_COORDINATION_KEY,
    JSON.stringify({ version: 1, epoch: 'fictional-a', status: 'stable' }),
  );
  setAccessToken('fictional-a', Date.now() + 1800_000, 'fictional-a');
  // Public startup should display credential forms rather than resume A.
  clearLocalAuthState();
  invalidateBrowserCache();
});
afterEach(async () => {
  cleanup();
  clearLocalAuthState();
  pendingResponses.splice(0).forEach((resolve) => resolve());
  await flush();
  expect(locks.held).toBe(false);
  expect(locks.queue).toHaveLength(0);
  expect(getRefreshPromise()).toBeNull();
  delete window.google;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

const emailConfig = {
  email: { enabled: true },
  google: { enabled: false, clientId: '' },
};
function PublicRoute() {
  return <AuthLanding mode="hosted" runtimeConfig={emailConfig} />;
}
function ProtectedRoute() {
  const auth = useAuth();
  return (
    <div data-testid="protected-view">
      {auth.authHeader}
      <DevicesTab />
    </div>
  );
}
async function emailSubmit() {
  fireEvent.change(await screen.findByLabelText('Email Address'), {
    target: { value: 'fictional@example.invalid' },
  });
  fireEvent.click(
    screen.getByRole('button', { name: 'Send verification code' }),
  );
  fireEvent.change(await screen.findByLabelText('Verification Code'), {
    target: { value: '000000' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Verify and sign in' }));
}
async function confirmRevoke() {
  const label = await screen.findByText(device.display_name);
  const row = label.closest('div.min-w-0')!.parentElement!;
  fireEvent.click(within(row).getByRole('button', { name: 'Revoke' }));
  const modal = screen.getByRole('heading', { name: 'Revoke device?' })
    .parentElement!.parentElement!.parentElement!;
  fireEvent.click(within(modal).getByRole('button', { name: 'Revoke' }));
}

describe('real nested action owners across privacy gates', () => {
  it('authoritative refresh expiration still routes an ordinary protected startup to sign-in without retrying', async () => {
    setPath('/settings/deep-link');
    setAccessToken('fictional-a', Date.now() - 1000, 'fictional-a');
    refreshResponse = async () => new Response(null, { status: 401 });
    render(
      <AuthProvider>
        <ProtectedRoute />
      </AuthProvider>,
    );
    await waitFor(() => expect(window.location.href).toBe('/auth'));
    expect(calls.filter(({ url }) => url.endsWith('/refresh'))).toHaveLength(1);
    expect(getAccessToken()).toBeNull();
    expect(getAuthHeader()).toBeNull();
    expect(readAuthCoordination()?.status).toBe('signed-out');
    expect(sessionStorage.getItem(LOCAL_SIGNOUT_KEY)).toBeNull();
  });
  it('failed exit persistence cancels a real owned DELETE immediately, keeps the URL, and navigates only after verified retry', async () => {
    setPath('/settings/devices');
    setAccessToken('fictional-a', Date.now() + 1800_000, 'fictional-a');
    render(
      <AuthProvider>
        <ProtectedRoute />
      </AuthProvider>,
    );
    const deleted = deferred<Response>();
    deleteResponse = () => deleted.promise;
    await confirmRevoke();
    await act(flush);
    const call = calls.find(({ url }) => url.endsWith('/devices/other'))!;
    const nativeSetItem = sessionStorage.setItem;
    const blocked = vi
      .spyOn(sessionStorage, 'setItem')
      .mockImplementation((key, value) => {
        if (key === LOCAL_SIGNOUT_KEY)
          throw new Error('fictional session storage failure');
        nativeSetItem(key, value);
      });
    fireEvent.click(screen.getByTestId('auth-cache-gate-signout'));
    await act(flush);
    expect(call.init!.signal!.aborted).toBe(true);
    expect(getAccessToken()).toBeNull();
    expect(getAuthHeader()).toBeNull();
    expect(screen.queryByTestId('protected-view')).toBeNull();
    expect(window.location.href).toBe('http://localhost:3000/settings/devices');
    expect(screen.getByText(/Reloading may restore this session/)).toBeTruthy();
    blocked.mockRestore();
    fireEvent.click(screen.getByTestId('auth-cache-gate-retry'));
    await act(flush);
    expect(sessionStorage.getItem(LOCAL_SIGNOUT_KEY)).toBe('1');
    expect(window.location.href).toBe('/auth');
    deleted.resolve(ack());
  });

  it('verified sign-in waits through failed fence removal and explicit retry without resubmitting credentials', async () => {
    sessionStorage.setItem(LOCAL_SIGNOUT_KEY, 'corrupt');
    render(
      <AuthProvider>
        <PublicRoute />
      </AuthProvider>,
    );
    await screen.findByText('Welcome to Daemon');
    const nativeRemove = sessionStorage.removeItem;
    const blocked = vi
      .spyOn(sessionStorage, 'removeItem')
      .mockImplementation((key) => {
        if (key === LOCAL_SIGNOUT_KEY)
          throw new Error('fictional removal failure');
        nativeRemove(key);
      });
    await emailSubmit();
    await act(flush);
    expect(navigation.push).not.toHaveBeenCalled();
    expect(getAuthHeader()).toBeNull();
    expect(sessionStorage.getItem(LOCAL_SIGNOUT_KEY)).toBe('corrupt');
    expect(screen.getByText(/Validated sign-in is waiting/)).toBeTruthy();
    blocked.mockRestore();
    fireEvent.click(screen.getByTestId('auth-cache-gate-retry'));
    await act(flush);
    await waitFor(() =>
      expect(navigation.push).toHaveBeenCalledExactlyOnceWith('/'),
    );
    expect(sessionStorage.getItem(LOCAL_SIGNOUT_KEY)).toBeNull();
    expect(getAuthHeader()).toBe('Bearer fictional-b');
    expect(
      calls.filter(({ url }) => url.endsWith('/email/complete')),
    ).toHaveLength(1);
  });
  it('email supersession retires Google busy feedback without cancelling email', async () => {
    render(
      <AuthProvider>
        <AuthLanding
          mode="hosted"
          runtimeConfig={{
            ...emailConfig,
            google: { enabled: true, clientId: 'fictional-client' },
          }}
        />
      </AuthProvider>,
    );
    await screen.findByRole('button', { name: 'Fictional Google button' });
    const pending = deferred<Response>();
    authResponse = () => pending.promise;
    act(() => googleCallback({ credential: 'fictional-proof' }));
    await act(flush);
    const google = calls.find(({ url }) => url.endsWith('/google/complete'))!;
    await emailSubmit();
    await act(flush);
    expect(google.init!.signal!.aborted).toBe(true);
    const email = calls.find(({ url }) => url.endsWith('/email/complete'))!;
    expect(email.init!.signal!.aborted).toBe(false);
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
    expect(
      screen
        .getByRole('button', { name: 'Verifying...' })
        .hasAttribute('disabled'),
    ).toBe(true);
    expect(navigation.push).not.toHaveBeenCalled();
  });

  it.each(['current', 'route', 'provider', 'local'] as const)(
    'Google timeout belongs only to the current intent (%s)',
    async (exit) => {
      const view = render(
        <AuthProvider>
          <AuthLanding
            mode="hosted"
            runtimeConfig={{
              ...emailConfig,
              google: { enabled: true, clientId: 'fictional-client' },
            }}
          />
        </AuthProvider>,
      );
      await screen.findByRole('button', { name: 'Fictional Google button' });
      const response = deferred<Response>();
      authResponse = () => response.promise;
      // Drive the UI timeout callback directly, before the independent ten-second
      // transaction deadline. This tests error ownership, not elapsed-time policy.
      const nativeSetTimeout = globalThis.setTimeout;
      let expire: (() => void) | undefined;
      vi.spyOn(globalThis, 'setTimeout').mockImplementation(
        (handler, delay, ...args) => {
          if (delay === 15_000 && typeof handler === 'function')
            expire = () => handler(...args);
          return nativeSetTimeout(handler, delay, ...args);
        },
      );
      act(() => googleCallback({ credential: 'fictional-proof' }));
      await act(flush);
      const completion = calls.find(({ url }) =>
        url.endsWith('/google/complete'),
      )!;
      expect(completion.init!.signal!.aborted).toBe(false);
      expect(expire).toBeDefined();
      if (exit === 'route') {
        setPath('/elsewhere');
        view.rerender(
          <AuthProvider>
            <div>Other route</div>
          </AuthProvider>,
        );
      } else if (exit === 'provider') view.unmount();
      else if (exit === 'local') act(() => clearLocalAuthState());
      await act(async () => {
        expire!();
        await flush();
      });
      expect(completion.init!.signal!.aborted).toBe(true);
      if (exit === 'current')
        expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
      else
        expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
      expect(navigation.push).not.toHaveBeenCalled();
    },
  );

  it.each(['setup', 'enrollment'] as const)(
    'superseding %s clears its busy state without cancelling the replacement',
    async (first) => {
      setPath('/setup');
      render(
        <AuthProvider>
          <AuthLanding mode="self-hosted" runtimeConfig={emailConfig} />
        </AuthProvider>,
      );
      fireEvent.change(await screen.findByLabelText('Setup Token'), {
        target: { value: 'fictional-setup' },
      });
      fireEvent.change(screen.getByLabelText('Pending ID'), {
        target: { value: 'fictional-pending' },
      });
      fireEvent.change(screen.getByLabelText('Code'), {
        target: { value: '000000' },
      });
      const firstLabel =
        first === 'setup' ? 'Complete Setup' : 'Complete Enrollment';
      const secondLabel =
        first === 'setup' ? 'Complete Enrollment' : 'Complete Setup';
      const pending = deferred<Response>();
      authResponse = () => pending.promise;
      fireEvent.click(screen.getByRole('button', { name: firstLabel }));
      await act(flush);
      const firstCall = calls.find(({ url }) =>
        url.endsWith(first === 'setup' ? '/setup' : '/enroll/complete'),
      )!;
      fireEvent.click(screen.getByRole('button', { name: secondLabel }));
      await act(flush);
      expect(firstCall.init!.signal!.aborted).toBe(true);
      expect(
        screen
          .getByRole('button', { name: firstLabel })
          .hasAttribute('disabled'),
      ).toBe(false);
      const secondCall = calls.find(({ url }) =>
        url.endsWith(first === 'setup' ? '/enroll/complete' : '/setup'),
      )!;
      expect(secondCall.init!.signal!.aborted).toBe(false);
      expect(navigation.push).not.toHaveBeenCalled();
      // A newer same-kind intent must remain busy after the old finally runs.
      const replacementInput = screen.getByLabelText(
        first === 'setup' ? 'Pending ID' : 'Setup Token',
      );
      fireEvent.submit(replacementInput.closest('form')!);
      await act(flush);
      expect(secondCall.init!.signal!.aborted).toBe(true);
      const replacements = calls.filter(({ url }) =>
        url.endsWith(first === 'setup' ? '/enroll/complete' : '/setup'),
      );
      expect(replacements).toHaveLength(2);
      expect(replacements[1].init!.signal!.aborted).toBe(false);
      expect(
        screen
          .getByRole('button', {
            name:
              first === 'setup' ? 'Completing enrollment...' : 'Setting up...',
          })
          .hasAttribute('disabled'),
      ).toBe(true);
    },
  );

  it.each(['email', 'google', 'setup', 'enrollment'] as const)(
    '%s completion survives its own cache invalidation, and navigation waits for acknowledgement',
    async (kind) => {
      if (kind === 'setup' || kind === 'enrollment') setPath('/setup');
      render(
        <AuthProvider>
          <AuthLanding
            mode={
              kind === 'setup' || kind === 'enrollment'
                ? 'self-hosted'
                : 'hosted'
            }
            runtimeConfig={
              kind === 'google'
                ? {
                    ...emailConfig,
                    google: { enabled: true, clientId: 'fictional-client' },
                  }
                : emailConfig
            }
          />
        </AuthProvider>,
      );
      await screen.findByText('Welcome to Daemon');
      const cache = deferred<Response>();
      const response = deferred<Response>();
      clearResponse = () => cache.promise;
      authResponse = () => response.promise;
      if (kind === 'email') await emailSubmit();
      else if (kind === 'google') {
        await screen.findByRole('button', { name: 'Fictional Google button' });
        act(() => googleCallback({ credential: 'fictional-proof' }));
      } else if (kind === 'setup') {
        fireEvent.change(screen.getByLabelText('Setup Token'), {
          target: { value: 'fictional-setup' },
        });
        fireEvent.click(screen.getByRole('button', { name: 'Complete Setup' }));
      } else {
        fireEvent.change(screen.getByLabelText('Pending ID'), {
          target: { value: 'fictional-pending' },
        });
        fireEvent.change(screen.getByLabelText('Code'), {
          target: { value: '000000' },
        });
        fireEvent.click(
          screen.getByRole('button', { name: 'Complete Enrollment' }),
        );
      }
      await act(flush);
      const completion = calls.find(({ url }) =>
        url.endsWith(
          kind === 'email'
            ? '/email/complete'
            : kind === 'google'
              ? '/google/complete'
              : kind === 'setup'
                ? '/setup'
                : '/enroll/complete',
        ),
      );
      expect(
        completion,
        'the nested form must dispatch its valid action',
      ).toBeDefined();
      expect(completion!.init!.signal!.aborted).toBe(false);
      expect(screen.getByText('Welcome to Daemon')).toBeTruthy();
      expect(getAuthHeader()).toBeNull();
      expect(getAccessToken()).toBeNull();
      expect(navigation.push).not.toHaveBeenCalled();
      await act(async () => {
        response.resolve(identity());
        await flush();
      });
      expect(navigation.push).not.toHaveBeenCalled();
      expect(getAuthHeader()).toBeNull();
      await act(async () => {
        cache.resolve(ack());
        await flush();
      });
      await waitFor(() =>
        expect(navigation.push).toHaveBeenCalledExactlyOnceWith('/'),
      );
      expect(getAuthHeader()).toBe('Bearer fictional-b');
    },
  );

  it.each(['route', 'provider', 'local'] as const)(
    'genuine public %s exit cancels held completion without success navigation',
    async (exit) => {
      const view = render(
        <AuthProvider>
          <PublicRoute />
        </AuthProvider>,
      );
      const response = deferred<Response>();
      authResponse = () => response.promise;
      await emailSubmit();
      await act(flush);
      const call = calls.find(({ url }) => url.endsWith('/email/complete'));
      expect(call).toBeDefined();
      if (exit === 'provider') view.unmount();
      else if (exit === 'route') {
        setPath('/landing');
        view.rerender(
          <AuthProvider>
            <div>Public route exited</div>
          </AuthProvider>,
        );
      } else {
        // Hold the cache gate so its immediate local escape remains available.
        const cache = deferred<Response>();
        clearResponse = () => cache.promise;
        act(() => invalidateBrowserCache());
        fireEvent.click(await screen.findByTestId('auth-cache-gate-signout'));
      }
      await act(flush);
      expect(call!.init!.signal!.aborted).toBe(true);
      await act(async () => {
        response.resolve(identity());
        await flush();
      });
      expect(navigation.push).not.toHaveBeenCalled();
      expect(getAccessToken()).not.toBe('fictional-b');
    },
  );

  it('failed post-completion cache clear preserves the challenge and successful intent until explicit Retry, without resubmission', async () => {
    render(
      <AuthProvider>
        <PublicRoute />
      </AuthProvider>,
    );
    await screen.findByLabelText('Email Address');
    clearResponse = async () => new Response(null, { status: 500 });
    await emailSubmit();
    await act(flush);
    expect(getBrowserCacheState().status).toBe('failed');
    expect(navigation.push).not.toHaveBeenCalled();
    expect(getAuthHeader()).toBeNull();
    expect(
      (screen.getByLabelText('Verification Code') as HTMLInputElement).value,
    ).toBe('000000');
    expect(
      screen
        .getByRole('button', { name: 'Verifying...' })
        .hasAttribute('disabled'),
    ).toBe(true);
    const completions = calls.filter(({ url }) =>
      url.endsWith('/email/complete'),
    );
    expect(completions).toHaveLength(1);
    clearResponse = async () => ack();
    fireEvent.click(screen.getByTestId('auth-cache-gate-retry'));
    await waitFor(() =>
      expect(navigation.push).toHaveBeenCalledExactlyOnceWith('/'),
    );
    expect(
      calls.filter(({ url }) => url.endsWith('/email/complete')),
    ).toHaveLength(1);
  });

  it('local exit while a successful public intent waits for cache acknowledgement suppresses navigation', async () => {
    render(
      <AuthProvider>
        <PublicRoute />
      </AuthProvider>,
    );
    await screen.findByLabelText('Email Address');
    const cache = deferred<Response>();
    clearResponse = () => cache.promise;
    await emailSubmit();
    await act(flush);
    expect(navigation.push).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('auth-cache-gate-signout'));
    await act(async () => {
      cache.resolve(ack());
      await flush();
    });
    expect(navigation.push).not.toHaveBeenCalled();
    expect(getAccessToken()).toBeNull();
  });

  it.each([false, true])(
    'noncurrent DELETE survives protected unmount (expired refresh: %s) and reports the same outcome after remount',
    async (expired) => {
      setPath('/settings/devices');
      setAccessToken('fictional-a', Date.now() + 31_000, 'fictional-a');
      render(
        <AuthProvider>
          <ProtectedRoute />
        </AuthProvider>,
      );
      await screen.findByText(device.display_name);
      if (expired) vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 2000);
      const deleted = deferred<Response>();
      const refreshed = deferred<Response>();
      deleteResponse = () => deleted.promise;
      refreshResponse = () => refreshed.promise;
      await confirmRevoke();
      await act(flush);
      expect(screen.queryByTestId('protected-view')).toBeNull();
      expect(screen.queryByText(device.display_name)).toBeNull();
      expect(getAuthHeader()).toBeNull();
      expect(readAuthCoordination()?.status).toBe('pending');
      if (expired) {
        const refresh = calls.find(({ url }) => url.endsWith('/refresh'))!;
        expect(refresh.init!.signal!.aborted).toBe(false);
        expect(calls.some(({ url }) => url.endsWith('/devices/other'))).toBe(
          false,
        );
        await act(async () => {
          refreshed.resolve(identity());
          await flush();
        });
      }
      const deletion = calls.find(({ url }) => url.endsWith('/devices/other'))!;
      expect(deletion).toBeDefined();
      expect(deletion.init!.signal!.aborted).toBe(false);
      await act(async () => {
        deleted.resolve(new Response(null, { status: 204 }));
        await flush();
      });
      await screen.findByTestId('protected-view');
      await screen.findByText('No active sessions found');
      expect(
        calls.filter(({ url }) => url.endsWith('/devices/other')),
      ).toHaveLength(1);
      expect(readAuthCoordination()?.status).toBe('stable');
      expect(navigation.push).not.toHaveBeenCalled();
      vi.restoreAllMocks();
    },
  );

  it.each(['route', 'provider', 'local'] as const)(
    'protected %s exit cancels a delayed DELETE without stale result',
    async (exit) => {
      setPath('/settings/devices');
      setAccessToken('fictional-a', Date.now() + 1800_000, 'fictional-a');
      const view = render(
        <AuthProvider>
          <ProtectedRoute />
        </AuthProvider>,
      );
      const deleted = deferred<Response>();
      deleteResponse = () => deleted.promise;
      await confirmRevoke();
      await act(flush);
      const call = calls.find(({ url }) => url.endsWith('/devices/other'))!;
      expect(call).toBeDefined();
      expect(call.init!.signal!.aborted).toBe(false);
      expect(screen.queryByTestId('protected-view')).toBeNull();
      if (exit === 'provider') view.unmount();
      else if (exit === 'route') {
        setPath('/auth');
        view.rerender(
          <AuthProvider>
            <PublicRoute />
          </AuthProvider>,
        );
      } else fireEvent.click(screen.getByTestId('auth-cache-gate-signout'));
      await act(flush);
      expect(call.init!.signal!.aborted).toBe(true);
      await act(async () => {
        deleted.resolve(new Response(null, { status: 204 }));
        await flush();
      });
      expect(navigation.push).not.toHaveBeenCalled();
      expect(screen.queryByText('No active sessions found')).toBeNull();
    },
  );

  it('acknowledged noncurrent DELETE rejection is reported by the remounted view without retrying the operation', async () => {
    setPath('/settings/devices');
    setAccessToken('fictional-a', Date.now() + 1800_000, 'fictional-a');
    render(
      <AuthProvider>
        <ProtectedRoute />
      </AuthProvider>,
    );
    const deleted = deferred<Response>();
    deleteResponse = () => deleted.promise;
    await confirmRevoke();
    await act(flush);
    expect(screen.queryByTestId('protected-view')).toBeNull();
    await act(async () => {
      deleted.resolve(new Response(null, { status: 404 }));
      await flush();
    });
    await screen.findByText('Device not found');
    expect(screen.getByText(device.display_name)).toBeTruthy();
    expect(
      calls.filter(({ url }) => url.endsWith('/devices/other')),
    ).toHaveLength(1);
  });

  it.each([false, true])(
    'normal provider logout cancels a held DELETE (current=%s) without late success navigation',
    async (current) => {
      setPath('/settings/devices');
      currentDevice = current;
      setAccessToken('fictional-a', Date.now() + 1800_000, 'fictional-a');
      let logout: (() => Promise<void>) | undefined;
      function CaptureLogout() {
        const auth = useAuth();
        useEffect(() => {
          logout = auth.logout;
        }, [auth.logout]);
        return null;
      }
      render(
        <AuthProvider>
          <CaptureLogout />
          <ProtectedRoute />
        </AuthProvider>,
      );
      const deleted = deferred<Response>();
      deleteResponse = () => deleted.promise;
      await confirmRevoke();
      await act(flush);
      const call = calls.find(({ url }) => url.endsWith('/devices/other'))!;
      expect(call).toBeDefined();
      expect(call.init!.signal!.aborted).toBe(false);
      expect(screen.queryByTestId('protected-view')).toBeNull();
      // AccountWidget is already unmounted by this gate. Calling its captured
      // context method deliberately tests the stronger ordering anyway; this
      // does not simulate a possible click on an absent logout button.
      expect(logout).toBeDefined();
      await act(async () => {
        const completion = logout!();
        // The coordinator's transport signal is cancelled synchronously even
        // though the outer device owner's controller is a different object.
        expect(call.init!.signal!.aborted).toBe(true);
        await completion;
        await flush();
      });
      expect(window.location.href).toBe('/auth');
      expect(getAccessToken()).toBeNull();
      // Aborting a dispatched cookie writer leaves uncertainty. Local logout
      // completes, but this is not confirmation of server-side revocation.
      expect(calls.some(({ url }) => url.endsWith('/auth/logout'))).toBe(false);
      expect(readAuthCoordination()).toEqual({
        version: 1,
        epoch: 'fictional-a',
        status: 'uncertain',
      });
      await act(async () => {
        deleted.resolve(new Response(null, { status: 204 }));
        await flush();
      });
      expect(window.location.replace).not.toHaveBeenCalled();
      expect(navigation.push).not.toHaveBeenCalled();
      expect(screen.queryByText('No active sessions found')).toBeNull();
      expect(screen.queryByTestId('protected-view')).toBeNull();
    },
  );

  it('an uncertain DELETE keeps protected DOM absent and is not automatically repeated by cache retry', async () => {
    setPath('/settings/devices');
    setAccessToken('fictional-a', Date.now() + 1800_000, 'fictional-a');
    render(
      <AuthProvider>
        <ProtectedRoute />
      </AuthProvider>,
    );
    const deleted = deferred<Response>();
    deleteResponse = () => deleted.promise;
    await confirmRevoke();
    await act(flush);
    await act(async () => {
      deleted.resolve(new Response(null, { status: 500 }));
      await flush();
    });
    expect(readAuthCoordination()?.status).toBe('uncertain');
    expect(screen.queryByTestId('protected-view')).toBeNull();
    expect(getAuthHeader()).toBeNull();
    fireEvent.click(screen.getByTestId('auth-cache-gate-retry'));
    await act(flush);
    expect(screen.queryByTestId('protected-view')).toBeNull();
    expect(
      calls.filter(({ url }) => url.endsWith('/devices/other')),
    ).toHaveLength(1);
  });

  it('current-device completion navigates from the owner above the gate on the helper committed signed-out generation', async () => {
    setPath('/settings/devices');
    currentDevice = true;
    setAccessToken('fictional-a', Date.now() + 1800_000, 'fictional-a');
    const replace = vi.mocked(window.location.replace);
    render(
      <AuthProvider>
        <ProtectedRoute />
      </AuthProvider>,
    );
    const deleted = deferred<Response>();
    deleteResponse = () => deleted.promise;
    await confirmRevoke();
    await act(flush);
    const call = calls.find(({ url }) => url.endsWith('/devices/other'))!;
    expect(call.init!.signal!.aborted).toBe(false);
    expect(screen.queryByTestId('protected-view')).toBeNull();
    await act(async () => {
      deleted.resolve(new Response(null, { status: 204 }));
      await flush();
    });
    expect(replace).toHaveBeenCalledExactlyOnceWith('/setup');
    expect(getAccessToken()).toBeNull();
    expect(screen.queryByTestId('protected-view')).toBeNull();
  });
});
