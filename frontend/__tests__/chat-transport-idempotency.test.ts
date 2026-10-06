import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { chatTransportFetch } from '../lib/chatTransportFetch';
import {
  clearPendingSubmissions,
  keyForSubmission,
  MAX_PENDING_SUBMISSIONS,
  PENDING_SUBMISSION_TTL_MS,
  promotePendingSubmission,
  settlePendingSubmission,
} from '../lib/pendingSubmission';

vi.mock('../lib/auth', () => ({
  getAuthGeneration: () => 1,
  getAuthHeader: () => 'Bearer account-1',
  refreshIfNeeded: () => Promise.resolve(undefined),
}));

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
const keys: string[] = [];

beforeEach(() => {
  storage = memoryStorage();
  keys.length = 0;
  vi.stubGlobal('localStorage', storage);
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('')));
});
afterEach(() => vi.unstubAllGlobals());

async function send(
  text: string,
  {
    conversationId = 'conv-1',
    model = 'auto',
    attachments = [] as unknown[],
  } = {},
) {
  await chatTransportFetch(
    '/api/chat',
    {
      method: 'POST',
      body: JSON.stringify({
        messages: [{ role: 'user', parts: [{ type: 'text', text }] }],
        attachments,
      }),
    },
    {
      model,
      conversationId,
      onGeneration: vi.fn(),
      onSubmissionKey: (key) => keys.push(key),
    },
  );
  return keys[keys.length - 1];
}

it('reuses the key when an unresolved submission is sent again', async () => {
  const first = await send('hello');
  // The response was lost (or the tab reloaded) and the user sends again.
  expect(await send('hello')).toBe(first);
  expect(first).toMatch(/^[0-9a-f-]{36}$/);
});

it('keeps one entry per submission across conversations', async () => {
  const a = await send('question A', { conversationId: 'conv-a' });
  const b = await send('question B', { conversationId: 'conv-b' });
  expect(b).not.toBe(a);
  // B finishing settles only B; A is still unresolved and keeps its key.
  settlePendingSubmission(b);
  expect(await send('question A', { conversationId: 'conv-a' })).toBe(a);
  expect(await send('question B', { conversationId: 'conv-b' })).not.toBe(b);
});

it('treats a changed model or attachment as a new submission', async () => {
  const base = await send('hello');
  expect(await send('hello', { model: 'other-model' })).not.toBe(base);
  expect(
    await send('hello', { attachments: [{ name: 'a.txt', content: 'x' }] }),
  ).not.toBe(base);
});

it('follows a new chat into the conversation the backend named', async () => {
  const key = await send('start a chat', { conversationId: null as never });
  promotePendingSubmission(key, 'conv-new');
  expect(await send('start a chat', { conversationId: 'conv-new' })).toBe(key);
});

it('keeps a key the caller already chose', async () => {
  await chatTransportFetch(
    '/api/chat',
    {
      method: 'POST',
      body: JSON.stringify({ messages: [], idempotency_key: 'retry-same' }),
    },
    { model: 'auto', conversationId: 'c', onGeneration: vi.fn() },
  );
  const body = JSON.parse(
    (
      (fetch as unknown as ReturnType<typeof vi.fn>).mock
        .calls[0][1] as RequestInit
    ).body as string,
  );
  expect(body.idempotency_key).toBe('retry-same');
});

it('bounds storage by age and count, and clears on sign-in changes', () => {
  const now = 1_000_000;
  const old = keyForSubmission({ text: 'old' }, null, now);
  expect(
    keyForSubmission(
      { text: 'old' },
      null,
      now + PENDING_SUBMISSION_TTL_MS + 1,
    ),
  ).not.toBe(old);
  for (let index = 0; index < MAX_PENDING_SUBMISSIONS + 5; index += 1) {
    keyForSubmission({ text: `q${index}` }, null, now);
  }
  const stored = JSON.parse(
    storage.getItem('daemon.pendingSubmissions.v2') ?? '{}',
  );
  expect(stored.entries.length).toBe(MAX_PENDING_SUBMISSIONS);
  clearPendingSubmissions();
  expect(storage.getItem('daemon.pendingSubmissions.v2')).toBeNull();
});

it('never stores the submitted text', () => {
  keyForSubmission({ text: 'a private question' }, 'conv-a');
  const stored = storage.getItem('daemon.pendingSubmissions.v2') ?? '';
  expect(stored).toBeTruthy();
  expect(stored).not.toContain('private');
  expect(stored).not.toContain('question');
});

it('falls back to a fresh key when storage is unavailable', () => {
  vi.stubGlobal('localStorage', undefined);
  expect(keyForSubmission({ text: 'hi' }, null)).not.toBe(
    keyForSubmission({ text: 'hi' }, null),
  );
});
