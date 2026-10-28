import { afterEach, describe, expect, it, vi } from 'vitest';
import { MessageHistory, type MessageHistoryPage } from '../lib/messageHistory';
import type { DaemonMessage } from '../lib/chatMessages';
import type { PersistedHistoryMessage } from '../lib/historyPages';

type Tail = { id: string; messages: DaemonMessage[]; title: string };
const id = (n: number) =>
  `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const CONVERSATION = id(9999);
const message = (n: number): PersistedHistoryMessage => ({
  id: id(n),
  role: 'assistant',
  content: 'synthetic',
  parts: [{ type: 'text', text: 'synthetic' }],
  created_at: '2026-10-28T00:00:00.123456Z',
});
const page = (
  numbers: number[],
  more = false,
): { status: number; value: MessageHistoryPage<Tail> } => {
  const messages = numbers.map(message);
  const start = numbers.length ? `cursor_${numbers[0]}` : null;
  const end = numbers.length ? `cursor_${numbers.at(-1)}` : null;
  return {
    status: 200,
    value: {
      conversation: { id: CONVERSATION, messages, title: 'synthetic' },
      messages,
      page: {
        has_more: more,
        next_cursor: more ? start : null,
        start_cursor: start,
        end_cursor: end,
      },
    },
  };
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function setup() {
  let generation = 1;
  const read =
    vi.fn<ConstructorParameters<typeof MessageHistory<Tail>>[0]['read']>();
  const yieldSlice = vi.fn(async () => {});
  const owner = new MessageHistory<Tail>({
    generation: () => generation,
    read,
    retryDelays: [1, 2],
    yield: yieldSlice,
  });
  owner.select(CONVERSATION);
  return {
    owner,
    read,
    yieldSlice,
    account: () => {
      generation += 1;
      owner.select(CONVERSATION);
    },
  };
}
afterEach(() => vi.useRealTimers());

describe('selected persisted message history owner', () => {
  it('retries a failed gap bridge against its original boundary rather than the newly merged tail', async () => {
    const { owner, read } = setup();
    read
      .mockResolvedValueOnce(page([1, 2], true))
      .mockResolvedValueOnce(page([5, 6], true))
      .mockRejectedValueOnce(new Error('synthetic gap failure'))
      .mockResolvedValueOnce(page([5, 6], true))
      .mockResolvedValueOnce(page([1, 2, 3, 4], true));
    await owner.refresh();
    await owner.refresh();
    expect(owner.getSnapshot()).toMatchObject({
      incomplete: true,
      error: true,
      retryKind: 'tail',
    });
    await owner.refresh();
    expect(read.mock.calls[4][1]).toEqual({ before: 'cursor_5' });
    expect(owner.getSnapshot().messages).toHaveLength(6);
    expect(owner.getSnapshot().incomplete).toBe(false);
    owner.dispose();
  });

  it('does not let a successful tail poll hide an incomplete covered-range refresh', async () => {
    const { owner, read } = setup();
    read
      .mockResolvedValueOnce(page([1, 2, 3], true))
      .mockResolvedValueOnce(page([3, 4], true))
      .mockRejectedValueOnce(new Error('synthetic range failure'))
      .mockResolvedValueOnce(page([3, 4]))
      .mockResolvedValueOnce(page([1, 2, 3, 4]));
    await owner.refresh();
    await owner.refreshRange();
    await owner.refresh();
    expect(owner.getSnapshot().incomplete).toBe(true);
    await owner.refreshRange();
    expect(owner.getSnapshot().incomplete).toBe(false);
    owner.dispose();
  });
  it('loads older pages without changing the bounded SDK-tail object or payload', async () => {
    const { owner, read } = setup();
    read
      .mockResolvedValueOnce(page([5, 6], true))
      .mockResolvedValueOnce(page([3, 4], true))
      .mockResolvedValueOnce(page([1, 2]));
    const tail = await owner.refresh();
    const payload = JSON.stringify(tail?.messages);
    await owner.loadOlder();
    await owner.loadOlder();
    expect(owner.getSnapshot().tail).toBe(tail);
    expect(JSON.stringify(owner.getSnapshot().tail?.messages)).toBe(payload);
    expect(owner.getSnapshot().messages.map((item) => item.id)).toEqual(
      [1, 2, 3, 4, 5, 6].map(id),
    );
    expect(read.mock.calls.map(([, bounds]) => bounds)).toEqual([
      {},
      { before: 'cursor_5' },
      { before: 'cursor_3' },
    ]);
    expect(owner.canUse(tail!)).toBe(true);
    owner.dispose();
  });

  it('deduplicates overlapping/duplicate-only pages while advancing the server cursor', async () => {
    const { owner, read } = setup();
    const duplicate = page([5, 6], true);
    duplicate.value.page.next_cursor = 'advanced_boundary';
    read
      .mockResolvedValueOnce(page([5, 6], true))
      .mockResolvedValueOnce(duplicate)
      .mockResolvedValueOnce(page([1, 2]));
    await owner.refresh();
    await owner.loadOlder();
    await owner.loadOlder();
    expect(read.mock.calls[2][1]).toEqual({ before: 'advanced_boundary' });
    expect(owner.getSnapshot().messages).toHaveLength(4);
    owner.dispose();
  });

  it('bridges more than one disconnected tail page in five-request scheduling slices', async () => {
    const { owner, read, yieldSlice } = setup();
    const numbers = (start: number) =>
      Array.from({ length: 100 }, (_, n) => start + n);
    read
      .mockResolvedValueOnce(page(numbers(1), true))
      .mockResolvedValueOnce(page(numbers(501), true))
      .mockResolvedValueOnce(page(numbers(401), true))
      .mockResolvedValueOnce(page(numbers(301), true))
      .mockResolvedValueOnce(page(numbers(201), true))
      .mockResolvedValueOnce(page(numbers(101), true))
      .mockResolvedValueOnce(page(numbers(1), true));
    await owner.refresh();
    const tail = await owner.refresh();
    expect(tail?.messages).toHaveLength(100);
    expect(tail?.messages[0].id).toBe(id(501));
    expect(owner.getSnapshot().messages).toHaveLength(600);
    expect(yieldSlice).toHaveBeenCalledOnce();
    expect(owner.getSnapshot()).toMatchObject({
      syncing: false,
      hasOlder: true,
      error: false,
    });
    owner.dispose();
  });

  it('keeps the older continuation independent of completed covered-range refresh', async () => {
    const { owner, read } = setup();
    read
      .mockResolvedValueOnce(page([5, 6], true))
      .mockResolvedValueOnce(page([3, 4], true))
      .mockResolvedValueOnce(page([5, 6, 7], true))
      .mockResolvedValueOnce(page([3, 4]))
      .mockResolvedValueOnce(page([1, 2]));
    await owner.refresh();
    await owner.loadOlder();
    await owner.refreshRange();
    expect(read.mock.calls[2][1]).toEqual({
      before: undefined,
      through: 'cursor_3',
    });
    expect(read.mock.calls[3][1]).toEqual({
      before: 'cursor_5',
      through: 'cursor_3',
    });
    expect(owner.getSnapshot().hasOlder).toBe(true);
    await owner.loadOlder();
    expect(read.mock.calls[4][1]).toEqual({ before: 'cursor_3' });
    expect(owner.getSnapshot().messages).toHaveLength(7);
    owner.dispose();
  });

  it('does not remove covered messages after an incomplete refresh or lose retry boundaries', async () => {
    const { owner, read } = setup();
    read
      .mockResolvedValueOnce(page([1, 2, 3], true))
      .mockResolvedValueOnce(page([3, 4], true))
      .mockRejectedValueOnce(new Error('synthetic unavailable'));
    await owner.refresh();
    await owner.refreshRange();
    expect(owner.getSnapshot().messages.map((item) => item.id)).toEqual(
      [1, 2, 3, 4].map(id),
    );
    expect(owner.getSnapshot()).toMatchObject({ hasOlder: true, error: true });
    read.mockResolvedValueOnce(page([3, 4]));
    await owner.refreshRange();
    expect(read.mock.calls[3][1]).toEqual({
      before: undefined,
      through: 'cursor_1',
    });
    expect(owner.getSnapshot().messages.map((item) => item.id)).toEqual(
      [3, 4].map(id),
    );
    owner.dispose();
  });

  it('pairs coverage with its opaque lower cursor, not the sorted first loaded row', async () => {
    const { owner, read } = setup();
    read
      .mockResolvedValueOnce(page([5, 6], true))
      .mockResolvedValueOnce(page([3, 4]))
      .mockResolvedValueOnce(page([]));
    await owner.refresh();
    // Deletion of recent rows can make an unbounded latest window reach lower
    // than the original coverage cursor, without changing the older traversal.
    await owner.refresh();
    await owner.refreshRange();
    expect(read.mock.calls[2][1].through).toBe('cursor_5');
    expect(owner.getSnapshot().messages.map((item) => item.id)).toEqual(
      [3, 4].map(id),
    );
    owner.dispose();
  });

  it('retains content and continuation on malformed older page failure', async () => {
    const { owner, read } = setup();
    read
      .mockResolvedValueOnce(page([5, 6], true))
      .mockRejectedValueOnce(new Error('InvalidHistoryPage'))
      .mockResolvedValueOnce(page([1, 2]));
    const tail = await owner.refresh();
    await owner.loadOlder();
    expect(owner.getSnapshot()).toMatchObject({
      tail,
      hasOlder: true,
      error: true,
      loadingOlder: false,
    });
    await owner.loadOlder();
    expect(read.mock.calls[1][1]).toEqual(read.mock.calls[2][1]);
    owner.dispose();
  });

  it('rejects A-B-A and account races even if cancelled transport ignores its signal', async () => {
    const { owner, read, account } = setup();
    const stale = deferred<ReturnType<typeof page>>();
    read
      .mockImplementationOnce(() => stale.promise)
      .mockResolvedValue(page([1, 2]));
    const old = owner.refresh();
    await vi.waitFor(() => expect(read).toHaveBeenCalledOnce());
    const initialScope = owner.getSnapshot().scope;
    owner.select(id(9998));
    owner.select(CONVERSATION);
    const fresh = await owner.refresh();
    expect(owner.getSnapshot().scope).not.toBe(initialScope);
    stale.resolve(page([99]));
    expect(await old).toBeNull();
    expect(owner.getSnapshot().tail).toBe(fresh);
    expect(owner.canUse(fresh!)).toBe(true);
    account();
    expect(owner.getSnapshot().messages).toEqual([]);
    expect(owner.canUse(fresh!)).toBe(false);
    owner.dispose();
  });

  it('invalidates operation leases and returned tails before consumer adoption', async () => {
    const { owner, read } = setup();
    read.mockResolvedValue(page([1, 2]));
    const first = await owner.refresh();
    const operation = owner.captureOperation();
    const scope = owner.getSnapshot().scope;
    await owner.refresh();
    expect(owner.canUse(first!)).toBe(false); // newer revision, same selection
    expect(operation.isCurrent()).toBe(true);
    owner.select(null);
    expect(operation.isCurrent()).toBe(false);
    expect(owner.isScopeCurrent(scope)).toBe(false);
    operation.release();
    owner.dispose();
  });

  it('coalesces duplicate reads and preserves initial retry exhaustion/permanent statuses', async () => {
    vi.useFakeTimers();
    const { owner, read } = setup();
    read.mockResolvedValue({ status: 503 });
    owner.startInitial();
    owner.startInitial();
    await vi.advanceTimersByTimeAsync(10);
    expect(read).toHaveBeenCalledTimes(3);
    expect(owner.getSnapshot().failure).toBe('exhausted');
    owner.select(null);
    owner.select(CONVERSATION);
    read.mockClear().mockResolvedValue({ status: 404 });
    owner.startInitial();
    await vi.advanceTimersByTimeAsync(10);
    expect(read).toHaveBeenCalledOnce();
    expect(owner.getSnapshot().failure).toBe('permanent');
    owner.dispose();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('clears inaccessible history and does not publish after disposal/old queue completion', async () => {
    const { owner, read } = setup();
    read
      .mockResolvedValueOnce(page([1, 2], true))
      .mockResolvedValueOnce({ status: 403 });
    await owner.refresh();
    await owner.refresh();
    expect(owner.getSnapshot()).toMatchObject({
      tail: null,
      messages: [],
      hasOlder: false,
      failure: 'permanent',
    });
    const listener = vi.fn();
    const unsubscribe = owner.subscribe(listener);
    owner.dispose();
    expect(owner.captureOperation().signal.aborted).toBe(true);
    await owner.refresh();
    expect(listener).not.toHaveBeenCalled();
    owner.resume();
    owner.select(CONVERSATION);
    read.mockResolvedValue(page([3]));
    expect((await owner.refresh())?.messages).toHaveLength(1);
    unsubscribe();
    owner.dispose();
  });
});
