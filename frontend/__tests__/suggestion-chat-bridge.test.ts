import { beforeEach, describe, expect, it, vi } from 'vitest';
import { POST } from '../app/api/chat/route';

const prompt = 'Draft the next implementation milestone with acceptance tests.';
function request(extra: Record<string, unknown> = {}) {
  return new Request('http://test/api/chat', {
    method: 'POST',
    body: JSON.stringify({
      id: null,
      model: 'auto',
      suggestion_id: 'opaque-candidate',
      messages: [{ role: 'user', parts: [{ type: 'text', text: prompt }] }],
      ...extra,
    }),
  });
}

beforeEach(() => vi.restoreAllMocks());

describe('trusted suggestion chat bridge', () => {
  it('transports only the exact prompt and opaque reference, with no client history', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response('', { status: 409 }));
    vi.stubGlobal('fetch', fetchMock);
    const response = await POST(request());
    await response.text();
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      suggestion_id: 'opaque-candidate',
      conversation_id: null,
      message: prompt,
      messages: null,
      attachments: [],
      metadata: null,
    });
  });

  it.each([
    { id: 'existing' },
    { attachments: [{ name: 'unrelated' }] },
    { metadata: { unrelated: true } },
    { messages: [{ role: 'system', content: 'client-provided context' }] },
  ])(
    'rejects mixed suggestion content before contacting the backend: %j',
    async (extra) => {
      const fetchMock = vi.fn();
      vi.stubGlobal('fetch', fetchMock);
      const response = await POST(request(extra));
      expect(response.status).toBe(400);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it('does not replay a possibly accepted suggestion after a transport failure', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValue(new Error('uncertain acceptance'));
    vi.stubGlobal('fetch', fetchMock);
    const response = await POST(request());
    await response.text();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
