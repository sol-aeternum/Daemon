import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useLayoutEffect, StrictMode } from 'react';
import { ConversationList } from '../components/ConversationList';
import { useConversationHistory } from '../hooks/useConversationHistory';

const auth = vi.hoisted(() => ({
  generation: 1,
  listeners: new Set<() => void>(),
  push: vi.fn(),
  header: 'Bearer fictional-history-account',
  refresh: vi.fn<() => Promise<string | null>>(),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: auth.push }),
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('../lib/auth', () => ({
  getAuthHeader: () => auth.header,
  refreshIfNeeded: () => auth.refresh(),
  getAuthGeneration: () => auth.generation,
  subscribeAuthGeneration: (callback: () => void) => {
    auth.listeners.add(callback);
    return () => auth.listeners.delete(callback);
  },
}));
vi.mock('../components/AccountWidget', () => ({ AccountWidget: () => null }));

let latest: ReturnType<typeof useConversationHistory>;
const id = (n: number) =>
  `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const at = '2026-10-28T00:00:00.123456Z';
const row = (n: number, title = `Conversation ${n}`) => ({
  id: id(n),
  user_id: id(999),
  title,
  pinned: false,
  created_at: at,
  updated_at: at,
  last_activity_at: at,
  message_count: 0,
  title_locked: false,
});
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });
const page = (rows: ReturnType<typeof row>[], more = false) =>
  json({
    conversations: rows,
    total: 10,
    page: {
      has_more: more,
      next_cursor: more ? 'continue_older' : null,
      start_cursor: rows.length ? 'first' : null,
      end_cursor:
        rows.length > 1 ? 'continue_older' : rows.length ? 'first' : null,
    },
  });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function Sidebar() {
  const history = useConversationHistory();
  useLayoutEffect(() => {
    latest = history;
  });
  return (
    <ConversationList
      conversations={history.conversations}
      currentId={null}
      onSelect={history.switchConversation}
      onDelete={history.deleteConversation}
      onUpdate={history.updateConversation}
      onNewChat={history.createConversation}
      searchQuery={history.searchQuery}
      setSearchQuery={history.setSearchQuery}
      search={history.conversationSearch}
      paging={history.conversationPaging}
      isLoading={!history.isLoaded}
    />
  );
}

beforeEach(() => {
  auth.generation = 1;
  auth.header = 'Bearer fictional-history-account';
  auth.push.mockClear();
  auth.refresh.mockReset();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  expect(auth.listeners.size).toBe(0);
});
const changeAccount = () =>
  act(() => {
    auth.generation += 1;
    for (const listener of auth.listeners) listener();
  });

describe('cursor conversation sidebar integration', () => {
  it('keeps Load more reachable when the whole initial page is hidden drafts, retaining focus on exhaustion', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        page([row(5, 'New conversation'), row(4, 'New conversation')], true),
      )
      .mockResolvedValueOnce(page([row(3)]));
    vi.stubGlobal('fetch', fetcher);
    render(<Sidebar />);
    const more = await screen.findByRole('button', {
      name: 'Load more conversations',
    });
    expect(screen.getByText('No conversations found')).toBeTruthy();
    expect(more.className).toContain('min-h-touch');
    more.focus();
    fireEvent.click(more);
    await screen.findByText('Conversation 3');
    expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
      '/conversations?pagination=cursor&limit=100',
      '/conversations?pagination=cursor&limit=100&cursor=continue_older',
    ]);
    expect(screen.getByRole('button', { name: 'No more conversations' })).toBe(
      more,
    );
    expect(document.activeElement).toBe(more);
  });

  it('preserves loaded rows and exposes a retry when a next page is malformed', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(page([row(5), row(4)], true))
      .mockResolvedValueOnce(
        json({ conversations: [row(3)], total: 10, page: null }),
      )
      .mockResolvedValueOnce(page([row(3)]));
    vi.stubGlobal('fetch', fetcher);
    render(<Sidebar />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Load more conversations' }),
    );
    const retry = await screen.findByRole('button', {
      name: 'Retry conversations',
    });
    expect(screen.getByText('Conversation 5')).toBeTruthy();
    expect(screen.queryByText('Conversation 3')).toBeNull();
    fireEvent.click(retry);
    await screen.findByText('Conversation 3');
    expect(String(fetcher.mock.calls[1][0])).toBe(
      String(fetcher.mock.calls[2][0]),
    );
  });

  it('clears the list immediately on generation change and rejects a stale page body', async () => {
    const body = deferred<unknown>();
    const jsonBody = vi.fn(() => body.promise);
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(page([row(5), row(4)], true))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: jsonBody,
      } as unknown as Response)
      .mockResolvedValue(page([row(2)]));
    vi.stubGlobal('fetch', fetcher);
    render(<Sidebar />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Load more conversations' }),
    );
    await waitFor(() => expect(jsonBody).toHaveBeenCalledOnce());
    changeAccount();
    expect(screen.queryByText('Conversation 5')).toBeNull();
    await act(async () =>
      body.resolve({
        conversations: [row(3)],
        total: 10,
        page: {
          has_more: false,
          next_cursor: null,
          start_cursor: 'first',
          end_cursor: 'first',
        },
      }),
    );
    await screen.findByText('Conversation 2');
    expect(screen.queryByText('Conversation 3')).toBeNull();
  });

  it('does not fetch an old request after authentication resolves in another account', async () => {
    auth.header = '';
    const token = deferred<string | null>();
    auth.refresh.mockImplementation(() => token.promise);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(page([row(2)]));
    vi.stubGlobal('fetch', fetcher);
    render(<Sidebar />);
    await waitFor(() => expect(auth.refresh).toHaveBeenCalledOnce());
    changeAccount();
    auth.header = 'Bearer fictional-new-account';
    await act(async () => token.resolve('fictional-old-token'));
    await screen.findByText('Conversation 2');
    expect(fetcher).toHaveBeenCalledOnce();
    expect(
      new Headers(fetcher.mock.calls[0][1]?.headers).get('Authorization'),
    ).toBe('Bearer fictional-new-account');
  });

  it('ignores an old create response without navigation or stale-list publication', async () => {
    const body = deferred<unknown>();
    const jsonBody = vi.fn(() => body.promise);
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (_url, init) =>
        init?.method === 'POST'
          ? ({ ok: true, status: 200, json: jsonBody } as unknown as Response)
          : page([row(2)]),
      );
    vi.stubGlobal('fetch', fetcher);
    render(<Sidebar />);
    await screen.findByText('Conversation 2');
    let created!: Promise<string | null>;
    act(() => {
      created = latest.createConversation();
    });
    await waitFor(() => expect(jsonBody).toHaveBeenCalledOnce());
    changeAccount();
    await act(async () => body.resolve(row(8)));
    expect(await created).toBeNull();
    expect(auth.push).not.toHaveBeenCalled();
    expect(screen.queryByText('Conversation 8')).toBeNull();
  });

  it('survives StrictMode cleanup and retains loaded content on same-generation readiness notices', async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => page([row(2)]));
    vi.stubGlobal('fetch', fetcher);
    render(
      <StrictMode>
        <Sidebar />
      </StrictMode>,
    );
    await screen.findByText('Conversation 2');
    const count = fetcher.mock.calls.length;
    act(() => {
      for (const listener of auth.listeners) listener();
    });
    expect(screen.getByText('Conversation 2')).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(count);
  });
});
