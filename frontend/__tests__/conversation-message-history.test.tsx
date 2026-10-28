import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useLayoutEffect } from 'react';
import { useConversationHistory } from '../hooks/useConversationHistory';

const state = vi.hoisted(() => ({
  id: '00000000-0000-0000-0000-000000009999',
  generation: 1,
  listeners: new Set<() => void>(),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(state.id ? { id: state.id } : {}),
}));
vi.mock('../lib/auth', () => ({
  getAuthHeader: () => 'Bearer fictional-selected-history',
  refreshIfNeeded: vi.fn(),
  getAuthGeneration: () => state.generation,
  subscribeAuthGeneration: (callback: () => void) => {
    state.listeners.add(callback);
    return () => state.listeners.delete(callback);
  },
}));

let latest: ReturnType<typeof useConversationHistory>;
const id = (n: number) =>
  `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const A = id(9999);
const B = id(9998);
const at = '2026-10-28T00:00:00.123456Z';
const detail = (conversation: string, numbers: number[], more = false) => ({
  id: conversation,
  user_id: id(9997),
  title: conversation === A ? 'Selected A' : 'Selected B',
  created_at: at,
  updated_at: at,
  last_activity_at: at,
  pinned: false,
  messages: numbers.map((n) => ({
    id: id(n),
    conversation_id: conversation,
    user_id: id(9997),
    role: 'user',
    content: 'repeated fictional prompt',
    created_at: at,
  })),
  message_page: {
    has_more: more,
    next_cursor: more ? `cursor_${numbers[0]}` : null,
    start_cursor: numbers.length ? `cursor_${numbers[0]}` : null,
    end_cursor: numbers.length ? `cursor_${numbers.at(-1)}` : null,
  },
});
const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status });
const list = () =>
  json({
    conversations: [],
    total: 0,
    page: {
      has_more: false,
      next_cursor: null,
      start_cursor: null,
      end_cursor: null,
    },
  });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
function Probe() {
  const history = useConversationHistory();
  useLayoutEffect(() => {
    latest = history;
  });
  return (
    <>
      <p>selected:{history.getCurrentConversation()?.title ?? 'none'}</p>
      <p>display:{history.messageHistory.messages.length}</p>
      <p>tail:{history.getCurrentConversation()?.messages.length ?? 0}</p>
    </>
  );
}
beforeEach(() => {
  state.id = A;
  state.generation = 1;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  expect(state.listeners.size).toBe(0);
});
const account = () =>
  act(() => {
    state.generation += 1;
    for (const callback of state.listeners) callback();
  });

describe('selected history provider integration', () => {
  it('keeps older display rows separate from follower/SDK tail reads', async () => {
    const requests: URL[] = [];
    const responses = [
      detail(A, [5, 6], true),
      detail(A, [1, 2, 3, 4]),
      detail(A, [7, 8], true),
      detail(A, [5, 6], true),
    ];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input) => {
        const url = new URL(String(input), 'http://test');
        if (url.pathname === '/conversations') return list();
        requests.push(url);
        return json(responses.shift());
      }),
    );
    render(<Probe />);
    await screen.findByText('selected:Selected A');
    const original = latest.getCurrentConversation();
    await act(async () => latest.loadOlderMessages());
    expect(latest.messageHistory.messages).toHaveLength(6);
    expect(latest.getCurrentConversation()).toBe(original);
    expect(latest.getCurrentConversation()?.messages).toHaveLength(2);
    let fresh!: Awaited<ReturnType<typeof latest.refreshCurrentConversation>>;
    await act(async () => {
      fresh = await latest.refreshCurrentConversation();
    });
    expect(fresh?.messages.map((item) => item.id)).toEqual([id(7), id(8)]);
    expect(latest.messageHistory.messages).toHaveLength(8);
    expect(latest.messageHistory.hasOlder).toBe(false);
    expect(requests[1].searchParams.get('before')).toBe('cursor_5');
    expect(requests[3].searchParams.get('before')).toBe('cursor_7');
    expect(latest.isCurrentConversation(original!)).toBe(false);
    expect(latest.isCurrentConversation(fresh!)).toBe(true);
  });

  it('rejects an old A body after A-B-A and renders no old selected data during navigation', async () => {
    const body = deferred<unknown>();
    const jsonBody = vi.fn(() => body.promise);
    let aReads = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input) => {
        const url = new URL(String(input), 'http://test');
        if (url.pathname === '/conversations') return list();
        if (url.pathname.endsWith(A) && ++aReads === 2)
          return {
            ok: true,
            status: 200,
            json: jsonBody,
          } as unknown as Response;
        return json(detail(url.pathname.endsWith(A) ? A : B, [1, 2]));
      }),
    );
    const view = render(<Probe />);
    await screen.findByText('selected:Selected A');
    const scope = latest.messageHistory.scope;
    let stale!: ReturnType<typeof latest.refreshCurrentConversation>;
    act(() => {
      stale = latest.refreshCurrentConversation();
    });
    await waitFor(() => expect(jsonBody).toHaveBeenCalledOnce());
    state.id = B;
    view.rerender(<Probe />);
    expect(screen.queryByText('selected:Selected A')).toBeNull();
    await screen.findByText('selected:Selected B');
    state.id = A;
    view.rerender(<Probe />);
    await screen.findByText('selected:Selected A');
    await act(async () => body.resolve(detail(A, [99])));
    expect(await stale).toBeNull();
    expect(latest.messageHistory.scope).not.toBe(scope);
    expect(latest.messageHistory.messages.map((item) => item.id)).toEqual([
      id(1),
      id(2),
    ]);
    expect(latest.isHistoryScopeCurrent(scope)).toBe(false);
  });

  it('retains loaded history and older boundary when strict parsing rejects a whole page', async () => {
    let calls = 0;
    const malformed = detail(A, [3, 4]);
    malformed.messages[0].id = '';
    const before: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input) => {
        const url = new URL(String(input), 'http://test');
        if (url.pathname === '/conversations') return list();
        if (++calls === 1) return json(detail(A, [5, 6], true));
        before.push(url.searchParams.get('before')!);
        return json(calls === 2 ? malformed : detail(A, [1, 2, 3, 4]));
      }),
    );
    render(<Probe />);
    await screen.findByText('selected:Selected A');
    const tail = latest.getCurrentConversation();
    await act(async () => latest.loadOlderMessages());
    expect(latest.messageHistory).toMatchObject({
      error: true,
      hasOlder: true,
    });
    expect(latest.getCurrentConversation()).toBe(tail);
    expect(latest.messageHistory.messages).toHaveLength(2);
    await act(async () => latest.loadOlderMessages());
    expect(before).toEqual(['cursor_5', 'cursor_5']);
    expect(latest.messageHistory.messages).toHaveLength(6);
  });

  it.each(['key', 'status', 'cancel'] as const)(
    'returns an unknown %s result if auth changes during body parsing',
    async (operation) => {
      const body = deferred<unknown>();
      const jsonBody = vi.fn(() => body.promise);
      vi.stubGlobal(
        'fetch',
        vi.fn(async (input) => {
          const url = new URL(String(input), 'http://test');
          if (url.pathname === '/conversations') return list();
          if (url.pathname.startsWith('/tasks/'))
            return {
              ok: true,
              status: 200,
              json: jsonBody,
            } as unknown as Response;
          return json(detail(A, [1, 2]));
        }),
      );
      render(<Probe />);
      await screen.findByText('selected:Selected A');
      let pending!: Promise<unknown>;
      act(() => {
        pending =
          operation === 'key'
            ? latest.taskForKey('synthetic')
            : operation === 'status'
              ? latest.taskStatus('synthetic')
              : latest.cancelTask('synthetic');
      });
      await waitFor(() => expect(jsonBody).toHaveBeenCalledOnce());
      const oldTail = latest.getCurrentConversation();
      account();
      expect(latest.isCurrentConversation(oldTail!)).toBe(false);
      await act(async () =>
        body.resolve({ id: id(200), conversation_id: A, status: 'cancelled' }),
      );
      expect(await pending).toBe(
        operation === 'cancel' ? 'unconfirmed' : undefined,
      );
    },
  );

  it('does not clear loaded state or restart initial reads on same-generation readiness', async () => {
    const fetcher = vi.fn(async (input) =>
      String(input).includes('/conversations?')
        ? list()
        : json(detail(A, [1, 2])),
    );
    vi.stubGlobal('fetch', fetcher);
    render(<Probe />);
    await screen.findByText('selected:Selected A');
    const scope = latest.messageHistory.scope;
    const count = fetcher.mock.calls.length;
    act(() => {
      for (const callback of state.listeners) callback();
    });
    expect(latest.messageHistory.scope).toBe(scope);
    expect(latest.messageHistory.messages).toHaveLength(2);
    expect(fetcher).toHaveBeenCalledTimes(count);
  });
});
