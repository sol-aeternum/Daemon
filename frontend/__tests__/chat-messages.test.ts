import { describe, expect, it } from 'vitest';

import {
  getDaemonDataEvents,
  getDaemonMessageText,
  normalizeDaemonMessage,
  normalizeDaemonMessages,
  type DaemonMessage,
} from '../lib/chatMessages';

describe('chat message normalization', () => {
  it('converts persisted content messages into SDK 7 UI message parts', () => {
    const message = normalizeDaemonMessage({
      id: 'message_1',
      role: 'assistant',
      content: 'Persisted reply',
      metadata: { council_events: [] },
      tool_calls: [{ name: 'get_time' }],
      reasoning_text: 'Reasoning',
    });

    expect(message).toEqual(
      expect.objectContaining({
        id: 'message_1',
        role: 'assistant',
        content: 'Persisted reply',
        metadata: { council_events: [] },
        tool_calls: [{ name: 'get_time' }],
        reasoning_text: 'Reasoning',
        parts: [{ type: 'text', text: 'Persisted reply' }],
      }),
    );
    expect(getDaemonMessageText(message as DaemonMessage)).toBe(
      'Persisted reply',
    );
  });

  it('reads streamed SDK 7 text parts when legacy content is absent', () => {
    const message: DaemonMessage = {
      id: 'message_2',
      role: 'assistant',
      metadata: {},
      parts: [
        { type: 'text', text: 'Streamed ' },
        { type: 'text', text: 'reply' },
      ],
    };

    expect(getDaemonMessageText(message)).toBe('Streamed reply');
  });

  it('extracts SDK 7 data parts for the existing event archive', () => {
    const event = {
      type: 'routing' as const,
      model: 'openrouter/example',
    };
    const message: DaemonMessage = {
      id: 'message_3',
      role: 'assistant',
      metadata: {},
      parts: [
        { type: 'data-event', data: event },
        { type: 'text', text: 'Reply' },
      ],
    };

    expect(getDaemonDataEvents([message])).toEqual([event]);
  });

  it('filters malformed persisted records and supplies a stable fallback id', () => {
    expect(
      normalizeDaemonMessages([
        null,
        { id: 'bad', role: 'unknown', content: 'ignored' },
        { role: 'user', content: 'kept' },
      ]),
    ).toEqual([
      expect.objectContaining({
        id: 'persisted-message-2',
        role: 'user',
        parts: [{ type: 'text', text: 'kept' }],
      }),
    ]);
  });
});
