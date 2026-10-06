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

it('replays a new chat exactly as first sent after the backend names it', async () => {
  const key = await send('start a chat', { conversationId: null as never });
  promotePendingSubmission(key, 'conv-new');
  // Resent from the promoted conversation: same key, original (null) scope,
  // so the backend's request fingerprint matches and it replays the task.
  expect(await send('start a chat', { conversationId: 'conv-new' })).toBe(key);
  const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
  const body = JSON.parse(
    (calls[calls.length - 1][1] as RequestInit).body as string,
  );
  expect(body.id).toBeNull();
});

it('fingerprints the attachment fields the page actually sends', async () => {
  const file = {
    id: 'att-1',
    kind: 'text',
    name: 'a.txt',
    mime_type: 'text/plain',
    size: 3,
  };
  const first = await send('see file', {
    attachments: [{ ...file, text_content: 'one' }],
  });
  expect(
    await send('see file', { attachments: [{ ...file, text_content: 'one' }] }),
  ).toBe(first);
  expect(
    await send('see file', { attachments: [{ ...file, text_content: 'two' }] }),
  ).not.toBe(first);
});

it('keeps concurrent submissions from different tabs independent', () => {
  // Two tabs each record a submission; neither rewrites the other's item.
  const tabA = keyForSubmission({ text: 'from tab A' }, 'conv-a').key;
  const tabB = keyForSubmission({ text: 'from tab B' }, 'conv-b').key;
  settlePendingSubmission(tabB);
  expect(keyForSubmission({ text: 'from tab A' }, 'conv-a').key).toBe(tabA);
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
  const old = keyForSubmission({ text: 'old' }, null, now).key;
  const later = now + PENDING_SUBMISSION_TTL_MS + 1;
  expect(keyForSubmission({ text: 'old' }, null, later).key).not.toBe(old);
  for (let index = 0; index < MAX_PENDING_SUBMISSIONS + 5; index += 1) {
    keyForSubmission({ text: `q${index}` }, null, later + index);
  }
  expect(storage.length).toBe(MAX_PENDING_SUBMISSIONS);
  clearPendingSubmissions();
  expect(storage.length).toBe(0);
});

it('never stores the submitted text', () => {
  keyForSubmission({ text: 'a private question' }, 'conv-a');
  const stored = storage.getItem(storage.key(0) ?? '') ?? '';
  expect(stored).toBeTruthy();
  expect(stored).not.toContain('private');
  expect(stored).not.toContain('question');
});

it('falls back to a fresh key when storage is unavailable', () => {
  vi.stubGlobal('localStorage', undefined);
  expect(keyForSubmission({ text: 'hi' }, null).key).not.toBe(
    keyForSubmission({ text: 'hi' }, null).key,
  );
});

it('treats a reselected file (new attachment id) as a new submission', async () => {
  const file = {
    kind: 'text',
    name: 'a.txt',
    mime_type: 'text/plain',
    size: 3,
  };
  const first = await send('see file', {
    attachments: [{ ...file, id: 'first-pick', text_content: 'one' }],
  });
  // Same bytes, but the backend's fingerprint includes the id: reusing the
  // key would only earn a 409 idempotency_conflict.
  expect(
    await send('see file', {
      attachments: [{ ...file, id: 'second-pick', text_content: 'one' }],
    }),
  ).not.toBe(first);
});

it('declares the durable-task features this client supports', async () => {
  await send('hello');
  const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
  const body = JSON.parse(
    (calls[calls.length - 1][1] as RequestInit).body as string,
  );
  expect(body.client_features).toEqual(['task-cancel', 'task-reset']);
});
