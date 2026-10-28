import { afterEach, describe, expect, it, vi } from 'vitest';
import { HistoryScope, readScopedHistory } from '../lib/historyScope';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const response = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });
const auth = async () => ({ Authorization: 'Bearer fictional-test-only' });
const urls = ['https://example.invalid/conversations', '/conversations'];

afterEach(() => vi.useRealTimers());

describe('history request scopes', () => {
  it('leaves same-generation readiness and same committed selection alone', () => {
    const scope = new HistoryScope(() => 1, 'a');
    const lease = scope.capture();
    expect(scope.syncGeneration()).toBe(false);
    scope.commitSelection('a');
    expect(lease.isCurrent()).toBe(true);
    lease.release();
    lease.release();
    expect(lease.isCurrent()).toBe(false);
  });

  it('rejects A-B-A including null transitions, without cancelling account-list work', () => {
    const scope = new HistoryScope(() => 1, 'a');
    const list = scope.capture({ selected: false });
    const a = scope.capture();
    scope.commitSelection('b');
    scope.commitSelection('a');
    const aAgain = scope.capture();
    expect(a.signal.aborted).toBe(true);
    expect(aAgain.selectionEpoch).not.toBe(a.selectionEpoch);
    expect(aAgain.isCurrent()).toBe(true);
    scope.commitSelection(null);
    scope.commitSelection('a');
    expect(aAgain.isCurrent()).toBe(false);
    expect(list.isCurrent()).toBe(true);
    scope.dispose();
  });

  it('invalidates every family on actual auth changes, even without a notification', () => {
    let generation = 1;
    const scope = new HistoryScope(() => generation, 'same-url');
    const detail = scope.capture();
    const list = scope.capture({ selected: false, family: 'list' });
    generation += 1;
    expect(detail.isCurrent()).toBe(false);
    expect(list.isCurrent()).toBe(false);
    expect(list.signal.aborted).toBe(true);
    expect(scope.capture().generation).toBe(2);
    scope.dispose();
  });

  it('invalidates a query before debounce, independently of other jobs', () => {
    const scope = new HistoryScope(() => 1, 'a');
    const search = scope.capture({ family: 'search', selected: false });
    const detail = scope.capture();
    scope.invalidateFamily('search');
    expect(search.isCurrent()).toBe(false);
    expect(detail.isCurrent()).toBe(true);
    scope.dispose();
  });

  it('releasing an older request cannot invalidate its replacement', () => {
    const scope = new HistoryScope(() => 1, 'a');
    const old = scope.capture({ family: 'detail' });
    const fresh = scope.capture({ family: 'detail' });
    expect(old.isCurrent()).toBe(false);
    old.release();
    expect(fresh.isCurrent()).toBe(true);
    scope.invalidateFamily('detail');
    expect(fresh.isCurrent()).toBe(false);
    scope.dispose();
  });

  it('propagates caller abort, already-aborted signals and disposal', () => {
    const scope = new HistoryScope(() => 1, 'a');
    const controller = new AbortController();
    const lease = scope.capture({ signal: controller.signal });
    controller.abort();
    expect(lease.isCurrent()).toBe(false);
    expect(scope.capture({ signal: controller.signal }).signal.aborted).toBe(
      true,
    );
    scope.dispose();
    const closed = scope.capture();
    expect(closed.isCurrent()).toBe(false);
    expect(() => closed.assertCurrent()).toThrow(/no longer current/);
    closed.release();
  });
});

