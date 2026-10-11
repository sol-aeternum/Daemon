import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  AUTH_COOKIE_LOCK,
  AUTH_COORDINATION_KEY,
  cancelAuthCookieOperations,
  readAuthCoordination,
  runAuthCookieTransaction,
  subscribeAuthCoordination,
  type AuthCoordinationRecord,
} from '../lib/authCookieCoordinator';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

// A FIFO model of native Web Locks: abort removes a queued request, but cannot
// release a held lock while its callback's browser transport still runs.
class TestLocks {
  held = false;
  queue: Array<() => Promise<void>> = [];
  request = vi.fn(
    <T>(
      name: string,
      options: { signal: AbortSignal },
      action: () => Promise<T>,
    ) => {
      expect(name).toBe(AUTH_COOKIE_LOCK);
      const result = deferred<T>();
      const start = async () => {
        if (options.signal.aborted) {
          result.reject(options.signal.reason);
          return;
        }
        this.held = true;
        options.signal.removeEventListener('abort', cancel);
        try {
          result.resolve(await action());
        } catch (error) {
          result.reject(error);
        } finally {
          this.held = false;
          this.drain();
        }
      };
      const cancel = () => {
        this.queue = this.queue.filter((item) => item !== start);
        result.reject(options.signal.reason);
      };
      options.signal.addEventListener('abort', cancel, { once: true });
      this.queue.push(start);
      this.drain();
      return result.promise;
    },
  );
  drain() {
    if (!this.held) void this.queue.shift()?.();
  }
}

let locks: TestLocks;
let values: Map<string, string>;
let storage: {
  getItem: ReturnType<typeof vi.fn>;
  setItem: ReturnType<typeof vi.fn>;
};
const stable = (epoch = 'fictional-epoch-a'): AuthCoordinationRecord => ({
  version: 1,
  epoch,
  status: 'stable',
});
const setRecord = (record: AuthCoordinationRecord) =>
  values.set(AUTH_COORDINATION_KEY, JSON.stringify(record));
const flush = async () => {
  for (let i = 0; i < 12; i++) await Promise.resolve();
};

