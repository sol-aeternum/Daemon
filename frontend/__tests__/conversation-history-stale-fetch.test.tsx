import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { useConversationHistory } from '../hooks/useConversationHistory';

const state = vi.hoisted(() => ({ search: '' }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(state.search),
}));
vi.mock('../lib/auth', () => ({
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
      id,
      title: id,
      messages: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      status: 'active',
      metadata: {},
    }),
  );
}

function OpenConversation() {
  const history = useConversationHistory();
  return (
    <>
      <p>open:{history.getCurrentConversation()?.id ?? 'none'}</p>
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
      const url = String(input);
      const match = /\/conversations\/(conv-[ab])$/.exec(url);
      if (!match) {
        return Promise.resolve(
          new Response(JSON.stringify({ conversations: [] })),
        );
      }
      const request = deferred();
      pending.set(match[1], request);
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
      const url = String(input);
      if (!url.endsWith('/conversations/conv-a')) {
        return Promise.resolve(
          new Response(JSON.stringify({ conversations: [] })),
        );
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
        if (!String(input).endsWith('/conversations/conv-a')) {
          return Promise.resolve(
            new Response(JSON.stringify({ conversations: [] })),
          );
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
          if (String(input).endsWith('/conversations/conv-a')) {
            calls++;
            return Promise.resolve(new Response('unavailable', { status }));
          }
          return Promise.resolve(
            new Response(JSON.stringify({ conversations: [] })),
          );
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
