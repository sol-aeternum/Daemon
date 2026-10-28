import { describe, expect, it, vi } from 'vitest';
import {
  ConversationPager,
  type ConversationPage,
} from '../lib/conversationPager';

type Row = { id: string; rank: number; title: string };
const row = (rank: number, title = 'synthetic'): Row => ({
  id: String(rank),
  rank,
  title,
});
const page = (
  rows: Row[],
  next: string | null = null,
  total = rows.length,
): ConversationPage<Row> => ({
  rows,
  total,
  page: {
    has_more: next !== null,
    next_cursor: next,
    start_cursor: rows.length ? `start-${rows[0].id}` : null,
    end_cursor: rows.length ? `end-${rows.at(-1)!.id}` : null,
  },
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const idle = async (pager: ConversationPager<Row>) =>
  vi.waitFor(() => {
    expect(pager.getSnapshot().refreshing).toBe(false);
    expect(pager.getSnapshot().loading).toBe(false);
    expect(pager.getSnapshot().loaded).toBe(true);
  });
function setup() {
  let generation = 1;
  const readPage =
    vi.fn<(cursor: string | null) => Promise<ConversationPage<Row>>>();
  const readOne =
    vi.fn<(id: string) => Promise<{ status: number; row?: Row }>>();
  const yieldSlice = vi.fn(async () => {});
  const pager = new ConversationPager<Row>({
    generation: () => generation,
    compare: (a, b) => b.rank - a.rank,
    readPage,
    readOne,
    yield: yieldSlice,
  });
  return {
    pager,
    readPage,
    readOne,
    yieldSlice,
    account: () => {
      generation += 1;
      pager.syncGeneration();
    },
  };
}

describe('provider-owned live conversation paging', () => {
  it('loads older pages, deduplicates and continues through duplicate-only pages', async () => {
    const { pager, readPage } = setup();
    readPage
      .mockResolvedValueOnce(page([row(10), row(9)], 'first', 6))
      .mockResolvedValueOnce(page([row(9)], 'duplicates', 6))
      .mockResolvedValueOnce(page([row(8), row(7)], null, 6));
    pager.refresh();
    await idle(pager);
    pager.loadMore();
    await idle(pager);
    expect(pager.getSnapshot().hasMore).toBe(true);
    pager.loadMore();
    await idle(pager);
    expect(readPage.mock.calls.map(([cursor]) => cursor)).toEqual([
      null,
      'first',
      'duplicates',
    ]);
    expect(pager.getSnapshot().rows.map((item) => item.id)).toEqual([
      '10',
      '9',
      '8',
      '7',
    ]);
    expect(pager.getSnapshot().hasMore).toBe(false);
    pager.dispose();
  });

  it('preserves loaded pages during head refresh and walks to frozen traversal boundary', async () => {
    const { pager, readPage, readOne } = setup();
    readPage
      .mockResolvedValueOnce(page([row(10), row(9)], 'old-head'))
      .mockResolvedValueOnce(page([row(8), row(7)], 'old-tail'))
      .mockResolvedValueOnce(page([row(11), row(10)], 'refresh-head'))
      .mockResolvedValueOnce(page([row(9), row(8)], 'refresh-middle'))
      .mockResolvedValueOnce(page([row(7), row(6)], 'refresh-tail'));
    pager.refresh();
    await idle(pager);
    pager.loadMore();
    await idle(pager);
    const paused = deferred<ConversationPage<Row>>();
    pager.refresh();
    await idle(pager);
    expect(pager.getSnapshot().rows.map((item) => item.id)).toEqual([
      '11',
      '10',
      '9',
      '8',
      '7',
      '6',
    ]);
    expect(readOne).not.toHaveBeenCalled();
    readPage.mockImplementationOnce(() => paused.promise);
    pager.loadMore();
    expect(readPage.mock.calls.at(-1)?.[0]).toBe('refresh-tail');
    paused.resolve(page([], null));
    await idle(pager);
    expect(pager.getSnapshot().rows).toHaveLength(6);
    pager.dispose();
  });

  it('revalidates missing IDs, moves survivors, drops only 403/404', async () => {
    const { pager, readPage, readOne } = setup();
    readPage
      .mockResolvedValueOnce(page([row(5), row(4), row(3), row(2)], null))
      .mockResolvedValueOnce(page([row(6), row(5)], null));
    readOne.mockImplementation(async (id) =>
      id === '4'
        ? { status: 200, row: { ...row(4), rank: 20 } }
        : { status: id === '3' ? 403 : 404 },
    );
    pager.refresh();
    await idle(pager);
    pager.refresh();
    await idle(pager);
    expect(readOne.mock.calls.map(([id]) => id)).toEqual(['4', '3', '2']);
    expect(pager.getSnapshot().rows.map((item) => item.id)).toEqual([
      '4',
      '6',
      '5',
    ]);
    pager.dispose();
  });

  it('retains missing IDs and the previous continuation on transient revalidation failure', async () => {
    const { pager, readPage, readOne } = setup();
    readPage
      .mockResolvedValueOnce(page([row(5), row(4)], 'saved'))
      .mockResolvedValueOnce(page([row(6), row(5)], 'sweep-head'))
      .mockResolvedValueOnce(page([row(3)], 'uncommitted-tail'));
    readOne.mockResolvedValue({ status: 503 });
    pager.refresh();
    await idle(pager);
    pager.refresh();
    await idle(pager);
    expect(pager.getSnapshot()).toMatchObject({
      error: true,
      updatesPending: true,
      hasMore: true,
    });
    expect(pager.getSnapshot().rows.some((item) => item.id === '4')).toBe(true);
    readPage.mockResolvedValueOnce(page([row(2)], null));
    pager.loadMore();
    await idle(pager);
    expect(readPage.mock.calls.at(-1)?.[0]).toBe('saved');
    pager.dispose();
  });

  it('does not let an older sweep overwrite optimistic edits or remove a newly edited missing row', async () => {
    const { pager, readPage, readOne } = setup();
    readPage.mockResolvedValueOnce(page([row(5), row(4)], null));
    pager.refresh();
    await idle(pager);
    const head = deferred<ConversationPage<Row>>();
    readPage.mockImplementationOnce(() => head.promise);
    readOne.mockResolvedValue({ status: 404 });
    pager.refresh();
    pager.update((rows) => rows.map((item) => ({ ...item, title: 'edited' })));
    head.resolve(page([row(5)], null));
    await idle(pager);
    expect(pager.getSnapshot().rows.map((item) => item.title)).toEqual([
      'edited',
      'edited',
    ]);
    pager.dispose();
  });

  it('does not overwrite an optimistic creation absent from the frozen sweep snapshot', async () => {
    const { pager, readPage } = setup();
    readPage.mockResolvedValueOnce(page([row(5)], null));
    pager.refresh();
    await idle(pager);
    const head = deferred<ConversationPage<Row>>();
    readPage.mockImplementationOnce(() => head.promise);
    pager.refresh();
    pager.update((rows) => [...rows, row(6, 'new local metadata')]);
    head.resolve(page([row(6), row(5)], null));
    await idle(pager);
    expect(pager.getSnapshot().rows[0].title).toBe('new local metadata');
    pager.dispose();
  });

  it('yields after five requests and prioritizes explicit paging between slices', async () => {
    const { pager, readPage, yieldSlice } = setup();
    readPage.mockResolvedValueOnce(page([row(100)], 'original', 100));
    pager.refresh();
    await idle(pager);
    const pause = deferred<void>();
    yieldSlice.mockImplementationOnce(() => pause.promise);
    readPage.mockImplementation(async (cursor) => {
      if (cursor === 'original') return page([row(99)], 'explicit', 100);
      const rank = cursor === null ? 110 : Number(cursor) - 1;
      return page([row(rank)], String(rank), 100);
    });
    pager.refresh();
    await vi.waitFor(() => expect(yieldSlice).toHaveBeenCalledOnce());
    expect(readPage).toHaveBeenCalledTimes(6); // initial + five-request slice
    pager.loadMore();
    pause.resolve();
    await idle(pager);
    expect(readPage.mock.calls[6][0]).toBe('original');
    readPage.mockResolvedValueOnce(page([], null));
    pager.loadMore();
    await idle(pager);
    // A sweep frozen at100 cannot adopt a cursor over an explicit extension99.
    expect(readPage.mock.calls.at(-1)?.[0]).toBe('explicit');
    pager.dispose();
  });

  it('invalidates queued work and late responses across account changes', async () => {
    const { pager, readPage, account } = setup();
    const pending = deferred<ConversationPage<Row>>();
    readPage
      .mockImplementationOnce(() => pending.promise)
      .mockResolvedValue(page([row(2)], null));
    pager.refresh();
    account();
    expect(pager.getSnapshot().rows).toEqual([]);
    pager.refresh();
    pending.resolve(page([row(999)], 'old-account'));
    await idle(pager);
    expect(pager.getSnapshot().rows).toEqual([row(2)]);
    expect(pager.getSnapshot().generation).toBe(2);
    pager.dispose();
  });

  it('preserves content on page failure, retry uses the unchanged boundary', async () => {
    const { pager, readPage } = setup();
    readPage
      .mockResolvedValueOnce(page([row(5)], 'saved'))
      .mockRejectedValueOnce(new Error('invalid page'))
      .mockResolvedValueOnce(page([row(4)], null));
    pager.refresh();
    await idle(pager);
    pager.loadMore();
    await idle(pager);
    expect(pager.getSnapshot()).toMatchObject({
      error: true,
      hasMore: true,
      rows: [row(5)],
    });
    pager.retry();
    await idle(pager);
    expect(readPage.mock.calls.map(([cursor]) => cursor)).toEqual([
      null,
      'saved',
      'saved',
    ]);
    expect(pager.getSnapshot().rows).toEqual([row(5), row(4)]);
    pager.dispose();
  });

  it('same-generation readiness does not discard rows and unmount stops publications', async () => {
    const { pager, readPage } = setup();
    readPage.mockResolvedValueOnce(page([row(5)], 'saved'));
    pager.refresh();
    await idle(pager);
    pager.syncGeneration();
    expect(pager.getSnapshot().rows).toEqual([row(5)]);
    const pending = deferred<ConversationPage<Row>>();
    readPage.mockImplementationOnce(() => pending.promise);
    const listener = vi.fn();
    pager.subscribe(listener);
    pager.loadMore();
    pager.dispose();
    listener.mockClear();
    pending.resolve(page([row(4)], null));
    await Promise.resolve();
    await Promise.resolve();
    expect(listener).not.toHaveBeenCalled();
  });
});