beforeEach(() => {
  locks = new TestLocks();
  values = new Map();
  storage = {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      values.set(key, value);
    }),
  };
  vi.stubGlobal('localStorage', storage);
  vi.stubGlobal('navigator', { locks });
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('{}', { status: 200 })),
  );
  setRecord(stable());
});
afterEach(async () => {
  cancelAuthCookieOperations();
  await flush();
  expect(locks.held).toBe(false);
  expect(locks.queue).toHaveLength(0);
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('cookie transaction record and exclusion', () => {
  it('owned local commit runs after verified shared finalization, before observers and lock release', async () => {
    let installed = false;
    let committedEpoch = '';
    const observations: boolean[] = [];
    const stop = subscribeAuthCoordination(() => {
      if (readAuthCoordination()?.status === 'stable')
        observations.push(installed);
    });
    await runAuthCookieTransaction(
      'sign-in',
      { epoch: stable().epoch, replaceIdentity: true },
      async (tx) => {
        await tx.request('/api/v1/auth/email/complete', { method: 'POST' });
        expect(tx.epoch).toBe(stable().epoch);
        tx.finish('stable', (epoch) => {
          committedEpoch = epoch;
          expect(locks.held).toBe(true);
          expect(readAuthCoordination()).toEqual(stable(epoch));
          expect(epoch).not.toBe(tx.epoch);
          installed = true;
        });
      },
    );
    stop();
    expect(observations).toEqual([true]);
    expect(readAuthCoordination()?.epoch).toBe(committedEpoch);
  });

  it('failed shared commit cannot install or expose a local identity', async () => {
    const install = vi.fn();
    await expect(
      runAuthCookieTransaction(
        'sign-in',
        { epoch: stable().epoch, replaceIdentity: true },
        async (tx) => {
          await tx.request('/api/v1/auth/email/complete', { method: 'POST' });
          tx.finish('stable', install);
          storage.setItem.mockImplementation(() => {
            throw new Error('fictional storage failure');
          });
        },
      ),
    ).rejects.toThrow('unavailable');
    expect(install).not.toHaveBeenCalled();
  });

  it('terminal cancellation cannot run an already registered local commit', async () => {
    const gate = deferred<void>();
    const install = vi.fn();
    const work = runAuthCookieTransaction(
      'refresh',
      { epoch: stable().epoch },
      async (tx) => {
        await tx.request('/api/v1/auth/refresh', { method: 'POST' });
        tx.finish('stable', install);
        await gate.promise;
      },
    );
    const rejected = expect(work).rejects.toThrow('Authentication changed');
    await flush();
    cancelAuthCookieOperations();
    await rejected;
    gate.resolve();
    await flush();
    expect(install).not.toHaveBeenCalled();
    expect(readAuthCoordination()?.status).toBe('uncertain');
  });
  it('holds owned pending metadata through response consumption, then finalizes', async () => {
    const gate = deferred<void>();
    const work = runAuthCookieTransaction(
      'refresh',
      { epoch: stable().epoch },
      async (tx) => {
        expect(readAuthCoordination()).toMatchObject({
          status: 'pending',
          epoch: tx.epoch,
          kind: 'refresh',
        });
        const response = await tx.request('/api/v1/auth/refresh', {
          method: 'POST',
          keepalive: true,
        });
        await response.json();
        await gate.promise;
        tx.finish('stable');
        return 'fictional result';
      },
    );
    await flush();
    expect(locks.held).toBe(true);
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/auth/refresh',
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        credentials: 'include',
        keepalive: false,
      }),
    );
    gate.resolve();
    expect(await work).toBe('fictional result');
    await flush();
    expect(readAuthCoordination()).toEqual(stable());
  });

  it('replaces identity with a new opaque epoch and persists no credentials', async () => {
    let epoch = '';
    await runAuthCookieTransaction(
      'sign-in',
      { epoch: stable().epoch, replaceIdentity: true },
      async (tx) => {
        await (
          await tx.request('/api/v1/auth/email/complete', { method: 'POST' })
        ).json();
        expect(readAuthCoordination()).toMatchObject({
          epoch: stable().epoch,
          status: 'pending',
        });
        tx.finish('stable', (committedEpoch) => {
          epoch = committedEpoch;
        });
      },
    );
    expect(epoch).not.toBe(stable().epoch);
    expect(readAuthCoordination()).toEqual(stable(epoch));
    expect(values.get(AUTH_COORDINATION_KEY)).not.toMatch(
      /access_token|refresh_token|account|Bearer/,
    );
  });

  it('identity rejection retains the same owned epoch and permits an old-epoch queued refresh', async () => {
    const gate = deferred<void>();
    const signIn = runAuthCookieTransaction(
      'sign-in',
      { epoch: stable().epoch, replaceIdentity: true },
      async (tx) => {
        await tx.request('/api/v1/auth/email/complete', { method: 'POST' });
        await gate.promise;
        tx.rejectIdentity();
      },
    );
    const refresh = runAuthCookieTransaction(
      'refresh',
      { epoch: stable().epoch },
      async (tx) => {
        await tx.request('/api/v1/auth/refresh', { method: 'POST' });
        tx.finish('stable');
      },
    );
    expect(readAuthCoordination()).toMatchObject({
      epoch: stable().epoch,
      status: 'pending',
    });
    gate.resolve();
    await signIn;
    await refresh;
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(readAuthCoordination()).toEqual(stable());
  });

  it.each(['uncertain', 'signed-out'] as const)(
    'verified credential rejection cannot promote prior %s',
    async (status) => {
      const prior: AuthCoordinationRecord = { ...stable(), status };
      setRecord(prior);
      await runAuthCookieTransaction(
        'sign-in',
        { epoch: prior.epoch, replaceIdentity: true },
        async (tx) => {
          await tx.request('/api/v1/auth/email/complete', { method: 'POST' });
          tx.rejectIdentity();
        },
      );
      expect(readAuthCoordination()).toEqual(prior);
    },
  );

  it('missing identity bootstrap publishes no stable state, and rejection leaves signed-out', async () => {
    values.clear();
    const states: string[] = [];
    const stop = subscribeAuthCoordination(() => {
      states.push(readAuthCoordination()?.status ?? 'missing');
    });
    await runAuthCookieTransaction(
      'setup',
      { epoch: null, replaceIdentity: true },
      async (tx) => {
        await tx.request('/api/v1/auth/setup', { method: 'POST' });
        tx.rejectIdentity();
      },
    );
    stop();
    expect(states).toEqual(['pending', 'signed-out']);
    expect(readAuthCoordination()?.status).toBe('signed-out');
  });

  it('missing identity cancelled before dispatch never invents stable readiness', async () => {
    values.clear();
    await expect(
      runAuthCookieTransaction(
        'setup',
        { epoch: null, replaceIdentity: true },
        async () => {
          throw new Error('fictional pre-dispatch failure');
        },
      ),
    ).rejects.toThrow('fictional pre-dispatch failure');
    expect(readAuthCoordination()?.status).toBe('signed-out');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('verified-rejection finalization is unavailable to refresh', async () => {
    await expect(
      runAuthCookieTransaction(
        'refresh',
        { epoch: stable().epoch },
        async (tx) => {
          tx.rejectIdentity();
        },
      ),
    ).rejects.toThrow('Authentication changed');
    expect(readAuthCoordination()).toEqual(stable());
    expect(fetch).not.toHaveBeenCalled();
  });

  it('refresh cannot request identity-replacement authority', async () => {
    await expect(
      runAuthCookieTransaction(
        'refresh',
        { epoch: stable().epoch, replaceIdentity: true },
        vi.fn(),
      ),
    ).rejects.toThrow('Authentication changed');
    expect(locks.request).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('registered identity rejection cannot restore state after terminal cancellation', async () => {
    const gate = deferred<void>();
    const restored = vi.fn();
    const work = runAuthCookieTransaction(
      'sign-in',
      { epoch: stable().epoch, replaceIdentity: true },
      async (tx) => {
        await tx.request('/api/v1/auth/email/complete', { method: 'POST' });
        tx.rejectIdentity(restored);
        await gate.promise;
      },
    );
    const rejected = expect(work).rejects.toThrow('Authentication changed');
    await flush();
    cancelAuthCookieOperations();
    await rejected;
    gate.resolve();
    await flush();
    expect(restored).not.toHaveBeenCalled();
    expect(readAuthCoordination()?.status).toBe('uncertain');
  });

  it('a queued old-epoch refresh cannot silently adopt the new sign-in epoch', async () => {
    const gate = deferred<void>();
    const signIn = runAuthCookieTransaction(
      'sign-in',
      { epoch: stable().epoch, replaceIdentity: true },
      async (tx) => {
        await tx.request('/api/v1/auth/email/complete', { method: 'POST' });
        await gate.promise;
        tx.finish('stable');
      },
    );
    const stale = runAuthCookieTransaction(
      'refresh',
      { epoch: stable().epoch },
      async (tx) => {
        await tx.request('/api/v1/auth/refresh', { method: 'POST' });
        tx.finish('stable');
      },
    );
    const rejected = expect(stale).rejects.toThrow('Authentication changed');
    gate.resolve();
    await signIn;
    await rejected;
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('a missing record is not permission for a previously bound token to bootstrap', async () => {
    values.clear();
    await expect(
      runAuthCookieTransaction(
        'refresh',
        { epoch: stable().epoch },
        async (tx) => {
          await tx.request('/api/v1/auth/refresh', { method: 'POST' });
        },
      ),
    ).rejects.toThrow('Authentication changed');
    expect(fetch).not.toHaveBeenCalled();
    expect(readAuthCoordination()).toBeNull();
  });

  it('a fresh unbound bootstrap creates its record under the lock before dispatch', async () => {
    values.clear();
    await runAuthCookieTransaction('refresh', { epoch: null }, async (tx) => {
      expect(locks.held).toBe(true);
      expect(readAuthCoordination()?.epoch).toBe(tx.epoch);
      await tx.request('/api/v1/auth/refresh', { method: 'POST' });
      tx.finish('stable');
    });
    expect(readAuthCoordination()?.status).toBe('stable');
  });

  it('an abandoned pending record quarantines sign-in rather than trusting lock release', async () => {
    const record: AuthCoordinationRecord = {
      ...stable(),
      status: 'pending',
      attempt: 'lost-attempt',
      kind: 'logout',
    };
    setRecord(record);
    const action = vi.fn();
    await expect(
      runAuthCookieTransaction(
        'sign-in',
        { epoch: record.epoch, replaceIdentity: true },
        action,
      ),
    ).rejects.toThrow('unconfirmed');
    expect(readAuthCoordination()).toEqual({ ...record, status: 'uncertain' });
    await expect(
      runAuthCookieTransaction(
        'sign-in',
        { epoch: record.epoch, replaceIdentity: true },
        action,
      ),
    ).rejects.toThrow('unconfirmed');
    expect(action).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(['uncertain', 'signed-out'] as const)(
    'cookie bootstrap cannot refresh from %s',
    async (status) => {
      setRecord({ ...stable(), status });
      await expect(
        runAuthCookieTransaction('refresh', { epoch: stable().epoch }, vi.fn()),
      ).rejects.toThrow('unconfirmed');
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it('explicit credentials can recover a TERMINATED uncertain attempt', async () => {
    setRecord({ ...stable(), status: 'uncertain' });
    await runAuthCookieTransaction(
      'sign-in',
      { epoch: stable().epoch, replaceIdentity: true },
      async (tx) => {
        await tx.request('/api/v1/auth/email/complete', { method: 'POST' });
        tx.finish('stable');
      },
    );
    expect(readAuthCoordination()?.status).toBe('stable');
    expect(readAuthCoordination()?.epoch).not.toBe(stable().epoch);
  });

  it('local mutation before dispatch restores only the owned previous record', async () => {
    let current = true;
    await expect(
      runAuthCookieTransaction(
        'refresh',
        { epoch: stable().epoch, isCurrent: () => current },
        async (tx) => {
          current = false;
          await tx.request('/api/v1/auth/refresh', { method: 'POST' });
        },
      ),
    ).rejects.toThrow('Authentication changed');
    expect(fetch).not.toHaveBeenCalled();
    expect(readAuthCoordination()).toEqual(stable());
  });

  it('external record removal fences late completion without recreating readiness', async () => {
    await expect(
      runAuthCookieTransaction(
        'refresh',
        { epoch: stable().epoch },
        async (tx) => {
          await tx.request('/api/v1/auth/refresh', { method: 'POST' });
          values.clear();
          tx.finish('stable');
        },
      ),
    ).rejects.toThrow('Authentication changed');
    expect(readAuthCoordination()).toBeNull();
  });
});

describe('deadline and cancellation ownership', () => {
  it('queued timeout does not enter or steal a frozen holder lock', async () => {
    vi.useFakeTimers();
    locks.held = true;
    const work = runAuthCookieTransaction(
      'refresh',
      { epoch: stable().epoch },
      vi.fn(),
    );
    const rejected = expect(work).rejects.toThrow('timed out');
    await vi.advanceTimersByTimeAsync(10_000);
    await rejected;
    expect(locks.held).toBe(true);
    expect(locks.queue).toHaveLength(0);
    expect(readAuthCoordination()).toEqual(stable());
    expect(fetch).not.toHaveBeenCalled();
    locks.held = false;
  });

  it('native-style abort releases only after termination and permits later B sign-in', async () => {
    vi.useFakeTimers();
    vi.mocked(fetch).mockImplementationOnce(
      (_url, init) =>
        new Promise((_yes, no) => {
          init!.signal!.addEventListener(
            'abort',
            () => no(init!.signal!.reason),
            { once: true },
          );
        }),
    );
    const old = runAuthCookieTransaction(
      'refresh',
      { epoch: stable().epoch },
      async (tx) => {
        await tx.request('/api/v1/auth/refresh', { method: 'POST' });
        tx.finish('stable');
      },
    );
    const rejected = expect(old).rejects.toThrow('timed out');
    await vi.advanceTimersByTimeAsync(10_000);
    await rejected;
    await flush();
    expect(locks.held).toBe(false);
    expect(readAuthCoordination()).toEqual({
      ...stable(),
      status: 'uncertain',
    });
    await runAuthCookieTransaction(
      'sign-in',
      { epoch: stable().epoch, replaceIdentity: true },
      async (tx) => {
        await tx.request('/api/v1/auth/email/complete', { method: 'POST' });
        tx.finish('stable');
      },
    );
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(readAuthCoordination()?.status).toBe('stable');
  });

  it('abort-ignoring fetch settles observer but keeps exclusion until its own termination', async () => {
    vi.useFakeTimers();
    const transport = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(transport.promise);
    const old = runAuthCookieTransaction(
      'refresh',
      { epoch: stable().epoch },
      async (tx) => {
        await tx.request('/api/v1/auth/refresh', { method: 'POST' });
        tx.finish('stable');
      },
    );
    const rejected = expect(old).rejects.toThrow('timed out');
    await vi.advanceTimersByTimeAsync(10_000);
    await rejected;
    expect(locks.held).toBe(true);
    expect(readAuthCoordination()).toMatchObject({
      status: 'uncertain',
      attempt: expect.any(String),
    });
    const b = runAuthCookieTransaction(
      'sign-in',
      { epoch: stable().epoch, replaceIdentity: true },
      vi.fn(),
    );
    const blocked = expect(b).rejects.toThrow('timed out');
    await vi.advanceTimersByTimeAsync(10_000);
    await blocked;
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(locks.held).toBe(true);
    // Terminate the fixture rather than resetting the production module.
    transport.resolve(new Response('{}'));
    await flush();
    expect(locks.held).toBe(false);
    expect(readAuthCoordination()).toEqual({
      ...stable(),
      status: 'uncertain',
    });
  });

  it('a body hang retains ownership through abort settlement', async () => {
    vi.useFakeTimers();
    vi.mocked(fetch).mockImplementationOnce(async (_url, init) => {
      const body = deferred<unknown>();
      init!.signal!.addEventListener(
        'abort',
        () => body.reject(init!.signal!.reason),
        { once: true },
      );
      return { arrayBuffer: () => body.promise } as Response;
    });
    const old = runAuthCookieTransaction(
      'refresh',
      { epoch: stable().epoch },
      async (tx) => {
        await (
          await tx.request('/api/v1/auth/refresh', { method: 'POST' })
        ).json();
        tx.finish('stable');
      },
    );
    const rejected = expect(old).rejects.toThrow('timed out');
    await vi.advanceTimersByTimeAsync(10_000);
    await rejected;
    await flush();
    expect(readAuthCoordination()).toEqual({
      ...stable(),
      status: 'uncertain',
    });
    expect(locks.held).toBe(false);
  });

  it('pre-aborted owner request performs no storage mutation or fetch', async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      runAuthCookieTransaction(
        'sign-in',
        {
          epoch: stable().epoch,
          replaceIdentity: true,
          signal: controller.signal,
        },
        vi.fn(),
      ),
    ).rejects.toThrow('Authentication changed');
    expect(locks.request).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
    expect(readAuthCoordination()).toEqual(stable());
  });

  it('pagehide cancels owned work even without a UI subscription', async () => {
    vi.mocked(fetch).mockImplementationOnce(
      (_url, init) =>
        new Promise((_yes, no) => {
          init!.signal!.addEventListener(
            'abort',
            () => no(init!.signal!.reason),
            { once: true },
          );
        }),
    );
    const work = runAuthCookieTransaction(
      'refresh',
      { epoch: stable().epoch },
      async (tx) => {
        await tx.request('/api/v1/auth/refresh', { method: 'POST' });
        tx.finish('stable');
      },
    );
    const rejected = expect(work).rejects.toThrow('Authentication changed');
    window.dispatchEvent(new Event('pagehide'));
    await rejected;
    await flush();
    expect(locks.held).toBe(false);
    expect(readAuthCoordination()).toEqual({
      ...stable(),
      status: 'uncertain',
    });
  });

  it('unawaited response bodies still own exclusion until transport terminates', async () => {
    const body = deferred<ArrayBuffer>();
    vi.mocked(fetch).mockResolvedValueOnce({
      arrayBuffer: () => body.promise,
      status: 200,
      statusText: 'OK',
      headers: new Headers(),
    } as Response);
    const work = runAuthCookieTransaction(
      'refresh',
      { epoch: stable().epoch },
      async (tx) => {
        void tx.request('/api/v1/auth/refresh', { method: 'POST' });
        tx.finish('stable');
      },
    );
    await flush();
    expect(locks.held).toBe(true);
    expect(readAuthCoordination()?.status).toBe('pending');
    body.resolve(new ArrayBuffer(0));
    await work;
    expect(readAuthCoordination()).toEqual(stable());
  });

  it('action failure waits for its outstanding browser transport before unlock', async () => {
    const transport = deferred<Response>();
    vi.mocked(fetch).mockReturnValueOnce(transport.promise);
    const work = runAuthCookieTransaction(
      'refresh',
      { epoch: stable().epoch },
      async (tx) => {
        void tx.request('/api/v1/auth/refresh', { method: 'POST' });
        throw new Error('fictional action failed');
      },
    );
    const rejected = expect(work).rejects.toThrow('fictional action failed');
    await rejected;
    expect(locks.held).toBe(true);
    expect(readAuthCoordination()).toMatchObject({
      status: 'uncertain',
      attempt: expect.any(String),
    });
    transport.resolve(new Response('{}'));
    await flush();
    expect(locks.held).toBe(false);
    expect(readAuthCoordination()).toEqual({
      ...stable(),
      status: 'uncertain',
    });
  });

  it('confirmed sign-out establishes a fresh shared epoch', async () => {
    await runAuthCookieTransaction(
      'logout',
      { epoch: stable().epoch },
      async (tx) => {
        await tx.request('/api/v1/auth/logout', { method: 'POST' });
        tx.finish('signed-out');
      },
    );
    expect(readAuthCoordination()?.status).toBe('signed-out');
    expect(readAuthCoordination()?.epoch).not.toBe(stable().epoch);
  });

  it('checks an elapsed absolute deadline before dispatch even if the timer has not fired', async () => {
    await expect(
      runAuthCookieTransaction(
        'refresh',
        { epoch: stable().epoch, deadline: Date.now() - 1 },
        vi.fn(),
      ),
    ).rejects.toThrow('timed out');
    expect(fetch).not.toHaveBeenCalled();
    expect(locks.request).not.toHaveBeenCalled();
  });
});

describe('fail closed, without fallback', () => {
  it('unsupported Web Locks makes zero requests', async () => {
    vi.stubGlobal('navigator', {});
    await expect(
      runAuthCookieTransaction('refresh', { epoch: stable().epoch }, vi.fn()),
    ).rejects.toThrow('unavailable');
    expect(fetch).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it('missing randomUUID is a clear unsupported-capability error before locking', async () => {
    vi.stubGlobal('crypto', {});
    await expect(
      runAuthCookieTransaction('refresh', { epoch: stable().epoch }, vi.fn()),
    ).rejects.toThrow('unavailable');
    expect(locks.request).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it('rejects a stale epoch at enqueue without waiting behind a held lock', async () => {
    locks.held = true;
    setRecord(stable('fictional-epoch-b'));
    await expect(
      runAuthCookieTransaction('refresh', { epoch: stable().epoch }, vi.fn()),
    ).rejects.toThrow('Authentication changed');
    expect(locks.request).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
    locks.held = false;
  });

  it('also rechecks an initially matching epoch after acquiring a queued lock', async () => {
    locks.held = true;
    const work = runAuthCookieTransaction(
      'refresh',
      { epoch: stable().epoch },
      vi.fn(),
    );
    const rejected = expect(work).rejects.toThrow('Authentication changed');
    expect(locks.queue).toHaveLength(1);
    // The current holder finalizes a different lifetime before releasing.
    setRecord(stable('fictional-epoch-b'));
    locks.held = false;
    locks.drain();
    await rejected;
    expect(fetch).not.toHaveBeenCalled();
    expect(readAuthCoordination()).toEqual(stable('fictional-epoch-b'));
  });

  it('Web Lock errors never fall through into a lease-based request', async () => {
    locks.request.mockRejectedValueOnce(new Error('Lock unavailable'));
    await expect(
      runAuthCookieTransaction('refresh', { epoch: stable().epoch }, vi.fn()),
    ).rejects.toThrow('Lock unavailable');
    expect(fetch).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it.each(['read', 'write', 'silent-write'] as const)(
    'unusable shared storage (%s) prevents dispatch',
    async (failure) => {
      if (failure === 'read')
        storage.getItem.mockImplementation(() => {
          throw new Error('fictional storage error');
        });
      if (failure === 'write')
        storage.setItem.mockImplementation(() => {
          throw new Error('fictional storage error');
        });
      if (failure === 'silent-write')
        storage.setItem.mockImplementation(() => {});
      await expect(
        runAuthCookieTransaction('refresh', { epoch: stable().epoch }, vi.fn()),
      ).rejects.toThrow('unavailable');
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it.each([
    'not-json',
    'null',
    '[]',
    '{}',
    JSON.stringify({ ...stable(), version: 2 }),
    JSON.stringify({ ...stable(), epoch: '' }),
    JSON.stringify({ ...stable(), status: 'other' }),
    JSON.stringify({ ...stable(), status: 'pending' }),
    JSON.stringify({ ...stable(), attempt: 'unowned', kind: 'refresh' }),
    JSON.stringify({ ...stable(), status: 'uncertain', attempt: 'lost' }),
    JSON.stringify({
      ...stable(),
      status: 'pending',
      attempt: 'lost',
      kind: 'unknown',
    }),
    JSON.stringify({
      ...stable(),
      access_token: 'fictional-must-not-be-stored',
    }),
  ])(
    'rejects malformed or expanded metadata before fetching (%#)',
    async (raw) => {
      values.set(AUTH_COORDINATION_KEY, raw);
      await expect(
        runAuthCookieTransaction('refresh', { epoch: stable().epoch }, vi.fn()),
      ).rejects.toThrow('unavailable');
      expect(fetch).not.toHaveBeenCalled();
      expect(locks.request).not.toHaveBeenCalled();
    },
  );

  it('storage clear and visibility restoration notify shared-lifetime consumers', () => {
    const listener = vi.fn();
    const stop = subscribeAuthCoordination(listener);
    window.dispatchEvent(new StorageEvent('storage', { key: null }));
    document.dispatchEvent(new Event('visibilitychange'));
    expect(listener).toHaveBeenCalledTimes(2);
    stop();
    window.dispatchEvent(
      new StorageEvent('storage', { key: AUTH_COORDINATION_KEY }),
    );
    expect(listener).toHaveBeenCalledTimes(2);
  });
});
