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
import { parseSnapshotListPage } from '../lib/webSnapshots';
import { isRequestBound } from '../lib/chatMessages';

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
  it('models the existing empty source-list and request-bound stream contracts', () => {
    expect(
      parseSnapshotListPage(
        { snapshots: [], total: 0, limit: 20, offset: 0 },
        conversation.id,
        0,
      ).rows,
    ).toEqual([]);
    expect(
      isRequestBound({
        id: 'streaming-fixture',
        role: 'assistant',
        parts: [{ type: 'data-event', data: { type: 'request_bound' } }],
      }),
    ).toBe(true);
    expect(
      isRequestBound({ id: 'streaming-fixture', role: 'assistant', parts: [] }),
    ).toBe(false);
  });
  it('rejects the old production-fixture IDs while preserving tool/provenance metadata through cursor encoding', () => {
    expect(() =>
      parseConversationHistoryPage(
        historyFixtureList([{ ...conversation, id: 'midnight-fixture' }]),
      ),
    ).toThrow();
    expect(parseConversationHistoryPage(historyFixtureList([])).total).toBe(0);
    const metadata = {
      home_suggestion: {
        version: 1,
        suggestion_id: 'fixture-candidate',
        sources: [
          {
            conversation_id: historyFixtureId(2000),
            title: 'Release discussion',
            messages: [
              {
                id: 'source-turn',
                role: 'user',
                content: 'Create a release plan, but do not deploy yet.',
              },
            ],
          },
        ],
      },
    };
    const toolCalls = [{ name: 'generate_document', arguments: {} }];
    const encoded = historyFixtureDetail(
      {
        ...conversation,
        messages: [
          {
            id: 'persisted-question',
            role: 'user',
            content: 'Synthetic prompt',
            metadata,
            tool_calls: toolCalls,
          },
        ],
      },
      new URL('http://fixture/?pagination=cursor&limit=100'),
    );
    const parsed = parseMessageHistoryPage(encoded, conversation.id);
    expect(parsed.messages).toHaveLength(1);
    expect(parsed.messages[0].metadata).toEqual(metadata);
    expect(parsed.messages[0].tool_calls).toEqual(toolCalls);
    expect(parsed.messages[0].id).toBe(historyFixtureId(1));
  });
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
