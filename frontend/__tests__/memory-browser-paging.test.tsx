import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MemoryTab from '../components/settings/MemoryTab';
import {
  MEMORY_PAGE_SIZE,
  parseMemoryPage,
  useMemories,
} from '../hooks/useMemories';

const authState = vi.hoisted(() => ({
  generation: 1,
  listeners: new Set<() => void>(),
}));
vi.mock('../lib/auth', () => ({
  ensureAuthHeader: async () => 'Bearer test-browser',
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
    id: `m-${String(index).padStart(3, '0')}`,
    content: `Memory number ${index}`,
    category: 'fact',
    status: 'active',
    source_type: 'extracted',
    conversation_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    confirmed: true,
    ...overrides,
  };
}

const NON_DELETED = ['active', 'pending', 'superseded', 'inactive', 'rejected'];

let rows: Row[];
let listRequests: URLSearchParams[];
let gate: ((params: URLSearchParams) => Promise<void> | void) | null;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** In-memory GET /memories with the server's filter and paging contract. */
async function serveList(params: URLSearchParams) {
  listRequests.push(params);
  if (gate) await gate(params);
  const status = params.get('status') ?? 'active';
  const source = params.get('source_type');
  const matching = rows.filter(
    (r) =>
      (status === 'all'
        ? NON_DELETED.includes(r.status)
        : r.status === status) &&
      (!source || r.source_type === source),
  );
  const limit = Number(params.get('limit') ?? 20);
  const offset = Number(params.get('offset') ?? 0);
  const page = matching.slice(offset, offset + limit);
  return json({
    memories: page,
    total: matching.length,
    has_more: offset + page.length < matching.length,
    limit,
    offset,
  });
}

beforeEach(() => {
  rows = Array.from({ length: 21 }, (_, i) => row(i + 1));
  listRequests = [];
  gate = null;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), 'http://localhost');
      if (
        url.pathname.endsWith('/memories') &&
        (!init?.method || init.method === 'GET')
      ) {
        return serveList(url.searchParams);
      }
      if (url.pathname.endsWith('/memories') && init?.method === 'DELETE') {
        const affected = rows.filter((r) =>
          NON_DELETED.includes(r.status),
        ).length;
        rows = rows.map((r) => ({ ...r, status: 'deleted' }));
        return json({ deleted: affected, hard: false });
      }
      return json({});
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('memory list response validation', () => {
  it('requires the paging metadata the browser depends on', () => {
    expect(
      parseMemoryPage({ memories: [], total: 0, has_more: false }).total,
    ).toBe(0);
    for (const body of [
      {},
      { memories: [] },
      { memories: [], total: 2 },
      { memories: [row(1), row(2)], total: 1, has_more: false },
      { memories: [], total: 1.5, has_more: false },
      { memories: null, total: 0, has_more: false },
    ]) {
      expect(() => parseMemoryPage(body)).toThrow(
        'Unexpected memory list response',
      );
    }
  });
});

describe('Memory Browser paging (#249)', () => {
  // The full Memory tab is slow to render under jsdom.
  it(
    'shows the true total and reaches every memory exactly once',
    { timeout: 15000 },
    async () => {
      render(<MemoryTab />);
      expect(await screen.findByText('Showing 20 of 21 memories')).toBeTruthy();
      expect(screen.queryByText('Memory number 21')).toBeNull();

      fireEvent.click(
        screen.getByRole('button', { name: 'Load more (1 remaining)' }),
      );

      expect(await screen.findByText('Memory number 21')).toBeTruthy();
      expect(screen.getAllByText('Memory number 21')).toHaveLength(1);
      expect(screen.getByText('Showing 21 of 21 memories')).toBeTruthy();
      expect(screen.queryByRole('button', { name: /Load more/ })).toBeNull();
      const more = listRequests.find(
        (p) => p.get('offset') === String(MEMORY_PAGE_SIZE),
      );
      expect(more?.get('limit')).toBe(String(MEMORY_PAGE_SIZE));
    },
  );

  it('counts active memories in the summary and keeps the empty state truthful', async () => {
    rows = [];
    render(<MemoryTab />);
    expect(await screen.findByText('No memories found')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Load more/ })).toBeNull();
    expect(
      screen.getByText('active memories').previousSibling?.textContent,
    ).toBe('0');
  });

  it('sends the real source and an explicit "all" status for the filter chips', async () => {
    rows = [
      ...Array.from({ length: 3 }, (_, i) => row(i + 1)),
      row(10, { source_type: 'user_created' }),
      row(11, { source_type: 'user_created', status: 'superseded' }),
      row(12, { source_type: 'user_created', status: 'deleted' }),
    ];
    render(<MemoryTab />);
    await screen.findByText('Showing 4 of 4 memories');

    fireEvent.click(screen.getByRole('button', { name: 'Added by you' }));
    await waitFor(() =>
      expect(listRequests.at(-1)?.get('source_type')).toBe('user_created'),
    );
    expect(await screen.findByText('Showing 1 of 1 memory')).toBeTruthy();

    // Category, source and status each have an "All" chip; status is last.
    const allChips = screen.getAllByRole('button', { name: 'All' });
    fireEvent.click(allChips[allChips.length - 1]);
    await waitFor(() => expect(listRequests.at(-1)?.get('status')).toBe('all'));
    expect(listRequests.at(-1)?.get('source_type')).toBe('user_created');
    // Active and superseded are listed; the deleted row is not.
    expect(await screen.findByText('Showing 2 of 2 memories')).toBeTruthy();
  });

  it('the Clear All dialog states the real affected count and what actually happens', async () => {
    rows = [
      ...Array.from({ length: 4 }, (_, i) => row(i + 1)),
      row(20, { status: 'superseded' }),
      row(21, { status: 'rejected' }),
      row(22, { status: 'deleted' }),
    ];
    render(<MemoryTab />);
    await screen.findByText('Showing 4 of 4 memories');
    await waitFor(() =>
      expect(
        (screen.getByRole('button', { name: /Clear All/ }) as HTMLButtonElement)
          .disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole('button', { name: /Clear All/ }));

    const dialog = screen
      .getByText('Clear All Memories?')
      .closest('div.relative') as HTMLElement;
    const text = dialog.textContent ?? '';
    expect(text).toContain('This removes all 6 of your memories (4 active');
    expect(text).toContain('permanently erased within 30 days');
    expect(text).not.toContain('permanently delete');

    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Yes, remove all' }),
    );
    expect(
      await screen.findByText(
        'Removed 6 memories. They are permanently erased within 30 days.',
      ),
    ).toBeTruthy();
  });
});

