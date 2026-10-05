import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProfileTab from '../components/settings/ProfileTab';

vi.mock('../lib/auth', () => ({
  ensureAuthHeader: async () => 'Bearer fictional',
  getAuthGeneration: () => 1,
  subscribeAuthGeneration: () => () => {},
}));

const fetchMock = vi.fn();
beforeEach(() => {
  vi.useFakeTimers();
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it.each(['enabled', 'failed', 'timed-out'] as const)(
  'keeps recovery independent of a stalled profile body: preference %s',
  async (preference) => {
    let finishProfile!: (settings: unknown) => void;
    const profileBody = new Promise((resolve) => {
      finishProfile = resolve;
    });
    const readProfileBody = vi.fn(() => profileBody);
    fetchMock.mockImplementation((_url: string, init: RequestInit) => {
      if (init.method === 'PATCH') return Promise.resolve(new Response('{}'));
      // Distinguish requests by their real contracts, not effect ordering.
      if (init.cache !== 'no-store') {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: readProfileBody,
        });
      }
      if (preference === 'failed') return Promise.reject(new Error('offline'));
      if (preference === 'timed-out') return new Promise(() => {});
      return Promise.resolve(
        new Response(
          JSON.stringify({ preferences: { home_suggestions_enabled: true } }),
        ),
      );
    });

    await act(async () => {
      render(<ProfileTab />);
    });
    expect(readProfileBody).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText('Display Name')).toBeNull();
    if (preference === 'timed-out') {
      expect(
        (
          screen.getByRole('button', {
            name: 'Turn off suggestions',
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(true);
    }
    // Go beyond the parent's headers-only timeout: its body is still stalled.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(13000);
    });
    const off = screen.getByRole('button', { name: 'Turn off suggestions' });
    expect((off as HTMLButtonElement).disabled).toBe(false);
    expect(screen.queryByLabelText('Display Name')).toBeNull();
    if (preference !== 'enabled')
      expect(screen.getByRole('status').textContent).toContain(
        'may be enabled',
      );

    await act(async () => {
      fireEvent.click(off);
    });
    expect(screen.getByRole('status').textContent).toBe(
      'Personal suggestions are off.',
    );
    const writes = fetchMock.mock.calls.filter(
      ([, init]) => init.method === 'PATCH',
    );
    expect(writes).toHaveLength(1);
    expect(JSON.parse(writes[0][1].body)).toEqual({
      preferences: { home_suggestions_enabled: false },
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(
      fetchMock.mock.calls.every(([url]) => url.endsWith('/users/me/settings')),
    ).toBe(true);

    // Completing the unrelated form must not remount or reset recovery state.
    await act(async () => {
      finishProfile({
        preferences: {
          display_name: 'Fictional profile',
          home_suggestions_enabled: true,
        },
      });
    });
    expect(
      (screen.getByLabelText('Display Name') as HTMLInputElement).value,
    ).toBe('Fictional profile');
    expect(screen.getByRole('status').textContent).toBe(
      'Personal suggestions are off.',
    );
    expect(fetchMock).toHaveBeenCalledTimes(3);
  },
);
