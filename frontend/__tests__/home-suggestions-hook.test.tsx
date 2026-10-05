import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useHomeSuggestions } from '@/hooks/useHomeSuggestions';
import {
  clearLocalAuthState,
  getAuthGeneration,
  setAccessToken,
} from '@/lib/auth';

const FUTURE = new Date(Date.now() + 3600_000).toISOString();

const WIRE_ROW = {
  id: 's1',
  summary: 'Summarise the plasma thread',
  prompt: 'Continue my plasma conversation.',
  source: { conversation_id: 'c1', title: 'Plasma thread' },
  expires_at: FUTURE,
};

const fetchMock = vi.fn<typeof globalThis.fetch>();

function signIn(token: string) {
  setAccessToken(token, Date.now() + 600_000);
}

const listUrl = (url: string) => url.endsWith('/home-suggestions');
const refreshUrl = (url: string) => url.endsWith('/home-suggestions/refresh');
const settingsUrl = (url: string) => url.endsWith('/users/me/settings');

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true, advanceTimeDelta: 20 });
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  clearLocalAuthState();
});

afterEach(() => {
  cleanup();
  clearLocalAuthState();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it('uses a single Bearer header for list, enable, refresh and disable', async () => {
  signIn('header-fixture');
  fetchMock.mockImplementation(async (input, init) => {
    expect(new Headers(init?.headers).get('Authorization')).toBe(
      'Bearer header-fixture',
    );
    if (settingsUrl(String(input))) return listResponse({});
    if (refreshUrl(String(input))) return listResponse({ status: 'unchanged' });
    return listResponse({ enabled: true, status: 'empty', suggestions: [] });
  });
  const view = renderHook(() => useHomeSuggestions());
  await waitFor(() => expect(view.result.current.view.status).toBe('empty'));
  await act(async () => {
    await view.result.current.enable();
  });
  await act(async () => {
    await view.result.current.disable();
  });
  expect(fetchMock.mock.calls.some(([url]) => settingsUrl(String(url)))).toBe(
    true,
  );
  expect(fetchMock.mock.calls.some(([url]) => refreshUrl(String(url)))).toBe(
    true,
  );
});

it('never renders old ready metadata while the new account response is pending', async () => {
  signIn('old-account');
  fetchMock.mockResolvedValueOnce(
    listResponse({ enabled: true, status: 'ready', suggestions: [WIRE_ROW] }),
  );
  const renders: Array<{ generation: number; count: number }> = [];
  const view = renderHook(() => {
    const hook = useHomeSuggestions();
    renders.push({
      generation: getAuthGeneration(),
      count: hook.view.suggestions.length,
    });
    return hook;
  });
  await waitFor(() => expect(view.result.current.view.status).toBe('ready'));
  fetchMock.mockImplementation(() => new Promise<Response>(() => {}));
  act(() => signIn('new-account'));
  const generation = getAuthGeneration();
  expect(view.result.current.view.suggestions).toEqual([]);
  expect(
    renders
      .filter((r) => r.generation === generation)
      .every((r) => r.count === 0),
  ).toBe(true);
});

it('removes rows at their deadline without another list request or generation', async () => {
  signIn('expiry-account');
  fetchMock.mockResolvedValue(
    listResponse({
      enabled: true,
      status: 'ready',
      suggestions: [
        { ...WIRE_ROW, expires_at: new Date(Date.now() + 2000).toISOString() },
      ],
    }),
  );
  const view = renderHook(() => useHomeSuggestions());
  await waitFor(() => expect(view.result.current.view.status).toBe('ready'));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2100);
  });
  expect(view.result.current.view.suggestions).toEqual([]);
  expect(view.result.current.view.status).toBe('expired');
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it.each(['error', 'unavailable'])(
  'keeps a successful HTTP %s response distinct from empty',
  async (status) => {
    signIn('status-account');
    fetchMock.mockResolvedValue(
      listResponse({ enabled: true, status, suggestions: [] }),
    );
    const view = renderHook(() => useHomeSuggestions());
    await waitFor(() => expect(view.result.current.view.status).toBe(status));
  },
);

function listResponse(
  body: unknown,
  status = 200,
  headers?: Record<string, string>,
) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  });
}

it('visiting home while signed out never fetches suggestions', async () => {
  const { result } = renderHook(() => useHomeSuggestions());
  expect(fetchMock).not.toHaveBeenCalled();
  expect(result.current.view.status).toBe('unloaded');
});

