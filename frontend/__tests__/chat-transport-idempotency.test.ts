import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { chatTransportFetch } from '../lib/chatTransportFetch';
import {
  clearPendingSubmissions,
  finishedSubmissionKeys,
  pendingSubmission,
  registerSubmission,
  unresolvedSubmissions,
  PENDING_SUBMISSION_TTL_MS,
  promotePendingSubmission,
  pendingTasksIn,
  recordSubmissionTask,
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
    conversationId = 'conv-1' as string | null,
    model = 'auto',
    attachments = [] as unknown[],
    key = undefined as string | undefined,
  } = {},
) {
  await chatTransportFetch(
    '/api/chat',
    {
      method: 'POST',
      body: JSON.stringify({
        messages: [{ role: 'user', parts: [{ type: 'text', text }] }],
        attachments,
        ...(key ? { idempotency_key: key } : {}),
      }),
    },
    {
      model,
      conversationId,
      onGeneration: vi.fn(),
      onSubmissionKey: (key) => {
        if (key) keys.push(key);
      },
    },
  );
  return keys[keys.length - 1];
}

function lastBody(): Record<string, unknown> {
  const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
  return JSON.parse((calls[calls.length - 1][1] as RequestInit).body as string);
}

it('gives a send without a key a fresh key and records it without content', async () => {
  const key = await send('a private question', { model: 'chosen' });
  expect(key).toMatch(/^[0-9a-f-]{36}$/);
  expect(lastBody().idempotency_key).toBe(key);
  const recorded = pendingSubmission(key);
  expect(recorded).toMatchObject({
    scope: 'conv-1',
    requestConversationId: 'conv-1',
    model: 'chosen',
  });
  // Nothing derived from the message is stored (#476, salt finding).
  const stored = [...Array(storage.length).keys()]
    .map((index) => storage.getItem(storage.key(index) ?? '') ?? '')
    .join(' ');
  expect(stored).not.toContain('private');
  expect(stored).not.toContain('fingerprint');
});

it('never matches by content: identical text without the held key is new', async () => {
  const first = await send('hello');
  expect(await send('hello')).not.toBe(first);
});

it('keeps the key the page chose for a held draft', async () => {
  expect(await send('hello', { key: 'draft-key-1' })).toBe('draft-key-1');
  expect(lastBody().idempotency_key).toBe('draft-key-1');
});

it('replays the original model and conversation when a held draft is resent', async () => {
  // First sent from a new chat with an explicit model; the backend names the
  // chat, the page reloads and the picker resets to auto.
  await send('start a chat', {
    key: 'held-1',
    conversationId: null,
    model: 'chosen-model',
  });
  promotePendingSubmission('held-1', 'conv-new');
  await send('start a chat', {
    key: 'held-1',
    conversationId: 'conv-new',
    model: 'auto',
  });
  const body = lastBody();
  expect(body.id).toBeNull(); // the original (absent) conversation
  expect(body.model).toBe('chosen-model');
});

it('migrates older records, keeping their keys but not their fingerprints', async () => {
  // Review of #479: a cached older client's unresolved key must survive.
  const createdAt = Date.now() - 1000;
  storage.setItem(
    'daemon.pendingSubmission.v4:legacy-key',
    JSON.stringify({
      fingerprint: 'ab'.repeat(32),
      scope: 'conv-a',
      requestConversationId: null,
      model: 'chosen',
      provider: null,
      createdAt,
      taskId: 'task-9',
    }),
  );
  storage.setItem('daemon.pendingSubmission.v4:broken', '{"fingerprint":"ab"}');
  storage.setItem('daemon.pendingSubmission.salt', 'ab'.repeat(32));
  expect(unresolvedSubmissions().map(({ key }) => key)).toEqual(['legacy-key']);
  expect(pendingSubmission('legacy-key')).toEqual({
    scope: 'conv-a',
    requestConversationId: null,
    model: 'chosen',
    provider: null,
    createdAt,
    taskId: 'task-9',
  });
  for (const gone of [
    'daemon.pendingSubmission.v4:legacy-key',
    'daemon.pendingSubmission.v4:broken',
    'daemon.pendingSubmission.salt',
  ]) {
    expect(storage.getItem(gone)).toBeNull();
  }
});

function legacyRecord(createdAt: number, taskId?: string): string {
  return JSON.stringify({
    fingerprint: 'ab'.repeat(32),
    scope: 'conv-a',
    requestConversationId: null,
    model: 'chosen',
    provider: null,
    createdAt,
    ...(taskId ? { taskId } : {}),
  });
}

