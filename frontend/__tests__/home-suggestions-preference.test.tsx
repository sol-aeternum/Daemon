import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { HomeSuggestionsPreference } from '../components/settings/HomeSuggestionsPreference';

const auth = vi.hoisted(() => ({
  generation: 1,
  header: vi.fn(),
  listeners: new Set<() => void>(),
}));
vi.mock('../lib/auth', () => ({
  getAuthGeneration: () => auth.generation,
  ensureAuthHeader: () => auth.header(),
  subscribeAuthGeneration: (listener: () => void) => {
    auth.listeners.add(listener);
    return () => auth.listeners.delete(listener);
  },
}));
const fetchMock = vi.fn();
const response = (enabled: unknown, status = 200) =>
  new Response(
    JSON.stringify({
      preferences:
        enabled === undefined ? {} : { home_suggestions_enabled: enabled },
    }),
    { status },
  );
beforeEach(() => {
  auth.generation = 1;
  auth.header.mockReset().mockResolvedValue('Bearer fixture');
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it.each([true, false, undefined])(
  'reads only settings on mount and sends an isolated explicit toggle: %s',
  async (enabled) => {
    fetchMock.mockResolvedValue(response(enabled));
    render(<HomeSuggestionsPreference />);
    const button = await screen.findByRole('button', {
      name: enabled === true ? 'Turn off suggestions' : 'Turn on suggestions',
    });
    await waitFor(() =>
      expect((button as HTMLButtonElement).disabled).toBe(false),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/users\/me\/settings$/);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({
      method: 'GET',
      cache: 'no-store',
    });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][1].method).toBe('PATCH');
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      preferences: { home_suggestions_enabled: enabled !== true },
    });
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain(
        enabled === true ? 'off.' : 'on.',
      ),
    );
  },
);

it.each([true, false, 'read-failure'])(
  'reconciles a failed committed write and retains explicit retry-off: %s',
  async (saved) => {
    fetchMock
      .mockResolvedValueOnce(response(false))
      .mockResolvedValueOnce(response(true, 503))
      .mockResolvedValueOnce(
        response(saved, saved === 'read-failure' ? 503 : 200),
      );
    render(<HomeSuggestionsPreference />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Turn on suggestions' }),
    );
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain(
        'change could not be confirmed',
      ),
    );
    const off = screen.getByRole('button', { name: 'Turn off suggestions' });
    await waitFor(() =>
      expect((off as HTMLButtonElement).disabled).toBe(false),
    );
    expect(fetchMock.mock.calls.map(([, init]) => init.method)).toEqual([
      'GET',
      'PATCH',
      'GET',
    ]);
    expect(screen.getByRole('status').textContent).toContain(
      saved === 'read-failure' ? 'may be enabled' : 'is unconfirmed',
    );
    fetchMock.mockResolvedValue(response(false));
    fireEvent.click(off);
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toBe(
        'Personal suggestions are off.',
      ),
    );
    expect(JSON.parse(fetchMock.mock.calls[3][1].body)).toEqual({
      preferences: { home_suggestions_enabled: false },
    });
  },
);

it.each([null, 'true', 'network'])(
  'keeps a safe off action for unknown initial preference: %s',
  async (value) => {
    if (value === 'network') fetchMock.mockRejectedValue(new Error('offline'));
    else fetchMock.mockResolvedValue(response(value));
    render(<HomeSuggestionsPreference />);
    await waitFor(() =>
      expect(screen.getByRole('status').textContent).toContain(
        'may be enabled',
      ),
    );
    expect(
      (
        screen.getByRole('button', {
          name: 'Turn off suggestions',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
    fetchMock.mockResolvedValue(response(false));
    fireEvent.click(
      screen.getByRole('button', { name: 'Turn off suggestions' }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][1].method).toBe('PATCH');
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      preferences: { home_suggestions_enabled: false },
    });
  },
);

it('masks stale account data and prevents dispatch after auth changes during refresh', async () => {
  fetchMock.mockResolvedValue(response(true));
  render(<HomeSuggestionsPreference />);
  await waitFor(() =>
    expect(screen.getByRole('status').textContent).toBe(
      'Personal suggestions are on.',
    ),
  );
  let finish!: (header: string) => void;
  auth.header.mockReturnValue(
    new Promise<string>((resolve) => {
      finish = resolve;
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Turn off suggestions' }));
  act(() => {
    auth.generation++;
    auth.listeners.forEach((listener) => listener());
  });
  expect(screen.getByRole('status').textContent).not.toBe(
    'Personal suggestions are on.',
  );
  await act(async () => {
    finish('Bearer replacement');
  });
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.filter(([, init]) => init.method === 'GET'),
    ).toHaveLength(2),
  );
  expect(
    fetchMock.mock.calls.filter(([, init]) => init.method === 'PATCH'),
  ).toHaveLength(0);
});

it('bounds stalled credential refresh and cannot dispatch after timeout', async () => {
  vi.useFakeTimers();
  let finish!: (header: string) => void;
  auth.header.mockReturnValue(
    new Promise<string>((resolve) => {
      finish = resolve;
    }),
  );
  render(<HomeSuggestionsPreference />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5100);
  });
  expect(screen.getByRole('status').textContent).toContain('may be enabled');
  expect(
    (
      screen.getByRole('button', {
        name: 'Turn off suggestions',
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(false);
  await act(async () => {
    finish('Bearer late');
  });
  expect(fetchMock).not.toHaveBeenCalled();
});

it('does not dispatch a pending write after unmount', async () => {
  fetchMock.mockResolvedValue(response(false));
  const mounted = render(<HomeSuggestionsPreference />);
  const on = await screen.findByRole('button', { name: 'Turn on suggestions' });
  let finish!: (header: string) => void;
  auth.header.mockReturnValue(
    new Promise<string>((resolve) => {
      finish = resolve;
    }),
  );
  fireEvent.click(on);
  mounted.unmount();
  await act(async () => {
    finish('Bearer late');
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
