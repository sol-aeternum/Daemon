import { afterEach, expect, it, vi } from 'vitest';
import * as auth from '../lib/auth';
import { AUTH_COORDINATION_KEY } from '../lib/authCookieCoordinator';
import { AuthCookieTestLocks } from './helpers/authCookieLocks';
import { createAuthTestStorage } from './helpers/authCookieEnvironment';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// This file starts a real auth-module lifetime with no bound credentials or
// shared record. Never reset modules/generation to manufacture eligibility.
it('one genuinely unbound retry obtains a fresh lock but retains the original deadline including owned fetch termination', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('localStorage', createAuthTestStorage());
  vi.stubGlobal('sessionStorage', createAuthTestStorage());
  const locks = new AuthCookieTestLocks();
  locks.held = true;
  vi.stubGlobal('navigator', { locks });
  let signal!: AbortSignal;
  vi.stubGlobal(
    'fetch',
    vi.fn((_url, init: RequestInit) => {
      signal = init.signal!;
      return new Promise<Response>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(signal.reason), {
          once: true,
        });
      });
    }),
  );
  const startup = auth.refreshAccessToken();
  expect(locks.queue).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(9000);
  localStorage.setItem(
    AUTH_COORDINATION_KEY,
    JSON.stringify({ version: 1, epoch: 'fictional-peer', status: 'stable' }),
  );
  locks.held = false;
  locks.drain();
  for (let i = 0; i < 40; i++) await Promise.resolve();
  expect(locks.request).toHaveBeenCalledTimes(2);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(signal.aborted).toBe(false);
  await vi.advanceTimersByTimeAsync(1000);
  expect(await startup).toMatchObject({
    success: false,
    error: 'Session operation timed out',
    preserveProtectedUrl: true,
  });
  expect(signal.aborted).toBe(true);
  for (let i = 0; i < 40; i++) await Promise.resolve();
  expect(locks.held).toBe(false);
  expect(locks.queue).toHaveLength(0);
  expect((await auth.refreshAccessToken()).success).toBe(false);
  expect(locks.request).toHaveBeenCalledTimes(2);
  expect(auth.getAuthHeader()).toBeNull();
});
