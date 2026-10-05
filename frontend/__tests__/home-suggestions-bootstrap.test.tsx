import { cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

vi.mock('../lib/auth', () => ({
  getAccessToken: () => null,
  getAuthGeneration: () => 0,
  subscribeAuthGeneration: () => () => {},
  ensureAuthHeader: async () => 'Bearer restored-cookie-session',
}));
vi.mock('../hooks/useAuthGeneration', () => ({ useAuthGeneration: () => 0 }));

import { useHomeSuggestions } from '../hooks/useHomeSuggestions';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('loads after cookie-session restoration on a fresh tab without a prior memory token', async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValue(
      new Response(
        JSON.stringify({ enabled: true, status: 'empty', suggestions: [] }),
      ),
    );
  vi.stubGlobal('fetch', fetchMock);
  const hook = renderHook(() => useHomeSuggestions());
  await waitFor(() => expect(hook.result.current.view.status).toBe('empty'));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe(
    'Bearer restored-cookie-session',
  );
});
