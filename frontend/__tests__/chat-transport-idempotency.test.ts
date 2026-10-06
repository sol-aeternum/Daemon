import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { chatTransportFetch } from '../lib/chatTransportFetch';

vi.mock('../lib/auth', () => ({
  getAuthGeneration: () => 1,
  getAuthHeader: () => 'Bearer account-1',
  refreshIfNeeded: () => Promise.resolve(undefined),
}));

const scope = {
  model: 'auto',
  conversationId: 'conv-1',
  onGeneration: vi.fn(),
};

function sentBody(): Record<string, unknown> {
  const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
  return JSON.parse((call[1] as RequestInit).body as string);
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('')));
});
afterEach(() => vi.unstubAllGlobals());

it('gives each submission its own idempotency key', async () => {
  const init = () => ({
    method: 'POST',
    body: JSON.stringify({ messages: [{ role: 'user', content: 'hi' }] }),
  });
  await chatTransportFetch('/api/chat', init(), scope);
  const first = sentBody().idempotency_key;
  (fetch as unknown as ReturnType<typeof vi.fn>).mockClear();
  await chatTransportFetch('/api/chat', init(), scope);
  const second = sentBody().idempotency_key;
  expect(typeof first).toBe('string');
  expect(first).toMatch(/^[0-9a-f-]{36}$/);
  expect(second).not.toBe(first);
});

it('keeps a key the caller already chose', async () => {
  await chatTransportFetch(
    '/api/chat',
    {
      method: 'POST',
      body: JSON.stringify({ messages: [], idempotency_key: 'retry-same' }),
    },
    scope,
  );
  expect(sentBody().idempotency_key).toBe('retry-same');
});
