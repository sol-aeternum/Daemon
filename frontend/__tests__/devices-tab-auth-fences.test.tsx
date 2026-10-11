import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react';

const session = vi.hoisted(() => ({ generation: 1 }));
const list = vi.hoisted(() => vi.fn());
const revoke = vi.hoisted(() => vi.fn());
vi.mock('../lib/auth', () => ({
  listDevices: list,
  revokeDevice: revoke,
  getAuthGeneration: () => session.generation,
  isAuthResultCurrent: (result: { generation?: number }) =>
    result.generation === session.generation,
}));
vi.mock('../components/settings/EnrollmentModal', () => ({
  default: () => null,
}));
import DevicesTab from '../components/settings/DevicesTab';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const device = (id: string, current = false) => ({
  id,
  device_name: `Fictional ${id}`,
  client_kind: 'web',
  created_at: '2026-10-01T00:00:00Z',
  last_seen_at: null,
  current,
  revoked: false,
});
let navigate: ReturnType<typeof vi.fn>;
beforeEach(() => {
  session.generation = 1;
  list.mockReset().mockResolvedValue({
    success: true,
    devices: [device('A', true), device('other')],
  });
  revoke.mockReset();
  navigate = vi.fn();
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { replace: navigate },
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function confirm(id = 'A') {
  const label = await screen.findByText(`Fictional ${id}`);
  const row = label.closest('div.min-w-0')!.parentElement!;
  fireEvent.click(within(row).getByRole('button', { name: 'Revoke' }));
  const modal = screen.getByRole('heading', { name: 'Revoke device?' })
    .parentElement!.parentElement!.parentElement!;
  fireEvent.click(within(modal).getByRole('button', { name: 'Revoke' }));
}

describe('DevicesTab caller ownership', () => {
  it.each(['success', 'failure', 'throw'] as const)(
    'late A list %s cannot populate or expose an error in B',
    async (outcome) => {
      const result = deferred<unknown>();
      list.mockReturnValueOnce(result.promise);
      render(<DevicesTab />);
      session.generation += 1;
      await act(async () => {
        if (outcome === 'throw')
          result.reject(new Error('Fictional private A error'));
        else
          result.resolve(
            outcome === 'success'
              ? { success: true, devices: [device('private-A')] }
              : { success: false, error: 'Fictional private A error' },
          );
      });
      expect(screen.queryByText('Fictional private-A')).toBeNull();
      expect(screen.queryByText('Fictional private A error')).toBeNull();
      expect(navigate).not.toHaveBeenCalled();
    },
  );

  it('current-device success navigates on the helper committed generation, not its old entry generation', async () => {
    const result = deferred<unknown>();
    revoke.mockReturnValueOnce(result.promise);
    render(<DevicesTab />);
    await confirm();
    expect(revoke).toHaveBeenCalledWith('A', {
      current: true,
      signal: expect.any(AbortSignal),
    });
    session.generation += 1; // owned terminal helper has cleared A locally
    await act(async () => {
      result.resolve({ success: true, generation: session.generation });
    });
    expect(navigate).toHaveBeenCalledExactlyOnceWith('/setup');
  });

  it.each(['success', 'failure', 'throw'] as const)(
    'late A revoke %s cannot redirect, report an error, or clear B pending UI',
    async (outcome) => {
      const a = deferred<unknown>();
      const b = deferred<unknown>();
      revoke.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
      render(<DevicesTab />);
      await confirm();
      const signalA = revoke.mock.calls[0][1].signal as AbortSignal;
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      session.generation += 1;
      await confirm('other');
      expect(signalA.aborted).toBe(true);
      await act(async () => {
        if (outcome === 'throw') a.reject(new Error('Fictional stale A error'));
        else
          a.resolve({
            success: outcome === 'success',
            generation: 1,
            error: 'Fictional stale A error',
          });
      });
      expect(navigate).not.toHaveBeenCalled();
      expect(screen.queryByText('Fictional stale A error')).toBeNull();
      expect(
        screen
          .getByRole('button', { name: 'Revoking...' })
          .hasAttribute('disabled'),
      ).toBe(true);
      await act(async () => {
        b.resolve({ success: true, generation: session.generation });
      });
      expect(screen.queryByText('Fictional other')).toBeNull();
      expect(screen.getByText('Fictional A')).toBeTruthy();
      expect(
        screen.queryByRole('heading', { name: 'Revoke device?' }),
      ).toBeNull();
      expect(navigate).not.toHaveBeenCalled();
    },
  );

  it('unmount cancels the owned revoke and a late successful result cannot navigate', async () => {
    const result = deferred<unknown>();
    revoke.mockReturnValueOnce(result.promise);
    const view = render(<DevicesTab />);
    await confirm();
    const signal = revoke.mock.calls[0][1].signal as AbortSignal;
    view.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => {
      result.resolve({ success: true, generation: session.generation });
    });
    expect(navigate).not.toHaveBeenCalled();
  });
});
