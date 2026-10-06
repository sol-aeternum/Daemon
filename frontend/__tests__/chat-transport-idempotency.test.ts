import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { chatTransportFetch } from '../lib/chatTransportFetch';
import {
  keyForSubmission,
  PENDING_SUBMISSION_TTL_MS,
  settlePendingSubmission,
} from '../lib/pendingSubmission';

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

function sentKey(): unknown {
  const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
  return JSON.parse((calls[calls.length - 1][1] as RequestInit).body as string)
    .idempotency_key;
}

const send = (text: string) =>
  chatTransportFetch(
    '/api/chat',
    {
      method: 'POST',
      body: JSON.stringify({
        messages: [{ role: 'user', parts: [{ type: 'text', text }] }],
      }),
    },
    scope,
  );

function memoryStorage(): Storage {
  const items = new Map<string, string>();
  return {
    get length() {
      return items.size;
    },
    clear: () => items.clear(),
    getItem: (key) => items.get(key) ?? null,
    key: (index) => [...items.keys()][index] ?? null,
    removeItem: (key) => void items.delete(key),
    setItem: (key, value) => void items.set(key, String(value)),
  };
}

let storage: Storage;

beforeEach(() => {
  storage = memoryStorage();
  vi.stubGlobal('localStorage', storage);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('')));
});
afterEach(() => {
  vi.unstubAllGlobals();
});

it('reuses the key when an unconfirmed submission is sent again', async () => {
  await send('hello');
  const first = sentKey();
  // The response was lost (or the tab reloaded) and the user sends again.
  await send('hello');
  expect(sentKey()).toBe(first);
  expect(first).toMatch(/^[0-9a-f-]{36}$/);
});

it('uses a new key once the outcome is known or the text differs', async () => {
  await send('hello');
  const first = sentKey();
  await send('something else');
  expect(sentKey()).not.toBe(first);
  settlePendingSubmission();
  await send('something else');
  const afterSettle = sentKey();
  await send('something else');
  expect(sentKey()).toBe(afterSettle);
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
  expect(sentKey()).toBe('retry-same');
});

it('scopes keys to the conversation and expires them', () => {
  const now = 1_000_000;
  const key = keyForSubmission('hi', 'conv-a', now);
  expect(keyForSubmission('hi', 'conv-a', now + 1000)).toBe(key);
  expect(keyForSubmission('hi', 'conv-b', now + 1000)).not.toBe(key);
  const fresh = keyForSubmission('again', null, now);
  expect(
    keyForSubmission('again', null, now + PENDING_SUBMISSION_TTL_MS + 1),
  ).not.toBe(fresh);
});

it('never stores the submitted text', () => {
  keyForSubmission('a private question', 'conv-a');
  const stored = storage.getItem('daemon.pendingSubmission.v1');
  expect(stored).toBeTruthy();
  expect(stored).not.toContain('private');
  expect(stored).not.toContain('question');
});

it('falls back to a fresh key when storage is unavailable', () => {
  vi.stubGlobal('localStorage', undefined);
  expect(keyForSubmission('hi', null)).not.toBe(keyForSubmission('hi', null));
});