it('publishes a ready list only for the current sign-in and freezes dismissed rows in-memory', async () => {
  signIn('token-a');
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    expect(listUrl(url) || refreshUrl(url) || settingsUrl(url)).toBe(true);
    if (listUrl(url)) {
      return listResponse({
        enabled: true,
        status: 'ready',
        suggestions: [WIRE_ROW],
      });
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
  let hook!: ReturnType<typeof useHomeSuggestions>;
  const view = renderHook(() => {
    hook = useHomeSuggestions();
    return hook;
  });
  // The mount effect performs the single GET for this sign-in generation.
  await act(async () => {
    await vi.runOnlyPendingTimersAsync();
  });
  await waitFor(() => {
    expect(view.result.current.view.status).toBe('ready');
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(view.result.current.view.suggestions[0]?.id).toBe('s1');

  act(() => view.result.current.dismiss('s1'));
  await waitFor(() => {
    expect(view.result.current.view.status).toBe('dismissed');
    expect(view.result.current.view.suggestions).toHaveLength(0);
  });
  act(() => view.result.current.undoDismiss('s1'));
  await waitFor(() => {
    expect(view.result.current.view.status).toBe('ready');
    expect(view.result.current.view.suggestions[0]?.id).toBe('s1');
  });

  const storageWrites = vi.spyOn(
    Storage.prototype as unknown as Storage,
    'setItem',
  );
  expect(storageWrites).not.toHaveBeenCalled();
});

it('discards a response that arrives after the account changed and never follows up', async () => {
  signIn('token-a');
  let resolveFirst!: (value: Response) => void;
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (!listUrl(url)) {
      throw new Error(`unexpected fetch: ${url}`);
    }
    if (fetchMock.mock.calls.length === 1) {
      // First GET (token-a) stays pending past the account switch.
      return new Promise<Response>((resolve) => {
        resolveFirst = resolve;
      });
    }
    // The new sign-in gets its own fresh list read.
    return listResponse({ enabled: true, status: 'empty', suggestions: [] });
  });
  const view = renderHook(() => useHomeSuggestions());
  // Let the first GET's header check settle so it is genuinely pending.
  await act(async () => {
    await Promise.resolve();
  });
  // The account switches while the first GET is still pending.
  act(() => {
    signIn('token-b');
  });
  await act(async () => {
    await Promise.resolve();
  });
  expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
  // The stale token-a response arrives; it must never publish.
  await act(async () => {
    resolveFirst(
      listResponse({
        enabled: true,
        status: 'ready',
        suggestions: [WIRE_ROW],
      }),
    );
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_000);
  });
  expect(view.result.current.view.suggestions).toHaveLength(0);
  expect(view.result.current.view.status).toBe('empty');
  // No refresh or polling follow-up from the discarded chain happened.
  expect(
    fetchMock.mock.calls.filter(
      ([, init]) => (init as RequestInit)?.method === 'POST',
    ),
  ).toHaveLength(0);
});

it('keeps error and empty truthful and distinct', async () => {
  signIn('token-a');
  fetchMock.mockImplementation(async () =>
    listResponse({ enabled: true, status: 'empty', suggestions: [] }),
  );
  const view = renderHook(() => useHomeSuggestions());
  await waitFor(() => {
    expect(view.result.current.view.status).toBe('empty');
  });
  expect(view.result.current.view.message).toBeNull();

  signIn('token-a2');
  fetchMock.mockImplementation(async () => listResponse({}, 500));
  await act(async () => {
    await view.result.current.load();
  });
  await waitFor(() => {
    expect(view.result.current.view.status).toBe('error');
  });
  expect(view.result.current.view.message).toContain('could not be loaded');
});

it('hides expired server rows instead of serving stale private context', async () => {
  signIn('token-a');
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    if (listUrl(String(input))) {
      return listResponse({
        enabled: true,
        status: 'ready',
        suggestions: [
          {
            ...WIRE_ROW,
            expires_at: new Date(Date.now() - 1000).toISOString(),
          },
        ],
      });
    }
    throw new Error('unexpected');
  });
  const view = renderHook(() => useHomeSuggestions());
  await waitFor(() => {
    expect(view.result.current.view.status).toBe('expired');
  });
});

