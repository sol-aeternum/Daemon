// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAuthenticatedImageUrl } from '@/hooks/useAuthenticatedImageUrl';

const auth = vi.hoisted(() => ({
  generation: 1,
  listeners: new Set<() => void>(),
  ensureHeader: vi.fn<() => Promise<string | null>>(),
}));

vi.mock('@/lib/auth', () => ({
  ensureAuthHeader: auth.ensureHeader,
  getAuthGeneration: () => auth.generation,
  subscribeAuthGeneration: (listener: () => void) => {
    auth.listeners.add(listener);
    return () => auth.listeners.delete(listener);
  },
}));

function replaceLifetime() {
  auth.generation += 1;
  for (const listener of auth.listeners) listener();
}

beforeEach(() => {
  auth.generation = 1;
  auth.ensureHeader.mockReset().mockResolvedValue('Bearer fictional-A');
  vi.stubGlobal('fetch', vi.fn());
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = vi.fn(() => 'blob:fictional-A');
      static revokeObjectURL = vi.fn();
    },
  );
});

afterEach(() => {
  cleanup();
  expect(auth.listeners.size).toBe(0);
  vi.unstubAllGlobals();
});

it('never publishes an A blob completed after the auth lifetime changes while the URL stays mounted', async () => {
  let finishBody!: (blob: Blob) => void;
  const body = new Promise<Blob>((resolve) => {
    finishBody = resolve;
  });
  const readBody = vi.fn(() => body);
  vi.mocked(fetch).mockResolvedValue({
    ok: true,
    blob: readBody,
  } as unknown as Response);
  const hook = renderHook(() =>
    useAuthenticatedImageUrl('/generated-images/reused.png'),
  );
  await waitFor(() => expect(readBody).toHaveBeenCalledOnce());
  expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toEqual({
    Authorization: 'Bearer fictional-A',
  });

  await act(async () => {
    replaceLifetime();
    finishBody(new Blob(['fictional private A bytes']));
    await body;
  });

  expect(hook.result.current.displayUrl).toBeNull();
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(true);
  expect(fetch).toHaveBeenCalledOnce();
});

it('does not start a request when the lifetime changes during header acquisition', async () => {
  let finishHeader!: (value: string) => void;
  auth.ensureHeader.mockReturnValue(
    new Promise((resolve) => {
      finishHeader = resolve;
    }),
  );
  const hook = renderHook(() =>
    useAuthenticatedImageUrl('/generated-images/a.png'),
  );
  await act(async () => {
    replaceLifetime();
    finishHeader('Bearer fictional-A');
  });
  expect(fetch).not.toHaveBeenCalled();
  expect(hook.result.current).toEqual({
    displayUrl: null,
    loading: false,
    error: false,
  });
});

it('does not read a late response body after the lifetime changes', async () => {
  let finishFetch!: (value: Response) => void;
  vi.mocked(fetch).mockReturnValue(
    new Promise((resolve) => {
      finishFetch = resolve;
    }),
  );
  const body = vi.fn();
  const hook = renderHook(() =>
    useAuthenticatedImageUrl('/generated-images/a.png'),
  );
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  await act(async () => {
    replaceLifetime();
    finishFetch({ ok: true, blob: body } as unknown as Response);
  });
  expect(body).not.toHaveBeenCalled();
  expect(hook.result.current.displayUrl).toBeNull();
});

it('revokes displayed media once and stays empty until a URL change starts B work', async () => {
  vi.mocked(fetch).mockImplementation(
    async () => new Response(new Blob(['fictional bytes'])),
  );
  const hook = renderHook(({ url }) => useAuthenticatedImageUrl(url), {
    initialProps: { url: '/generated-images/a.png' },
  });
  await waitFor(() =>
    expect(hook.result.current.displayUrl).toBe('blob:fictional-A'),
  );
  act(() => replaceLifetime());
  expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith(
    'blob:fictional-A',
  );
  expect(hook.result.current).toEqual({
    displayUrl: null,
    loading: false,
    error: false,
  });
  hook.rerender({ url: '/generated-images/a.png' });
  expect(fetch).toHaveBeenCalledOnce();
  auth.ensureHeader.mockResolvedValue('Bearer fictional-B');
  vi.mocked(URL.createObjectURL).mockReturnValue('blob:fictional-B');
  hook.rerender({ url: '/generated-images/b.png' });
  await waitFor(() =>
    expect(hook.result.current.displayUrl).toBe('blob:fictional-B'),
  );
  expect(vi.mocked(fetch).mock.calls[1][1]?.headers).toEqual({
    Authorization: 'Bearer fictional-B',
  });
  hook.unmount();
  expect(vi.mocked(URL.revokeObjectURL).mock.calls).toEqual([
    ['blob:fictional-A'],
    ['blob:fictional-B'],
  ]);
});

it('keeps displayed media for unchanged-lifetime notifications', async () => {
  vi.mocked(fetch).mockImplementation(
    async () => new Response(new Blob(['fictional bytes'])),
  );
  const hook = renderHook(() =>
    useAuthenticatedImageUrl('/generated-images/a.png'),
  );
  await waitFor(() =>
    expect(hook.result.current.displayUrl).toBe('blob:fictional-A'),
  );
  act(() => {
    for (const listener of auth.listeners) listener();
  });
  expect(hook.result.current.displayUrl).toBe('blob:fictional-A');
  expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(false);
});

it('ignores a rejected stale body without overwriting cleared state', async () => {
  let failBody!: (error: Error) => void;
  const body = new Promise<Blob>((_resolve, reject) => {
    failBody = reject;
  });
  const readBody = vi.fn(() => body);
  vi.mocked(fetch).mockResolvedValue({
    ok: true,
    blob: readBody,
  } as unknown as Response);
  const hook = renderHook(() =>
    useAuthenticatedImageUrl('/generated-images/a.png'),
  );
  await waitFor(() => expect(readBody).toHaveBeenCalledOnce());
  await act(async () => {
    replaceLifetime();
    failBody(new Error('fictional delayed failure'));
  });
  expect(hook.result.current).toEqual({
    displayUrl: null,
    loading: false,
    error: false,
  });
});

it('keeps external media outside protected-media fetching and revocation', () => {
  const hook = renderHook(() =>
    useAuthenticatedImageUrl('https://example.invalid/public.png'),
  );
  act(() => replaceLifetime());
  expect(hook.result.current.displayUrl).toBe(
    'https://example.invalid/public.png',
  );
  expect(auth.ensureHeader).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
  hook.unmount();
  expect(URL.revokeObjectURL).not.toHaveBeenCalled();
});

it('keeps an accepted owned URL across completion rerenders, then releases it only on a URL transition', async () => {
  vi.mocked(fetch).mockImplementation(
    async () => new Response(new Blob(['fictional bytes'])),
  );
  const hook = renderHook(({ url }) => useAuthenticatedImageUrl(url), {
    initialProps: { url: '/generated-images/a.png' as string | null },
  });
  await waitFor(() =>
    expect(hook.result.current.displayUrl).toBe('blob:fictional-A'),
  );
  hook.rerender({ url: '/generated-images/a.png' });
  hook.rerender({ url: '/generated-images/a.png' });
  expect(fetch).toHaveBeenCalledOnce();
  expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  hook.rerender({ url: 'https://example.invalid/borrowed.png' });
  expect(hook.result.current.displayUrl).toBe(
    'https://example.invalid/borrowed.png',
  );
  expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith(
    'blob:fictional-A',
  );
  hook.rerender({ url: null });
  expect(hook.result.current).toEqual({
    displayUrl: null,
    loading: false,
    error: false,
  });
  hook.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
});