describe('useMemories list lifecycle (#249)', () => {
  async function loadedHook() {
    const hook = renderHook(() => useMemories());
    await waitFor(() => expect(hook.result.current.memories).toHaveLength(20));
    return hook;
  }

  it('polling refresh keeps the loaded span instead of collapsing to page one', async () => {
    const { result } = await loadedHook();
    await act(async () => result.current.loadMore());
    expect(result.current.memories).toHaveLength(21);

    rows[0] = { ...rows[0], content: 'Edited elsewhere' };
    listRequests = [];
    await act(async () => result.current.refreshMemories());

    expect(result.current.memories).toHaveLength(21);
    expect(result.current.memories[0].content).toBe('Edited elsewhere');
    expect(listRequests.map((p) => [p.get('offset'), p.get('limit')])).toEqual([
      ['0', '21'],
    ]);
    expect(result.current.hasMore).toBe(false);
  });

  it('refresh and load more keep the current filters', async () => {
    rows = [
      ...Array.from({ length: 25 }, (_, i) =>
        row(i + 1, { source_type: 'import' }),
      ),
      row(99),
    ];
    const { result } = renderHook(() => useMemories());
    await act(async () =>
      result.current.fetchMemories({ source_type: 'import', status: 'active' }),
    );
    await act(async () => result.current.loadMore());
    await act(async () => result.current.refreshMemories());

    expect(result.current.total).toBe(25);
    expect(
      listRequests.slice(-3).every((p) => p.get('source_type') === 'import'),
    ).toBe(true);
    expect(result.current.memories.some((m) => m.id === 'm-099')).toBe(false);
  });

  it('a slow page for old filters never overwrites newer filter results', async () => {
    const { result } = await loadedHook();
    let release!: () => void;
    gate = (params) =>
      params.get('offset') === String(MEMORY_PAGE_SIZE)
        ? new Promise<void>((resolve) => (release = resolve))
        : undefined;
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.loadMore();
    });
    await waitFor(() => expect(release).toBeDefined());
    rows = [row(500, { status: 'superseded' })];
    await act(async () =>
      result.current.fetchMemories({ status: 'superseded' }),
    );
    await act(async () => {
      release();
      await pending;
    });

    expect(result.current.memories.map((m) => m.id)).toEqual(['m-500']);
    expect(result.current.total).toBe(1);
  });

  it('a sign-in change clears the list and drops a late response', async () => {
    const { result } = await loadedHook();
    let release!: () => void;
    gate = () => new Promise<void>((resolve) => (release = resolve));
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.refreshMemories();
    });
    await waitFor(() => expect(release).toBeDefined());
    act(() => changeSignIn());
    expect(result.current.memories).toEqual([]);
    await act(async () => {
      release();
      await pending;
    });
    expect(result.current.memories).toEqual([]);
    expect(result.current.total).toBe(0);
  });
});
