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
  return <p>open:{history.getCurrentConversation()?.id ?? 'none'}</p>;
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
