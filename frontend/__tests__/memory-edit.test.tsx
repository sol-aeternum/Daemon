import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MemoryTab from '../components/settings/MemoryTab';
import { parseCorrectedMemory, useMemories } from '../hooks/useMemories';

const authState = vi.hoisted(() => ({
  generation: 1,
  listeners: new Set<() => void>(),
}));
vi.mock('../lib/auth', () => ({
  ensureAuthHeader: async () => 'Bearer test-edit',
  getAuthGeneration: () => authState.generation,
  subscribeAuthGeneration: (listener: () => void) => {
    authState.listeners.add(listener);
    return () => authState.listeners.delete(listener);
  },
}));

function changeSignIn() {
  authState.generation += 1;
  for (const listener of [...authState.listeners]) listener();
}

interface Row {
  id: string;
  content: string;
  category: string;
  status: string;
  source_type: string;
  conversation_id: null;
  created_at: string;
  updated_at: string;
  confirmed: boolean;
}

function row(index: number, overrides: Partial<Row> = {}): Row {
  return {
    id: `m-${index}`,
    content: `Memory number ${index}`,
    category: 'fact',
    status: 'active',
    source_type: 'user_created',
    conversation_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    confirmed: true,
    ...overrides,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

let rows: Row[];
let listRequests: number;
let listParams: URLSearchParams[];
let failNextLists: number;
let listGate: (() => Promise<void>) | null;
let patchBodies: unknown[];
let patchReply:
  | ((body: { content: string; category?: string }, id: string) => Response)
  | null;
let patchGate: Promise<void> | null;
let trailRequests: number;

beforeEach(() => {
  rows = [row(1, { content: 'I commute by tram' }), row(2), row(3)];
  listRequests = 0;
  listParams = [];
  failNextLists = 0;
  listGate = null;
  patchBodies = [];
  patchReply = null;
  patchGate = null;
  trailRequests = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), 'http://localhost');
      if (url.pathname.endsWith('/trail')) {
        trailRequests += 1;
        return json({ detail: 'Not Found' }, 404);
      }
      const single = url.pathname.match(/\/memories\/([^/]+)$/);
      if (single && init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body));
        patchBodies.push(body);
        if (patchGate) await patchGate;
        if (patchReply) return patchReply(body, single[1]);
        const index = rows.findIndex((r) => r.id === single[1]);
        rows[index] = {
          ...rows[index],
          content: body.content,
          category: body.category ?? rows[index].category,
        };
        return json(rows[index]);
      }
      if (
        url.pathname.endsWith('/memories') &&
        (!init?.method || init.method === 'GET')
      ) {
        listRequests += 1;
        listParams.push(url.searchParams);
        if (failNextLists > 0) {
          failNextLists -= 1;
          return json({ detail: 'unavailable' }, 503);
        }
        // Snapshot first, then wait: a held reply reflects data at read time.
        // Honours the category and search filters like the real API.
        const category = url.searchParams.get('category');
        const search = url.searchParams.get('search')?.toLowerCase();
        const snapshot = rows
          .filter(
            (r) =>
              (!category || r.category === category) &&
              (!search || r.content.toLowerCase().includes(search)),
          )
          .map((r) => ({ ...r }));
        if (listGate) await listGate();
        const limit = Number(url.searchParams.get('limit') ?? 20);
        const offset = Number(url.searchParams.get('offset') ?? 0);
        const page = snapshot.slice(offset, offset + limit);
        return json({
          memories: page,
          total: snapshot.length,
          has_more: offset + page.length < snapshot.length,
        });
      }
      return json({});
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function openFirstMemoryForEditing() {
  render(<MemoryTab />);
  fireEvent.click(await screen.findByText('I commute by tram'));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
  return screen.getByLabelText('Memory content') as HTMLTextAreaElement;
}

