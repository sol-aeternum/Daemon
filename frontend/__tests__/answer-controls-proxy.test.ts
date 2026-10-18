import { afterEach, expect, it, vi } from 'vitest';
import { POST } from '../app/api/chat/route';

afterEach(() => vi.unstubAllGlobals());

it.each([
  {},
  { answer_detail: null, research_effort: null },
  { answer_detail: 'brief', research_effort: 'research' },
  ...['', 'Balanced', false, 0, [], {}].map((value) => ({
    answer_detail: value,
    research_effort: value,
  })),
])(
  'forwards exact controls without defaults/coercion, preserving backend validation: %j',
  async (controls) => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response('{"detail":"synthetic validation"}', { status: 422 }),
      );
    vi.stubGlobal('fetch', fetchMock);
    const response = await POST(
      new Request('http://test/api/chat', {
        method: 'POST',
        body: JSON.stringify({
          messages: [{ role: 'user', content: 'question' }],
          idempotency_key: 'same-key',
          ...controls,
        }),
      }),
    );
    // Existing AI SDK bridge uses typed refusal frames, not raw HTTP errors.
    const stream = await response.text();
    expect(stream).toContain('"type":"request_rejected","status":422');
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    for (const field of ['answer_detail', 'research_effort']) {
      if (field in controls)
        expect(body[field]).toEqual(controls[field as keyof typeof controls]);
      else expect(body).not.toHaveProperty(field);
    }
    expect(
      new Headers(fetchMock.mock.calls[0][1].headers).get('Idempotency-Key'),
    ).toBe('same-key');
  },
);

it('does not rewrite changed explicit controls under the same key into replay', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response('{"detail":{"code":"idempotency_conflict"}}', {
      status: 409,
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
  const response = await POST(
    new Request('http://test/api/chat', {
      method: 'POST',
      body: JSON.stringify({
        messages: [{ role: 'user', content: 'question' }],
        idempotency_key: 'already-used',
        answer_detail: 'detailed',
        research_effort: 'auto',
      }),
    }),
  );
  const stream = await response.text();
  expect(stream).toContain(
    '"type":"request_rejected","status":409,"code":"idempotency_conflict"',
  );
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
    answer_detail: 'detailed',
    research_effort: 'auto',
  });
});