it('polls only while generating, with a bounded backoff and no inference GET after', async () => {
  signIn('token-a');
  let attempts = 0;
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    if (!listUrl(String(input))) {
      throw new Error(`unexpected fetch: ${String(input)}`);
    }
    attempts += 1;
    if (attempts <= 12) {
      return listResponse({
        enabled: true,
        status: 'generating',
        suggestions: [],
      });
    }
    throw new Error('polling not being stopped');
  });
  const view = renderHook(() => useHomeSuggestions());
  await waitFor(() =>
    expect(view.result.current.view.status).toBe('generating'),
  );
  let guard = 0;
  while (view.result.current.view.status === 'generating' && guard < 40) {
    guard += 1;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
  }
  expect(view.result.current.view.status).toBe('unavailable');
  expect(view.result.current.view.message).toContain('taking longer');
  // Cap: initial GET + 10 attempt GETs.
  expect(
    fetchMock.mock.calls.filter(
      ([, init]) => ((init as RequestInit)?.method ?? 'GET') === 'GET',
    ),
  ).toHaveLength(11);
});

it('enabling PATCHes a strict boolean once and then issues exactly one refresh', async () => {
  signIn('token-a');
  const urls: string[] = [];
  fetchMock.mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      urls.push(url);
      if (settingsUrl(url)) {
        expect(init?.method).toBe('PATCH');
        const body = JSON.parse(String(init?.body));
        expect(body.preferences.home_suggestions_enabled).toBe(true);
        return listResponse({ preferences: {} }, 200);
      }
      if (refreshUrl(url)) {
        return new Response('{"status":"queued"}', {
          status: 202,
          headers: { 'content-type': 'application/json' },
        });
      }
      if (listUrl(url)) {
        return listResponse({
          enabled: true,
          status: 'ready',
          suggestions: [WIRE_ROW],
        });
      }
      throw new Error(`unexpected fetch: ${url}`);
    },
  );
  const view = renderHook(() => useHomeSuggestions());
  const enabled = await act(async () => view.result.current.enable());
  // Only one explicit refresh (202) plus its polling GET.
  const refreshes = fetchMock.mock.calls.filter(
    ([, init]) => (init as RequestInit)?.method === 'POST',
  );
  expect(refreshes).toHaveLength(1);
  expect(enabled).toBe(true);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1500);
  });
  await waitFor(() => {
    expect(view.result.current.view.status).toBe('ready');
  });
});

it('disabling hides rows immediately and PATCHes the strict false preference', async () => {
  signIn('token-a');
  fetchMock.mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (settingsUrl(url)) {
        const body = JSON.parse(String(init?.body)) as {
          preferences: { home_suggestions_enabled: boolean };
        };
        expect(typeof body.preferences.home_suggestions_enabled).toBe(
          'boolean',
        );
        expect(body.preferences.home_suggestions_enabled).toBe(false);
        return listResponse({ preferences: {} }, 200);
      }
      if (listUrl(url)) {
        return listResponse({
          enabled: true,
          status: 'ready',
          suggestions: [WIRE_ROW],
        });
      }
      throw new Error(`unexpected fetch: ${url}`);
    },
  );
  const view = renderHook(() => useHomeSuggestions());
  await waitFor(() => {
    expect(view.result.current.view.status).toBe('ready');
  });
  const ok = await act(async () => view.result.current.disable());
  expect(ok).toBe(true);
  await waitFor(() => {
    expect(view.result.current.view.status).toBe('disabled');
  });
  expect(view.result.current.view.suggestions).toHaveLength(0);
});

it('a throttled refresh keeps live rows and reports the throttle truthfully', async () => {
  signIn('token-a');
  fetchMock.mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (refreshUrl(url)) {
        return listResponse(
          { detail: 'Suggestions were refreshed too recently.' },
          429,
        );
      }
      if (listUrl(url)) {
        return listResponse({
          enabled: true,
          status: 'ready',
          suggestions: [WIRE_ROW],
        });
      }
      throw new Error(`unexpected fetch: ${url}`);
    },
  );
  const view = renderHook(() => useHomeSuggestions());
  await waitFor(() => {
    expect(view.result.current.view.status).toBe('ready');
  });
  await act(async () => {
    await view.result.current.refresh();
  });
  // Rows stay visible; no invented replacement.
  expect(view.result.current.view.suggestions).toHaveLength(1);
  expect(view.result.current.view.status).toBe('ready');
  expect(view.result.current.view.message).toContain('too recently');
});
