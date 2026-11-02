import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { chatTransportFetch } from '../lib/chatTransportFetch';
import { historyRecoveryCode } from '../lib/historyRecovery';
import {
  blockSubmission,
  clearPendingSubmissions,
  clearSubmissionBlock,
  finishedSubmissionKeys,
  pendingSubmission,
  promotePendingSubmission,
  recordSubmissionTask,
  registerSubmission,
  settlePendingSubmission,
  submissionBlock,
  unresolvedSubmissions,
  PENDING_SUBMISSION_TTL_MS,
} from '../lib/pendingSubmission';
import { POST } from '../app/api/chat/route';
import { createAuthTestStorage } from './helpers/authCookieEnvironment';

vi.mock('../lib/auth', () => ({
  getAuthGeneration: () => 1,
  getAuthHeader: () => 'Bearer synthetic',
  refreshIfNeeded: async () => {},
}));

beforeEach(() => {
  vi.stubGlobal('localStorage', createAuthTestStorage());
  clearPendingSubmissions();
  vi.stubGlobal('fetch', vi.fn());
});
afterEach(() => vi.unstubAllGlobals());

function register() {
  registerSubmission(
    'original',
    {
      scope: 'conversation',
      requestConversationId: 'conversation',
      model: 'selected',
      provider: 'selected',
    },
    100,
  );
}
const detail = (code: string) => ({
  detail: { code, task_id: null, retry_safe: false },
});
const send = () =>
  chatTransportFetch(
    '/api/chat',
    {
      method: 'POST',
      body: JSON.stringify({
        idempotency_key: 'original',
        messages: [{ role: 'user', content: 'synthetic' }],
      }),
    },
    { model: 'auto', conversationId: 'conversation', onGeneration: vi.fn() },
  );

it.each(['restore_recovery_required', 'history_unavailable'] as const)(
  'keeps %s keys, controls and refusal across TTL and never sends them',
  async (code) => {
    register();
    blockSubmission('original', code);
    promotePendingSubmission('original', 'promoted');
    recordSubmissionTask('original', 'original-task');
    settlePendingSubmission('original');
    expect(
      unresolvedSubmissions(100 + PENDING_SUBMISSION_TTL_MS + 1).map(
        (x) => x.key,
      ),
    ).toEqual(['original']);
    expect(pendingSubmission('original')).toMatchObject({
      requestConversationId: 'conversation',
      model: 'selected',
      provider: 'selected',
      historyBlocked: code,
    });
    await expect(send()).rejects.toThrow(/retained/);
    expect(fetch).not.toHaveBeenCalled();
    expect(submissionBlock('original')).toBe(code);
  },
);

it('does not settle a needs-attention task or infer that it is safe to retry', () => {
  register();
  recordSubmissionTask('original', 'task');
  expect(
    finishedSubmissionKeys(
      {
        id: 'conversation',
        latestTask: { id: 'task', status: 'needs_attention' },
      },
      () => true,
    ),
  ).toEqual([]);
  settlePendingSubmission('original');
  expect(pendingSubmission('original')).not.toBeNull();
  expect(submissionBlock('original')).toBe('inference_dispatch_unresolved');
});

it('allows settlement only after a successful observation explicitly clears the refusal', () => {
  register();
  blockSubmission('original', 'restore_recovery_required');
  clearSubmissionBlock('original');
  settlePendingSubmission('original');
  expect(pendingSubmission('original')).toBeNull();
});

it('clears in-memory and persisted refusals on account sign-out', () => {
  register();
  blockSubmission('original', 'history_unavailable');
  clearPendingSubmissions();
  expect(submissionBlock('original')).toBeNull();
  expect(unresolvedSubmissions()).toEqual([]);
});

it.each([409, 503])(
  'preserves a %s response without a proxy stream, replacement key or send retry',
  async (status) => {
    const code =
      status === 409 ? 'restore_recovery_required' : 'history_unavailable';
    const body = detail(code);
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const proxied = await POST(
      new Request('http://test/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          idempotency_key: 'original',
          messages: [{ role: 'user', content: 'synthetic' }],
        }),
      }),
    );
    expect(proxied.status).toBe(status);
    expect(proxied.headers.get('cache-control')).toBe('no-store');
    expect(await proxied.json()).toEqual(body);
    expect(fetch).toHaveBeenCalledOnce();
    vi.mocked(fetch).mockClear();
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify(body), { status }),
    );
    await expect(send()).rejects.toThrow(/retained/);
    expect(fetch).toHaveBeenCalledOnce();
    expect(pendingSubmission('original')?.historyBlocked).toBe(code);
    await expect(send()).rejects.toThrow(/retained/);
    expect(fetch).toHaveBeenCalledOnce();
  },
);

it('never tries another backend after a keyed POST transport failure', async () => {
  vi.mocked(fetch).mockRejectedValue(
    new TypeError('synthetic uncertain transport'),
  );
  const response = await POST(
    new Request('http://test/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        idempotency_key: 'original',
        messages: [{ role: 'user', content: 'synthetic' }],
      }),
    }),
  );
  await response.text();
  expect(fetch).toHaveBeenCalledOnce();
});

it('does not trust retry_safe strings or unrelated error codes as recovery facts', () => {
  expect(
    historyRecoveryCode({
      detail: { code: 'restore_recovery_required', retry_safe: 'false' },
    }),
  ).toBeNull();
  expect(historyRecoveryCode(detail('unrelated'))).toBeNull();
});