describe('scoped GET transport boundaries', () => {
  it.each(['account', 'selection', 'caller'] as const)(
    'does not fetch when %s changes during authentication',
    async (change) => {
      let generation = 1;
      const scope = new HistoryScope(() => generation, 'a');
      const controller = new AbortController();
      const lease = scope.capture({ signal: controller.signal });
      const headers = deferred<Record<string, string>>();
      const fetcher = vi.fn<typeof fetch>();
      const read = readScopedHistory(lease, urls, {
        getAuthHeaders: () => headers.promise,
        fetch: fetcher,
      });
      const rejected = expect(read).rejects.toMatchObject({
        name: 'AbortError',
      });
      if (change === 'account') generation += 1;
      if (change === 'selection') {
        scope.commitSelection('b');
        scope.commitSelection('a');
      }
      if (change === 'caller') controller.abort();
      headers.resolve(await auth());
      await rejected;
      expect(fetcher).not.toHaveBeenCalled();
      lease.release();
    },
  );

  it.each(['account', 'selection', 'caller'] as const)(
    'does not parse or fall back when %s changes during fetch',
    async (change) => {
      let generation = 1;
      const scope = new HistoryScope(() => generation, 'a');
      const controller = new AbortController();
      const lease = scope.capture({ signal: controller.signal });
      const fetched = deferred<Response>();
      const json = vi.fn(async () => ({}));
      const fetcher = vi
        .fn<typeof fetch>()
        .mockImplementation(() => fetched.promise);
      const read = readScopedHistory(lease, urls, {
        getAuthHeaders: auth,
        fetch: fetcher,
      });
      const rejected = expect(read).rejects.toMatchObject({
        name: 'AbortError',
      });
      await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
      if (change === 'account') generation += 1;
      if (change === 'selection') {
        scope.commitSelection('b');
        scope.commitSelection('a');
      }
      if (change === 'caller') controller.abort();
      fetched.resolve({ ok: true, status: 200, json } as unknown as Response);
      await rejected;
      expect(json).not.toHaveBeenCalled();
      expect(fetcher).toHaveBeenCalledOnce();
      lease.release();
    },
  );

  it.each(['account', 'selection', 'caller'] as const)(
    'rejects %s changes during body parsing',
    async (change) => {
      let generation = 1;
      const scope = new HistoryScope(() => generation, 'a');
      const controller = new AbortController();
      const lease = scope.capture({ signal: controller.signal });
      const body = deferred<unknown>();
      const json = vi.fn(() => body.promise);
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue({
        ok: true,
        status: 200,
        json,
      } as unknown as Response);
      const read = readScopedHistory(lease, urls, {
        getAuthHeaders: auth,
        fetch: fetcher,
      });
      const rejected = expect(read).rejects.toMatchObject({
        name: 'AbortError',
      });
      await vi.waitFor(() => expect(json).toHaveBeenCalledOnce());
      if (change === 'account') generation += 1;
      if (change === 'selection') {
        scope.commitSelection('b');
        scope.commitSelection('a');
      }
      if (change === 'caller') controller.abort();
      body.resolve({ synthetic: true });
      await rejected;
      expect(fetcher).toHaveBeenCalledOnce();
      lease.release();
    },
  );

  it('retains the lease after return so a deferred publication can reject stale data', async () => {
    let generation = 1;
    const scope = new HistoryScope(() => generation, 'a');
    const lease = scope.capture();
    const result = await readScopedHistory(lease, urls, {
      getAuthHeaders: auth,
      fetch: vi.fn<typeof fetch>().mockResolvedValue(response({ rows: [] })),
    });
    expect(result).toEqual({ status: 200, data: { rows: [] } });
    expect(lease.isCurrent()).toBe(true);
    const publication = vi.fn();
    const deferredUpdater = () => {
      if (lease.isCurrent()) publication(result.data);
    };
    generation += 1;
    deferredUpdater();
    expect(publication).not.toHaveBeenCalled();
    lease.release();
  });

  it('uses the next GET candidate for endpoint 404, not for authoritative 403', async () => {
    const scope = new HistoryScope(() => 1, 'a');
    const lease = scope.capture();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(response(null, 404))
      .mockResolvedValue(response(null, 403));
    const result = await readScopedHistory(lease, urls, {
      getAuthHeaders: auth,
      fetch: fetcher,
    });
    expect(result).toEqual({ status: 403, data: null });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(
      true,
    );
    lease.release();
  });

  it('cancels the actual fetch signal and never retries intentional cancellation', async () => {
    const scope = new HistoryScope(() => 1, 'a');
    const controller = new AbortController();
    const lease = scope.capture({ signal: controller.signal });
    let signal: AbortSignal | undefined;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          signal = init?.signal ?? undefined;
          signal?.addEventListener('abort', () => reject(signal?.reason));
        }),
    );
    const read = readScopedHistory(lease, urls, {
      getAuthHeaders: auth,
      fetch: fetcher,
    });
    const rejected = expect(read).rejects.toMatchObject({ name: 'AbortError' });
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    controller.abort();
    await rejected;
    expect(signal?.aborted).toBe(true);
    expect(fetcher).toHaveBeenCalledOnce();
    lease.release();
  });

  it('keeps the timeout active while parsing, including a body ignoring abort', async () => {
    vi.useFakeTimers();
    const scope = new HistoryScope(() => 1, 'a');
    const lease = scope.capture();
    const body = deferred<unknown>();
    const json = vi.fn(() => body.promise);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue({
      ok: true,
      status: 200,
      json,
    } as unknown as Response);
    const read = readScopedHistory(lease, [urls[0]], {
      getAuthHeaders: auth,
      fetch: fetcher,
      timeoutMs: 10,
    });
    const rejected = expect(read).rejects.toMatchObject({
      name: 'TimeoutError',
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(json).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(11);
    body.resolve({});
    await rejected;
    expect(lease.isCurrent()).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    lease.release();
  });

  it('can fall back after timeout without weakening the original scope', async () => {
    vi.useFakeTimers();
    const scope = new HistoryScope(() => 1, 'a');
    const lease = scope.capture();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementationOnce(
        (_url, init) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () =>
              reject(init.signal?.reason),
            );
          }),
      )
      .mockResolvedValue(response({ synthetic: true }));
    const read = readScopedHistory(lease, urls, {
      getAuthHeaders: auth,
      fetch: fetcher,
      timeoutMs: 10,
    });
    await vi.advanceTimersByTimeAsync(11);
    expect(await read).toEqual({ status: 200, data: { synthetic: true } });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
    lease.release();
  });

  it('does not fall back after cancellation races an endpoint-404 response', async () => {
    const scope = new HistoryScope(() => 1, 'a');
    const lease = scope.capture();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => {
      scope.commitSelection('b');
      return response(null, 404);
    });
    await expect(
      readScopedHistory(lease, urls, { getAuthHeaders: auth, fetch: fetcher }),
    ).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetcher).toHaveBeenCalledOnce();
    lease.release();
  });
});
