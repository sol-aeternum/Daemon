import { describe, expect, it } from 'vitest';
import {
  historyFixtureConversationId,
  historyFixtureId,
  historyFixtureList,
  historyFixtureDetail,
} from '../e2e/history-fixtures';
import {
  parseConversationHistoryPage,
  parseMessageHistoryPage,
} from '../lib/historyPages';

const conversation = {
  id: historyFixtureConversationId,
  title: 'Synthetic browser thread',
  pinned: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  messages: [
    {
      id: 'system-context',
      role: 'system',
      content: 'Synthetic internal context',
    },
    ...Array.from({ length: 100 }, (_, index) => ({
      id: `message-${index + 1}`,
      role: 'assistant',
      content: `Synthetic ${index + 1}`,
    })),
  ],
};

describe('cursor-compatible legacy browser fixtures', () => {
  it('produces whole-page-valid owner-qualified list and empty detail metadata', () => {
    expect(
      parseConversationHistoryPage(historyFixtureList([conversation])).total,
    ).toBe(1);
    const empty = historyFixtureDetail(
      { ...conversation, messages: [] },
      new URL('http://fixture/?pagination=cursor'),
    );
    expect(parseMessageHistoryPage(empty, conversation.id).messages).toEqual(
      [],
    );
    expect(empty.message_page).toEqual({
      has_more: false,
      next_cursor: null,
      start_cursor: null,
      end_cursor: null,
    });
  });

  it('bounds a 101-row fixture and advances before without changing persisted identities', () => {
    const tail = historyFixtureDetail(
      conversation,
      new URL('http://fixture/?pagination=cursor'),
    );
    expect(
      parseMessageHistoryPage(tail, conversation.id).messages,
    ).toHaveLength(100);
    expect(tail.messages[0].id).toBe(historyFixtureId(1));
    const older = historyFixtureDetail(
      conversation,
      new URL(`http://fixture/?before=${tail.message_page.next_cursor}`),
    );
    expect(
      parseMessageHistoryPage(
        older,
        conversation.id,
        tail.message_page.next_cursor,
      ).messages.map((message) => message.id),
    ).toEqual([historyFixtureId(0)]);
    expect(older.message_page.has_more).toBe(false);
  });

  it('keeps through inclusive and its exhaustion separate from older continuation', () => {
    const tail = historyFixtureDetail(conversation, new URL('http://fixture/'));
    const range = historyFixtureDetail(
      conversation,
      new URL(`http://fixture/?through=${tail.message_page.start_cursor}`),
    );
    expect(range.messages).toHaveLength(100);
    expect(range.message_page.has_more).toBe(false);
    expect(tail.message_page.has_more).toBe(true);
    expect(() =>
      historyFixtureDetail(
        conversation,
        new URL('http://fixture/?before=unknown'),
      ),
    ).toThrow('Unknown synthetic history cursor');
  });
});
