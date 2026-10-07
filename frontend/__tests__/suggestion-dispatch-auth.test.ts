import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DefaultChatTransport } from 'ai';
import { chatTransportFetch } from '../lib/chatTransportFetch';

const auth = vi.hoisted(() => ({ generation: 7, refresh: vi.fn() }));
vi.mock('../lib/auth', () => ({
  getAuthGeneration: () => auth.generation,
  getAuthHeader: () => `Bearer account-${auth.generation}`,
  refreshIfNeeded: () => auth.refresh(),
}));

const prompt = 'Private fictional suggestion from account seven';
const scope = {
  model: 'auto',
  conversationId: 'old-draft',
  onGeneration: vi.fn(),
};
const init = () => ({
  method: 'POST',
  body: JSON.stringify({
    suggestion_id: 'candidate',
    __suggestionAuthGeneration: 7,
    messages: [{ role: 'user', parts: [{ type: 'text', text: prompt }] }],
  }),
});

beforeEach(() => {
  auth.generation = 7;
  auth.refresh.mockReset().mockResolvedValue(undefined);
  scope.onGeneration.mockClear();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('')));
});
afterEach(() => vi.unstubAllGlobals());

it('rejects a queued SDK send when account switches before transport invocation', async () => {
  let invoke!: () => Promise<Response>;
  let scheduled!: () => void;
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    scheduled = resolve;
  });
  const dispatch = new Promise<void>((resolve) => {
    release = resolve;
  });
  const transport = new DefaultChatTransport({
    api: '/api/chat',
    fetch: (input, options) =>
      new Promise<Response>((resolve, reject) => {
        invoke = () => chatTransportFetch(input, options, scope);
        scheduled();
        // Test controls the exact gap between SDK queuing and transport execution.
        void dispatch.then(async () => {
          try {
            resolve(await invoke());
          } catch (error) {
            reject(error);
          }
        });
      }),
  });
  // Defer SDK preparation, keeping the activation-bound body immutable.
  const body = JSON.parse(init().body);
  const pending = transport.sendMessages({
    trigger: 'submit-message',
    messageId: undefined,
    abortSignal: undefined,
    chatId: 'fixture',
    messages: [
      { id: 'user', role: 'user', parts: [{ type: 'text', text: prompt }] },
    ],
    body,
  });
  await ready;
  auth.generation = 8;
  release();
  await expect(pending).rejects.toThrow('Authentication changed');
  expect(fetch).not.toHaveBeenCalled();
  expect(auth.refresh).not.toHaveBeenCalled();
  expect(scope.onGeneration).not.toHaveBeenCalled();
});

it('rejects revocation while credential refresh is awaited', async () => {
  let release!: () => void;
  auth.refresh.mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        release = resolve;
      }),
  );
  const pending = chatTransportFetch('/api/chat', init(), scope);
  auth.generation = 8;
  release();
  await expect(pending).rejects.toThrow('Authentication changed');
  expect(fetch).not.toHaveBeenCalled();
});

it('sends an unchanged lifetime once, with null destination and no internal guard', async () => {
  await chatTransportFetch('/api/chat', init(), scope);
  expect(fetch).toHaveBeenCalledTimes(1);
  const options = vi.mocked(fetch).mock.calls[0][1]!;
  expect(new Headers(options.headers).get('Authorization')).toBe(
    'Bearer account-7',
  );
  expect(JSON.parse(String(options.body))).toEqual({
    id: null,
    model: 'auto',
    suggestion_id: 'candidate',
    messages: [{ role: 'user', parts: [{ type: 'text', text: prompt }] }],
    // Declared so the bridge marks the turn request-bound; no idempotency key.
    client_features: ['task-cancel', 'task-reset'],
  });
});

it('refuses a suggestion missing its activation lifetime', async () => {
  await expect(
    chatTransportFetch(
      '/api/chat',
      { body: '{"suggestion_id":"candidate"}' },
      scope,
    ),
  ).rejects.toThrow('Authentication changed');
  expect(fetch).not.toHaveBeenCalled();
});
