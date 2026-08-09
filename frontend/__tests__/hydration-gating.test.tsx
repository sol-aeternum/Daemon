import React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, afterEach, beforeEach } from 'vitest';

const useClientMountedMock = vi.fn();

vi.mock('../hooks/useClientMounted', () => ({
  useClientMounted: () => useClientMountedMock(),
}));

import { useLocalStorage } from '../hooks/useLocalStorage';
import { useOnlineStatus } from '../hooks/useOnlineStatus';
import { StudioProvider, useStudio } from '../app/studio/StudioProvider';

function LocalStorageHarness({
  storageKey,
  fallbackValue,
}: {
  storageKey: string;
  fallbackValue: string;
}) {
  const { value, isLoaded, setValue, removeValue } = useLocalStorage(
    storageKey,
    fallbackValue,
  );

  return (
    <div>
      <div data-testid="local-value">{value}</div>
      <div data-testid="local-loaded">{isLoaded ? 'loaded' : 'not-loaded'}</div>
      <button type="button" onClick={() => setValue('updated')}>
        set
      </button>
      <button type="button" onClick={() => removeValue()}>
        clear
      </button>
    </div>
  );
}

function OnlineStatusHarness() {
  const { isOnline, wasOffline } = useOnlineStatus();
  return (
    <div>
      <div data-testid="is-online">{String(isOnline)}</div>
      <div data-testid="was-offline">{String(wasOffline)}</div>
    </div>
  );
}

function StudioHarness() {
  const { selectedModels, aspectRatio } = useStudio();
  return (
    <div>
      <div data-testid="studio-models">{JSON.stringify(selectedModels)}</div>
      <div data-testid="studio-ratio">{aspectRatio}</div>
    </div>
  );
}

function setNavigatorOnline(value: boolean) {
  Object.defineProperty(window.navigator, 'onLine', {
    configurable: true,
    get: () => value,
  });
}

describe('hydration gating', () => {
  beforeEach(() => {
    if (typeof localStorage === 'undefined') {
      const store: Record<string, string> = {};
      const fakeStorage = {
        getItem: (key: string): string | null => store[key] ?? null,
        setItem: (key: string, value: string): void => {
          store[key] = value;
        },
        removeItem: (key: string): void => {
          delete store[key];
        },
        clear: (): void => {
          for (const key of Object.keys(store)) {
            delete store[key];
          }
        },
      };

      Object.defineProperty(globalThis, 'localStorage', {
        configurable: true,
        value: fakeStorage,
      });
      Object.defineProperty(window, 'localStorage', {
        configurable: true,
        value: fakeStorage,
      });
    }

    localStorage.clear();
    useClientMountedMock.mockReset();
  });

  afterEach(() => {
    useClientMountedMock.mockReset();
  });

  it('masks persisted localStorage values until client mount', () => {
    const key = 'daemon:test';
    localStorage.setItem(key, JSON.stringify('stored'));
    useClientMountedMock.mockReturnValue(false);

    const { rerender } = render(
      <LocalStorageHarness storageKey={key} fallbackValue="fallback" />,
    );

    expect(screen.getByTestId('local-value').textContent).toBe('fallback');
    expect(screen.getByTestId('local-loaded').textContent).toBe('not-loaded');

    useClientMountedMock.mockReturnValue(true);
    rerender(<LocalStorageHarness storageKey={key} fallbackValue="fallback" />);

    expect(screen.getByTestId('local-value').textContent).toBe('stored');
    expect(screen.getByTestId('local-loaded').textContent).toBe('loaded');
  });

  it('keeps localStorage setter/remover behavior while mounted', async () => {
    const key = 'daemon:test:set';
    useClientMountedMock.mockReturnValue(true);

    render(<LocalStorageHarness storageKey={key} fallbackValue="fallback" />);

    await act(async () => {
      screen.getByRole('button', { name: 'set' }).click();
    });
    expect(screen.getByTestId('local-value').textContent).toBe('updated');
    expect(localStorage.getItem(key)).toBe(JSON.stringify('updated'));

    await act(async () => {
      screen.getByRole('button', { name: 'clear' }).click();
    });
    expect(screen.getByTestId('local-value').textContent).toBe('fallback');
    expect(localStorage.getItem(key)).toBeNull();
  });

  it('keeps online status stable until client mount and then syncs navigator', async () => {
    setNavigatorOnline(false);
    useClientMountedMock.mockReturnValue(false);

    const { rerender } = render(<OnlineStatusHarness />);

    expect(screen.getByTestId('is-online').textContent).toBe('true');
    expect(screen.getByTestId('was-offline').textContent).toBe('false');

    act(() => {
      setNavigatorOnline(true);
      useClientMountedMock.mockReturnValue(true);
      rerender(<OnlineStatusHarness />);
    });

    await waitFor(() => {
      expect(screen.getByTestId('is-online').textContent).toBe('true');
    });

    act(() => {
      setNavigatorOnline(false);
      window.dispatchEvent(new Event('offline'));
    });

    await waitFor(() => {
      expect(screen.getByTestId('is-online').textContent).toBe('false');
      expect(screen.getByTestId('was-offline').textContent).toBe('true');
    });
  });

  it('loads studio defaults until mount and then restores persisted values', async () => {
    const storedModels = ['gpt-4o-mini', 'gpt-4-vision'];
    localStorage.setItem('studio:selectedModels', JSON.stringify(storedModels));
    localStorage.setItem('studio:aspectRatio', '16:9');
    useClientMountedMock.mockReturnValue(false);

    const { rerender } = render(
      <StudioProvider>
        <StudioHarness />
      </StudioProvider>,
    );

    expect(screen.getByTestId('studio-models').textContent).toBe('[]');
    expect(screen.getByTestId('studio-ratio').textContent).toBe('1:1');

    useClientMountedMock.mockReturnValue(true);
    rerender(
      <StudioProvider>
        <StudioHarness />
      </StudioProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('studio-models').textContent).toBe(
        JSON.stringify(storedModels),
      );
      expect(screen.getByTestId('studio-ratio').textContent).toBe('16:9');
    });

    expect(localStorage.getItem('studio:selectedModels')).toBe(
      JSON.stringify(storedModels),
    );
    expect(localStorage.getItem('studio:aspectRatio')).toBe('16:9');
  });
});
