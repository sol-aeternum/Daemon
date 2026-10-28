import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { useConversationHistory } from '../hooks/useConversationHistory';

const state = vi.hoisted(() => ({ search: '' }));
const ids = {
  'conv-a': '00000000-0000-0000-0000-000000000001',
  'conv-b': '00000000-0000-0000-0000-000000000002',
};
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => {
    const params = new URLSearchParams(state.search);
    const selected = params.get('id');
    if (selected === 'conv-a' || selected === 'conv-b')
      params.set('id', ids[selected]);
    return params;
  },
}));
vi.mock('../lib/auth', () => ({
  getAuthGeneration: () => 0,
  subscribeAuthGeneration: () => () => {},
  getAuthHeader: () => 'Bearer test-history',
  refreshIfNeeded: vi.fn(),
}));

function deferred() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function conversation(id: string): Response {
  return new Response(
    JSON.stringify({
      id: ids[id as keyof typeof ids],
      user_id: '00000000-0000-0000-0000-000000000099',
      title: id,
      messages: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      last_activity_at: '2026-01-01T00:00:00Z',
      pinned: false,
      message_page: {
        has_more: false,
        next_cursor: null,
        start_cursor: null,
        end_cursor: null,
      },
      status: 'active',
      metadata: {},
    }),
  );
}

function selectedName(input: RequestInfo | URL): string | undefined {
  const path = new URL(String(input), 'http://test').pathname;
  return Object.entries(ids).find(
    ([, id]) => path === `/conversations/${id}`,
  )?.[0];
}

function emptyList(): Response {
  return new Response(
    JSON.stringify({
      conversations: [],
      total: 0,
      page: {
        has_more: false,
        next_cursor: null,
        start_cursor: null,
        end_cursor: null,
      },
    }),
  );
}

function OpenConversation() {
  const history = useConversationHistory();
  return (
    <>
      <p>open:{history.getCurrentConversation()?.title ?? 'none'}</p>
      <p>failure:{history.conversationLoadFailure ?? 'none'}</p>
    </>
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  state.search = '';
});

it('ignores an older conversation fetch that finishes after the current one', async () => {
  const pending = new Map<string, ReturnType<typeof deferred>>();
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const name = selectedName(input);
      if (!name) {
        return Promise.resolve(emptyList());
      }
      const request = deferred();
      pending.set(name, request);
      return request.promise;
    }),
  );

  state.search = 'id=conv-a';
  const view = render(<OpenConversation />);
  await vi.waitFor(() => expect(pending.has('conv-a')).toBe(true));
  // The user moves on before conv-a has loaded.
  state.search = 'id=conv-b';
  view.rerender(<OpenConversation />);
  await vi.waitFor(() => expect(pending.has('conv-b')).toBe(true));

  await act(async () => pending.get('conv-b')!.resolve(conversation('conv-b')));
  await screen.findByText('open:conv-b');
  await act(async () => pending.get('conv-a')!.resolve(conversation('conv-a')));
  expect(screen.getByText('open:conv-b')).toBeTruthy();
});

it('retries a conversation that failed to load', async () => {
  let calls = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      if (selectedName(input) !== 'conv-a') {
        return Promise.resolve(emptyList());
      }
      calls += 1;
      return Promise.resolve(
        calls === 1
          ? new Response('unavailable', { status: 503 })
          : conversation('conv-a'),
      );
    }),
  );
  state.search = 'id=conv-a';
  render(<OpenConversation />);
  // The first load fails; the retry (after about a second) succeeds.
  await screen.findByText('open:conv-a', undefined, { timeout: 4000 });
  expect(calls).toBe(2);
});

it('stops transient retries at the cap with an actionable failure state', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    let calls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
        if (selectedName(input) !== 'conv-a') {
          return Promise.resolve(emptyList());
        }
        calls += 1;
        return Promise.resolve(
          calls <= 5
            ? new Response('unavailable', { status: 503 })
            : conversation('conv-a'),
        );
      }),
    );
    state.search = 'id=conv-a';
    render(<OpenConversation />);
    // Initial load plus four retries, then no more requests.
    for (const ms of [1000, 3000, 10000, 30000, 30000]) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
      });
    }
    expect(screen.getByText('failure:exhausted')).toBeTruthy();
    expect(calls).toBe(5);
  } finally {
    vi.useRealTimers();
  }
});

it.each([403, 404])(
  'stops permanently unavailable %s conversation lookup without retries',
  async (status) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let calls = 0;
      vi.stubGlobal(
        'fetch',
        vi.fn((input: RequestInfo | URL) => {
          if (selectedName(input) === 'conv-a') {
            calls++;
            return Promise.resolve(new Response('unavailable', { status }));
          }
          return Promise.resolve(emptyList());
        }),
      );
      state.search = 'id=conv-a';
      render(<OpenConversation />);
      await act(async () => {});
      expect(screen.getByText('failure:permanent')).toBeTruthy();
      await act(async () => vi.advanceTimersByTimeAsync(120_000));
      expect(calls).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  },
);

it('drops a permanent selected-load failure across Home and a fresh A lookup', async () => {
  let calls = 0;
  const held = deferred();
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      if (selectedName(input) !== 'conv-a') {
        return Promise.resolve(emptyList());
      }
      calls++;
      return calls === 1
        ? Promise.resolve(new Response('missing', { status: 404 }))
        : held.promise;
    }),
  );
  state.search = 'id=conv-a';
  const view = render(<OpenConversation />);
  await screen.findByText('failure:permanent');
  state.search = '';
  view.rerender(<OpenConversation />);
  expect(screen.getByText('failure:none')).toBeTruthy();
  expect(screen.getByText('open:none')).toBeTruthy();
  state.search = 'id=conv-a';
  view.rerender(<OpenConversation />);
  expect(screen.getByText('failure:none')).toBeTruthy();
  await vi.waitFor(() => expect(calls).toBe(2));
  await act(async () => held.resolve(conversation('conv-a')));
  await screen.findByText('open:conv-a');
});