describe('Memory Browser edit (#395)', () => {
  it(
    'saves through PATCH, shows the acknowledged memory and leaves edit mode',
    { timeout: 15000 },
    async () => {
      const field = await openFirstMemoryForEditing();
      fireEvent.change(field, {
        target: { value: '  I commute by bicycle  ' },
      });
      fireEvent.change(screen.getByLabelText('Memory category'), {
        target: { value: 'preference' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() =>
        expect(screen.queryByLabelText('Memory content')).toBeNull(),
      );
      expect(patchBodies).toEqual([
        { content: 'I commute by bicycle', category: 'preference' },
      ]);
      expect(screen.getByText('I commute by bicycle')).toBeTruthy();
      expect(screen.queryByRole('alert')).toBeNull();
    },
  );

  it(
    'omits the category when it was not changed',
    { timeout: 15000 },
    async () => {
      const field = await openFirstMemoryForEditing();
      fireEvent.change(field, { target: { value: 'I commute by bus' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(patchBodies).toHaveLength(1));
      expect(patchBodies[0]).toEqual({ content: 'I commute by bus' });
    },
  );

  it.each([
    [404, 'This memory no longer exists, so the edit was not saved.'],
    [
      409,
      'Another memory already says exactly this. Change the wording or delete the other one.',
    ],
    [422, 'Write something to remember, under 2,000 characters.'],
    [
      503,
      'Memory is unavailable right now, so the edit was not saved. Try again shortly.',
    ],
  ])(
    'keeps the draft open with an accessible error on %s',
    { timeout: 15000 },
    async (status, message) => {
      patchReply = () => json({ detail: 'nope' }, status);
      const field = await openFirstMemoryForEditing();
      fireEvent.change(field, { target: { value: 'My unsaved correction' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));

      expect((await screen.findByRole('alert')).textContent).toBe(message);
      const stillOpen = screen.getByLabelText(
        'Memory content',
      ) as HTMLTextAreaElement;
      expect(stillOpen.value).toBe('My unsaved correction');
      expect(stillOpen.getAttribute('aria-describedby')).toBe(
        'memory-save-error',
      );
      expect(screen.queryByText('I commute by tram')).toBeNull();
    },
  );

  it(
    'keeps the draft and says the outcome is unknown when the reply is lost',
    { timeout: 15000 },
    async () => {
      patchReply = () => {
        throw new TypeError('connection reset');
      };
      const field = await openFirstMemoryForEditing();
      fireEvent.change(field, { target: { value: 'My correction' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      expect((await screen.findByRole('alert')).textContent).toContain(
        'this edit may not have been saved',
      );
      expect(
        (screen.getByLabelText('Memory content') as HTMLTextAreaElement).value,
      ).toBe('My correction');
    },
  );

  it(
    'does not treat a malformed success reply as a saved memory',
    { timeout: 15000 },
    async () => {
      patchReply = () => json({ status: 'updated' });
      const field = await openFirstMemoryForEditing();
      fireEvent.change(field, { target: { value: 'My correction' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      expect((await screen.findByRole('alert')).textContent).toContain(
        'may or may not have been saved',
      );
      expect(screen.getByLabelText('Memory content')).toBeTruthy();
    },
  );

  it('locks the draft while saving', { timeout: 15000 }, async () => {
    let release!: () => void;
    patchGate = new Promise<void>((resolve) => (release = resolve));
    const field = await openFirstMemoryForEditing();
    fireEvent.change(field, { target: { value: 'First correction' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(patchBodies).toHaveLength(1));
    expect(field.readOnly).toBe(true);
    fireEvent.change(field, { target: { value: 'Typed during save' } });
    expect(field.value).toBe('First correction');
    await act(async () => release());
    await waitFor(() =>
      expect(screen.queryByLabelText('Memory content')).toBeNull(),
    );
    expect(screen.getByText('First correction')).toBeTruthy();
  });

  it(
    'says history is unavailable and never requests it',
    { timeout: 15000 },
    async () => {
      render(<MemoryTab />);
      fireEvent.click(await screen.findByText('I commute by tram'));
      expect(
        await screen.findByText(/Edit history isn.t available yet/),
      ).toBeTruthy();
      const before = listRequests;
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(trailRequests).toBe(0);
      expect(listRequests).toBe(before); // no second list instance polling
    },
  );
});

describe('correctMemory list ordering (#395)', () => {
  async function loadedHook() {
    const hook = renderHook(() => useMemories());
    await waitFor(() => expect(hook.result.current.memories).toHaveLength(3));
    return hook;
  }

  it('a list reply read before the edit cannot overwrite the edited memory', async () => {
    const { result } = await loadedHook();
    let releaseList!: () => void;
    listGate = () => new Promise<void>((resolve) => (releaseList = resolve));
    let refresh!: Promise<void>;
    act(() => {
      refresh = result.current.refreshMemories();
    });
    await waitFor(() => expect(releaseList).toBeDefined());
    listGate = null;
    await act(async () => {
      const saved = await result.current.correctMemory(
        'm-1',
        'I commute by bicycle',
      );
      expect(saved.ok).toBe(true);
    });
    await act(async () => {
      releaseList();
      await refresh;
    });
    await waitFor(() =>
      expect(result.current.memories.find((m) => m.id === 'm-1')?.content).toBe(
        'I commute by bicycle',
      ),
    );
  });

  it('a poll during a pending edit waits until it settles', async () => {
    const { result } = await loadedHook();
    let release!: () => void;
    patchGate = new Promise<void>((resolve) => (release = resolve));
    let pending!: Promise<unknown>;
    act(() => {
      pending = result.current.correctMemory('m-1', 'I commute by bicycle');
    });
    const before = listRequests;
    await act(async () => result.current.refreshMemories());
    expect(listRequests).toBe(before);
    await act(async () => {
      release();
      await pending;
    });
    await waitFor(() => expect(listRequests).toBe(before + 1));
    expect(result.current.memories.find((m) => m.id === 'm-1')?.content).toBe(
      'I commute by bicycle',
    );
  });

  it('a sign-in change during an edit drops its result', async () => {
    const { result } = await loadedHook();
    let release!: () => void;
    patchGate = new Promise<void>((resolve) => (release = resolve));
    let pending!: Promise<unknown>;
    act(() => {
      pending = result.current.correctMemory('m-1', 'Other account sees this?');
    });
    await waitFor(() => expect(patchBodies).toHaveLength(1));
    act(() => changeSignIn());
    await act(async () => {
      release();
      await expect(pending).rejects.toThrow('Memory request superseded');
    });
    expect(result.current.memories).toEqual([]);
  });

  it('rejects blank or over-long text without a request', async () => {
    const { result } = await loadedHook();
    await act(async () => {
      expect((await result.current.correctMemory('m-1', '   ')).ok).toBe(false);
      expect(
        (await result.current.correctMemory('m-1', 'x'.repeat(2001))).ok,
      ).toBe(false);
    });
    expect(patchBodies).toHaveLength(0);
  });

  it('accepts only a reply for the memory that was edited', () => {
    expect(
      parseCorrectedMemory(
        { id: 'm-1', content: 'x', category: 'fact' },
        'm-1',
      ),
    ).not.toBeNull();
    expect(parseCorrectedMemory({ status: 'updated' }, 'm-1')).toBeNull();
    expect(
      parseCorrectedMemory(
        { id: 'm-2', content: 'x', category: 'fact' },
        'm-1',
      ),
    ).toBeNull();
    expect(
      parseCorrectedMemory({ id: 'm-1', content: 1, category: 'fact' }, 'm-1'),
    ).toBeNull();
  });
});

describe('edits that change filter membership (#437 review)', () => {
  function many(count: number, make: (i: number) => Partial<Row>) {
    return Array.from({ length: count }, (_, i) => row(i + 1, make(i + 1)));
  }

  it('a category edit out of the filter keeps paging exact (21 facts)', async () => {
    rows = many(21, () => ({ category: 'fact' }));
    const { result } = renderHook(() => useMemories());
    await act(async () =>
      result.current.fetchMemories({ category: 'fact', status: 'active' }),
    );
    expect(result.current.memories).toHaveLength(20);
    expect(result.current.total).toBe(21);

    await act(async () => {
      expect(
        (
          await result.current.correctMemory(
            'm-5',
            'Memory number 5',
            'preference',
          )
        ).ok,
      ).toBe(true);
    });
    await waitFor(() => expect(result.current.total).toBe(20));
    await act(async () => result.current.loadMore());

    const ids = result.current.memories.map((m) => m.id);
    expect(ids).not.toContain('m-5');
    expect(ids).toContain('m-21');
    expect(ids).toHaveLength(20);
    expect(new Set(ids).size).toBe(20);
    expect(result.current.hasMore).toBe(false);
  });

  it('a search-term edit out of the filter keeps paging exact', async () => {
    rows = many(21, (i) => ({ content: `Tram stop note ${i}` }));
    const { result } = renderHook(() => useMemories());
    await act(async () =>
      result.current.fetchMemories({ search: 'tram', status: 'active' }),
    );
    expect(result.current.total).toBe(21);

    await act(async () => {
      expect(
        (await result.current.correctMemory('m-3', 'Bus stop note 3')).ok,
      ).toBe(true);
    });
    await waitFor(() => expect(result.current.total).toBe(20));
    await act(async () => result.current.loadMore());

    const ids = result.current.memories.map((m) => m.id);
    expect(ids).not.toContain('m-3');
    expect(ids).toContain('m-21');
    expect(result.current.hasMore).toBe(false);
  });

  it(
    'the detail view keeps the acknowledged memory after it leaves the filter',
    { timeout: 15000 },
    async () => {
      rows = [
        row(1, { content: 'I commute by tram', category: 'fact' }),
        row(2),
      ];
      render(<MemoryTab />);
      await screen.findByText('I commute by tram');
      fireEvent.click(screen.getByRole('button', { name: 'Fact' }));
      await waitFor(() =>
        expect(screen.getByText('Showing 2 of 2 memories')).toBeTruthy(),
      );
      fireEvent.click(screen.getByText('I commute by tram'));
      fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
      fireEvent.change(screen.getByLabelText('Memory category'), {
        target: { value: 'preference' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() =>
        expect(screen.queryByLabelText('Memory content')).toBeNull(),
      );
      expect(screen.getByText('I commute by tram')).toBeTruthy();
      expect(screen.getByText('preference')).toBeTruthy();

      fireEvent.click(screen.getByRole('button', { name: 'Back to memories' }));
      await waitFor(() =>
        expect(screen.getByText('Showing 1 of 1 memory')).toBeTruthy(),
      );
      expect(screen.queryByText('I commute by tram')).toBeNull();
    },
  );

  it('explains a 412 and keeps the draft', { timeout: 15000 }, async () => {
    patchReply = () =>
      json(
        { detail: 'Memory changed since it was read; reload it and try again' },
        412,
      );
    const field = await openFirstMemoryForEditing();
    fireEvent.change(field, { target: { value: 'My stale edit' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect((await screen.findByRole('alert')).textContent).toBe(
      'This memory changed since you opened it, so your edit was not saved. Your draft is still here; reopen the memory to see the latest version.',
    );
    expect(
      (screen.getByLabelText('Memory content') as HTMLTextAreaElement).value,
    ).toBe('My stale edit');
  });
});

describe('#437 re-review: auth boundary and reconcile barrier', () => {
  it(
    'a sign-in change drops an open memory and its unsaved draft',
    { timeout: 15000 },
    async () => {
      const field = await openFirstMemoryForEditing();
      fireEvent.change(field, { target: { value: 'Account A private draft' } });
      act(() => changeSignIn());
      await waitFor(() =>
        expect(screen.queryByLabelText('Memory content')).toBeNull(),
      );
      expect(screen.queryByText('Account A private draft')).toBeNull();
      expect(screen.queryByText('I commute by tram')).toBeNull();
    },
  );

  it(
    'a sign-in change drops the cached saved memory too',
    { timeout: 15000 },
    async () => {
      const field = await openFirstMemoryForEditing();
      fireEvent.change(field, { target: { value: 'Account A saved text' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() =>
        expect(screen.queryByLabelText('Memory content')).toBeNull(),
      );
      expect(screen.getByText('Account A saved text')).toBeTruthy();
      act(() => changeSignIn());
      await waitFor(() =>
        expect(screen.queryByText('Account A saved text')).toBeNull(),
      );
      expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    },
  );

  it('"Load more" during a held post-edit reconcile waits and then reaches every row', async () => {
    rows = Array.from({ length: 21 }, (_, i) =>
      row(i + 1, { category: 'fact' }),
    );
    const { result } = renderHook(() => useMemories());
    await act(async () =>
      result.current.fetchMemories({ category: 'fact', status: 'active' }),
    );
    expect(result.current.memories).toHaveLength(20);

    let releaseList!: () => void;
    listGate = () => new Promise<void>((resolve) => (releaseList = resolve));
    await act(async () => {
      expect(
        (
          await result.current.correctMemory(
            'm-5',
            'Memory number 5',
            'preference',
          )
        ).ok,
      ).toBe(true);
    });
    await waitFor(() => expect(releaseList).toBeDefined()); // reconcile is held
    let more!: Promise<void>;
    act(() => {
      more = result.current.loadMore(); // clicked before the reconcile applies
    });
    expect(result.current.loading).toBe(true);
    listGate = null;
    await act(async () => {
      releaseList();
      await more;
    });

    const ids = result.current.memories.map((m) => m.id);
    expect(ids).not.toContain('m-5');
    expect(ids).toContain('m-21');
    expect(ids).toHaveLength(20);
    expect(new Set(ids).size).toBe(20);
    expect(result.current.total).toBe(20);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.loading).toBe(false);
  });
});

describe('#437 re-review: Load more never trusts an unrepaired offset', () => {
  async function editedFacts() {
    rows = Array.from({ length: 21 }, (_, i) =>
      row(i + 1, { category: 'fact' }),
    );
    const hook = renderHook(() => useMemories());
    await act(async () =>
      hook.result.current.fetchMemories({ category: 'fact', status: 'active' }),
    );
    expect(hook.result.current.memories).toHaveLength(20);
    return hook;
  }

  function staleOffsetRequested(from: number) {
    return listParams.slice(from).some((p) => p.get('offset') === '20');
  }

  function expectAllRemainingFacts(result: {
    current: ReturnType<typeof useMemories>;
  }) {
    const ids = result.current.memories.map((m) => m.id);
    expect(ids).not.toContain('m-5');
    expect(ids).toContain('m-21');
    expect(ids).toHaveLength(20);
    expect(new Set(ids).size).toBe(20);
    expect(result.current.total).toBe(20);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.loading).toBe(false);
  }

  it('a failed (503) reconcile leaves the list dirty; Load more repairs instead of paging', async () => {
    const { result } = await editedFacts();
    failNextLists = 1; // the post-edit reconcile read fails
    const before = listParams.length;
    await act(async () => {
      expect(
        (
          await result.current.correctMemory(
            'm-5',
            'Memory number 5',
            'preference',
          )
        ).ok,
      ).toBe(true);
    });
    await waitFor(() => expect(failNextLists).toBe(0));
    await act(async () => result.current.loadMore());

    expect(staleOffsetRequested(before)).toBe(false);
    expectAllRemainingFacts(result);
  });

  it('a reconcile superseded by a newer poll still never pages from the stale offset', async () => {
    const { result } = await editedFacts();
    let releaseFirst!: () => void;
    let held = 0;
    listGate = () => {
      held += 1;
      if (held === 1)
        return new Promise<void>((resolve) => (releaseFirst = resolve));
      return Promise.resolve();
    };
    const before = listParams.length;
    await act(async () => {
      expect(
        (
          await result.current.correctMemory(
            'm-5',
            'Memory number 5',
            'preference',
          )
        ).ok,
      ).toBe(true);
    });
    await waitFor(() => expect(releaseFirst).toBeDefined()); // reconcile held
    let poll!: Promise<void>;
    act(() => {
      poll = result.current.refreshMemories(); // supersedes the reconcile
    });
    let more!: Promise<void>;
    act(() => {
      more = result.current.loadMore(); // supersedes the poll too
    });
    await act(async () => {
      releaseFirst();
      await Promise.all([poll, more]);
    });

    expect(staleOffsetRequested(before)).toBe(false);
    expectAllRemainingFacts(result);
  });

  it('once a reconcile publishes, Load more pages normally from the repaired offset', async () => {
    rows = Array.from({ length: 45 }, (_, i) =>
      row(i + 1, { category: 'fact' }),
    );
    const { result } = renderHook(() => useMemories());
    await act(async () =>
      result.current.fetchMemories({ category: 'fact', status: 'active' }),
    );
    await act(async () => {
      await result.current.correctMemory(
        'm-5',
        'Memory number 5',
        'preference',
      );
    });
    await waitFor(() => expect(result.current.total).toBe(44)); // reconcile published
    const before = listParams.length;
    await act(async () => result.current.loadMore());
    expect(listParams.slice(before).map((p) => p.get('offset'))).toEqual([
      '20',
    ]);
    expect(result.current.memories).toHaveLength(40);
    expect(result.current.hasMore).toBe(true);
  });
});
