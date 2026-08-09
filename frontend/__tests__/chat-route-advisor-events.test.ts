import { beforeEach, describe, expect, it, vi } from 'vitest';

let capturedWrites: Array<{ partType: string; value: unknown }> = [];

vi.mock('ai', () => ({
  createDataStreamResponse: async ({
    execute,
  }: {
    execute: (dataStream: {
      write: (part: { partType: string; value: unknown }) => void;
    }) => Promise<void>;
  }) => {
    capturedWrites = [];
    await execute({
      write: (part) => {
        capturedWrites.push(part);
      },
    });

    return new Response(JSON.stringify(capturedWrites), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  },
}));

vi.mock('@ai-sdk/ui-utils', () => ({
  formatDataStreamPart: (partType: string, value: unknown) => ({
    partType,
    value,
  }),
}));

import { POST } from '../app/api/chat/route';

function buildSseResponse(frames: string[]): Response {
  return new Response(`${frames.join('\n\n')}\n\n`, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

function encodeFrame(eventType: string, data: Record<string, unknown>): string {
  return `event: ${eventType}\ndata: ${JSON.stringify(data)}`;
}

describe('chat route advisor event bridge', () => {
  beforeEach(() => {
    capturedWrites = [];
    vi.restoreAllMocks();
  });

  it('bootstraps advisor-only data events without sending nested text to the main reply stream', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        buildSseResponse([
          encodeFrame('advisor_start', {
            id: 'evt_advisor_start_1',
            request_id: 'req_1',
            data: {
              advisor_id: 'advisor_coding_1',
              domain: 'coding',
              difficulty: 'high',
              model: 'openrouter/anthropic/claude-opus-4.7',
              tool_call_id: 'call_consult_advisor',
            },
          }),
          encodeFrame('advisor_text_delta', {
            id: 'evt_advisor_text_1',
            request_id: 'req_1',
            data: {
              advisor_id: 'advisor_coding_1',
              text: 'Nested advisor text.',
            },
          }),
          encodeFrame('tool_call', {
            id: 'evt_tool_call_1',
            request_id: 'req_1',
            data: {
              advisor_id: 'advisor_coding_1',
              name: 'get_time',
              arguments: { format: 'iso' },
              tool_call_id: 'call_nested_1',
            },
          }),
          encodeFrame('tool_result', {
            id: 'evt_tool_result_1',
            request_id: 'req_1',
            data: {
              advisor_id: 'advisor_coding_1',
              name: 'get_time',
              result: { time: '2026-04-19T12:34:56Z' },
              tool_call_id: 'call_nested_1',
            },
          }),
          encodeFrame('advisor_end', {
            id: 'evt_advisor_end_1',
            request_id: 'req_1',
            data: {
              advisor_id: 'advisor_coding_1',
              status: 'completed',
              tokens_in: 21,
              tokens_out: 13,
            },
          }),
          encodeFrame('final', {
            id: 'evt_final_1',
            request_id: 'req_1',
            data: {
              text: 'Main reply.',
            },
          }),
        ]),
      ),
    );

    const response = await POST(
      new Request('http://test/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: [
            { role: 'user', content: 'Should we consult an advisor?' },
          ],
          id: 'conv_1',
        }),
      }),
    );

    const writes = (await response.json()) as Array<{
      partType: string;
      value: unknown;
    }>;
    const textParts = writes
      .filter((part) => part.partType === 'text')
      .map((part) => part.value);
    const dataEvents = writes
      .filter((part) => part.partType === 'data')
      .flatMap((part) => part.value as Array<Record<string, unknown>>);

    expect(textParts).toEqual(['', 'Main reply.']);
    expect(textParts).not.toContain('Nested advisor text.');

    expect(dataEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: 'advisor_start',
          advisor_id: 'advisor_coding_1',
          tool_call_id: 'call_consult_advisor',
        }),
        expect.objectContaining({
          type: 'advisor_text_delta',
          advisor_id: 'advisor_coding_1',
          content: 'Nested advisor text.',
        }),
        expect.objectContaining({
          type: 'tool_call',
          advisor_id: 'advisor_coding_1',
          tool_call_id: 'call_nested_1',
        }),
        expect.objectContaining({
          type: 'tool_result',
          advisor_id: 'advisor_coding_1',
          tool_call_id: 'call_nested_1',
        }),
        expect.objectContaining({
          type: 'advisor_end',
          advisor_id: 'advisor_coding_1',
          tokens_in: 21,
          tokens_out: 13,
        }),
      ]),
    );
  });

  it('keeps non-advisor streams backward compatible', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        buildSseResponse([
          encodeFrame('token', {
            id: 'evt_token_1',
            request_id: 'req_plain',
            data: { text: 'Top-level reply.' },
          }),
          encodeFrame('tool_call', {
            id: 'evt_tool_call_plain_1',
            request_id: 'req_plain',
            data: {
              name: 'web_search',
              arguments: { query: 'daemon' },
            },
          }),
          encodeFrame('tool_result', {
            id: 'evt_tool_result_plain_1',
            request_id: 'req_plain',
            data: {
              name: 'web_search',
              result: { hits: 3 },
            },
          }),
        ]),
      ),
    );

    const response = await POST(
      new Request('http://test/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: [{ role: 'user', content: 'Search for daemon' }],
          id: 'conv_plain',
        }),
      }),
    );

    const writes = (await response.json()) as Array<{
      partType: string;
      value: unknown;
    }>;
    const textParts = writes
      .filter((part) => part.partType === 'text')
      .map((part) => part.value);
    const dataEvents = writes
      .filter((part) => part.partType === 'data')
      .flatMap((part) => part.value as Array<Record<string, unknown>>);

    expect(textParts).toEqual(['Top-level reply.']);
    expect(
      dataEvents.some((event) => String(event.type).startsWith('advisor_')),
    ).toBe(false);
    expect(dataEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ type: 'tool_call', name: 'web_search' }),
        expect.objectContaining({ type: 'tool_result', name: 'web_search' }),
      ]),
    );
  });
});