it('keeps an older record when its migration cannot be written (#479 review)', () => {
  const createdAt = Date.now() - 1000;
  storage.setItem(
    'daemon.pendingSubmission.v4:legacy-key',
    legacyRecord(createdAt),
  );
  const write = storage.setItem;
  storage.setItem = () => {
    throw new DOMException('full', 'QuotaExceededError');
  };
  // The key is still listed for reconciliation, and nothing was deleted.
  expect(unresolvedSubmissions().map(({ key }) => key)).toEqual(['legacy-key']);
  expect(
    storage.getItem('daemon.pendingSubmission.v4:legacy-key'),
  ).not.toBeNull();
  storage.setItem = write;
  // A later pass migrates it.
  expect(unresolvedSubmissions().map(({ key }) => key)).toEqual(['legacy-key']);
  expect(pendingSubmission('legacy-key')?.createdAt).toBe(createdAt);
  expect(storage.getItem('daemon.pendingSubmission.v4:legacy-key')).toBeNull();
});

it('never overwrites a current record with an older copy of the same key', () => {
  registerSubmission('shared-key', {
    scope: 'conv-new',
    requestConversationId: null,
    model: 'current',
    provider: null,
  });
  recordSubmissionTask('shared-key', 'task-new');
  storage.setItem(
    'daemon.pendingSubmission.v4:shared-key',
    legacyRecord(Date.now() - 5000, 'task-old'),
  );
  expect(unresolvedSubmissions().map(({ key }) => key)).toEqual(['shared-key']);
  expect(pendingSubmission('shared-key')?.taskId).toBe('task-new');
  expect(storage.getItem('daemon.pendingSubmission.v4:shared-key')).toBeNull();
});

it('settling a key also removes an older copy kept by a failed migration', () => {
  storage.setItem(
    'daemon.pendingSubmission.v4:legacy-key',
    legacyRecord(Date.now() - 1000),
  );
  settlePendingSubmission('legacy-key');
  expect(unresolvedSubmissions()).toEqual([]);
});

it('falls back to a fresh key when storage is unavailable', async () => {
  vi.stubGlobal('localStorage', undefined);
  const a = await send('hi');
  const b = await send('hi');
  expect(a).toMatch(/^[0-9a-f-]{36}$/);
  expect(b).not.toBe(a);
});

it('tracks, settles and lists submissions by key', async () => {
  const a = await send('first', { conversationId: 'conv-a' });
  const b = await send('second', { conversationId: 'conv-a' });
  recordSubmissionTask(a, 'task-a');
  expect(pendingTasksIn('conv-a')).toEqual([{ key: a, taskId: 'task-a' }]);
  const terminal = (status: string) => status === 'completed';
  expect(
    finishedSubmissionKeys(
      {
        id: 'conv-a',
        activeTask: null,
        latestTask: { id: 'task-a', status: 'completed' },
      },
      terminal,
    ),
  ).toEqual([a]);
  expect(
    finishedSubmissionKeys(
      {
        id: 'conv-a',
        activeTask: { id: 'task-a' },
        latestTask: { id: 'task-a', status: 'running' },
      },
      terminal,
    ),
  ).toEqual([]);
  settlePendingSubmission(a);
  expect(unresolvedSubmissions().map(({ key }) => key)).toEqual([b]);
  clearPendingSubmissions();
  expect(unresolvedSubmissions()).toEqual([]);
});

it('expires an unresolved entry after the TTL', async () => {
  registerSubmission(
    'old',
    { scope: null, requestConversationId: null, model: 'auto', provider: null },
    1_000,
  );
  expect(unresolvedSubmissions(1_000 + PENDING_SUBMISSION_TTL_MS + 1)).toEqual(
    [],
  );
});

it('declares the durable-task features this client supports', async () => {
  await send('hello');
  const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
  const body = JSON.parse(
    (calls[calls.length - 1][1] as RequestInit).body as string,
  );
  expect(body.client_features).toEqual(['task-cancel', 'task-reset']);
});

it('sends suggestions request-bound: features kept, no key recorded', async () => {
  const reported: Array<string | null> = [];
  await chatTransportFetch(
    '/api/chat',
    {
      method: 'POST',
      body: JSON.stringify({
        suggestion_id: 'candidate',
        __suggestionAuthGeneration: 1,
        messages: [{ role: 'user', parts: [{ type: 'text', text: 'plan' }] }],
      }),
    },
    {
      model: 'auto',
      conversationId: null,
      onGeneration: vi.fn(),
      onSubmissionKey: (key) => reported.push(key),
    },
  );
  const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
  const body = JSON.parse(
    (calls[calls.length - 1][1] as RequestInit).body as string,
  );
  expect(body.client_features).toEqual(['task-cancel', 'task-reset']);
  expect(body.idempotency_key).toBeUndefined();
  expect(reported).toEqual([null]);
  expect(storage.length).toBe(0); // no pending submission the request lacks
});

it('reports the conversation the request was queued in with its key', async () => {
  const reported: Array<[string | null, string | null]> = [];
  await chatTransportFetch(
    '/api/chat',
    {
      method: 'POST',
      body: JSON.stringify({
        messages: [{ role: 'user', parts: [{ type: 'text', text: 'hi' }] }],
      }),
    },
    {
      model: 'auto',
      conversationId: 'conv-queued',
      onGeneration: vi.fn(),
      onSubmissionKey: (key, conversationId) =>
        reported.push([key, conversationId]),
    },
  );
  expect(reported[0][1]).toBe('conv-queued');
});
