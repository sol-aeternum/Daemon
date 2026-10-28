import { describe, expect, it } from 'vitest';
import {
  compareConversationBoundary,
  compareMessageBoundary,
  historyTimestamp,
  InvalidHistoryPage,
  parseConversationHistoryPage,
  parseMessageHistoryPage,
  projectDisplayedHistory,
} from '../lib/historyPages';
import type { DaemonMessage } from '../lib/chatMessages';

const id = (n: number) =>
  `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const OWNER = id(1);
const CONVERSATION = id(2);
const AT = '2026-10-28T01:02:03.123456+00:00';
const rawMessage = (n: number) => ({
  id: id(n),
  conversation_id: CONVERSATION,
  user_id: OWNER,
  role: 'user',
  content: 'repeated synthetic text',
  created_at: AT,
});
const row = (n: number) => ({
  id: id(n),
  user_id: OWNER,
  title: 'synthetic',
  pinned: false,
  created_at: AT,
  updated_at: AT,
  last_activity_at: AT,
});
const metadata = (
  length: number,
  more = false,
  side: 'start' | 'end' = 'start',
) => ({
  has_more: more,
  start_cursor: length ? 'opaque_first' : null,
  end_cursor: length > 1 ? 'opaque_last' : length ? 'opaque_first' : null,
  next_cursor: more
    ? side === 'start'
      ? 'opaque_first'
      : 'opaque_last'
    : null,
});
const detail = (length = 2, more = true) => ({
  id: CONVERSATION,
  user_id: OWNER,
  messages: Array.from({ length }, (_, n) => rawMessage(n + 10)),
  message_page: metadata(length, more),
});
const listing = (length = 2, more = true) => ({
  conversations: Array.from({ length }, (_, n) => row(1000 - n)),
  total: length + (more ? 1 : 0),
  page: metadata(length, more, 'end'),
});

describe('microsecond history tuples', () => {
  it('preserves submillisecond precision and equivalent timezone offsets', () => {
    expect(
      historyTimestamp(AT) - historyTimestamp(AT.replace('123456', '123455')),
    ).toBe(BigInt(1));
    expect(historyTimestamp(AT)).toBe(
      historyTimestamp('2026-10-28T10:32:03.123456+09:30'),
    );
    expect(historyTimestamp(AT)).toBe(
      historyTimestamp('2026-10-27T19:32:03.123456-05:30'),
    );
    expect(historyTimestamp('2026-10-28T01:02:03Z')).toBe(
      historyTimestamp('2026-10-28T01:02:03.000000+00:00'),
    );
    expect(historyTimestamp('0001-01-01T00:00:00Z')).toBeLessThan(
      historyTimestamp('0099-01-01T00:00:00Z'),
    );
  });

  it.each([
    '',
    'not-a-date',
    '2026-10-28',
    '2026-10-28T01:02:03',
    '2026-02-30T01:02:03Z',
    '2026-13-01T01:02:03Z',
    '2026-00-01T01:02:03Z',
    '2026-01-00T01:02:03Z',
    '0000-01-01T01:02:03Z',
    '2026-01-01T24:00:00Z',
    '2026-01-01T00:60:00Z',
    '2026-01-01T00:00:60Z',
    '2026-01-01T00:00:00+24:00',
    '2026-01-01T00:00:00+00:60',
    '2026-01-01T00:00:00.1234567Z',
    null,
    123,
  ])('rejects invalid or ambiguous timestamp %s', (value) => {
    expect(() => historyTimestamp(value)).toThrow(InvalidHistoryPage);
  });

  it('uses pin then exact activity then native UUID order, newest first', () => {
    const recent = {
      ...row(10),
      last_activity_at: AT.replace('123456', '123457'),
    };
    const pinned = { ...row(9), pinned: true };
    expect(
      [row(11), recent, pinned, row(12)]
        .sort(compareConversationBoundary)
        .map((item) => item.id),
    ).toEqual([id(9), id(10), id(12), id(11)]);
    expect(
      compareConversationBoundary(
        { ...row(11), last_activity_at: null },
        row(11),
      ),
    ).toBe(0);
    expect(compareMessageBoundary(rawMessage(11), rawMessage(12))).toBe(-1);
  });
});

describe('strict persisted page validation', () => {
  it.each([0, 1, 100])(
    'accepts terminal %i-message pages and keeps cursors verbatim',
    (length) => {
      const value = detail(length, false);
      const parsed = parseMessageHistoryPage(value, CONVERSATION);
      expect(parsed.messages).toHaveLength(length);
      expect(parsed.page).toEqual(value.message_page);
      expect(parsed.messages[0]?.created_at).toBe(length ? AT : undefined);
      expect(parsed.messages[0]?.id).toBe(length ? id(10) : undefined);
    },
  );

  it('does not mistake empty terminal pages for a lost list total', () => {
    const value = { ...listing(0, false), total: 234 };
    expect(parseConversationHistoryPage(value).total).toBe(234);
  });

  it('accepts nullable titles from ConversationOut without losing the continuation', () => {
    const value = listing();
    const conversations = value.conversations.map((item) => ({
      ...item,
      title: null,
    }));
    const parsed = parseConversationHistoryPage({ ...value, conversations });
    expect(parsed.conversations.every((item) => item.title === null)).toBe(
      true,
    );
    expect(parsed.page).toEqual(value.page);
  });

  it.each([
    { id: undefined },
    { id: '' },
    { id: 'persisted-message-0' },
    { id: 'not-a-uuid' },
    { conversation_id: id(99) },
    { user_id: id(99) },
    { created_at: undefined },
    { created_at: '2026-10-28T00:00:00' },
    { role: 'tool' },
    { content: null },
  ])(
    'rejects a whole page rather than normalizing away malformed rows %s',
    (patch) => {
      const value = detail();
      Object.assign(value.messages[1], patch);
      expect(() => parseMessageHistoryPage(value, CONVERSATION)).toThrow(
        InvalidHistoryPage,
      );
    },
  );

  it('rejects foreign conversation envelopes and nonarray/oversized pages', () => {
    expect(() => parseMessageHistoryPage(detail(), id(99))).toThrow(
      InvalidHistoryPage,
    );
    expect(() =>
      parseMessageHistoryPage({ ...detail(), messages: null }, CONVERSATION),
    ).toThrow(InvalidHistoryPage);
    expect(() => parseMessageHistoryPage(detail(101), CONVERSATION)).toThrow(
      InvalidHistoryPage,
    );
    expect(() => parseConversationHistoryPage(listing(101))).toThrow(
      InvalidHistoryPage,
    );
  });

  it('requires strict chronological order and unique real IDs within each page', () => {
    const reversed = detail();
    reversed.messages.reverse();
    expect(() => parseMessageHistoryPage(reversed, CONVERSATION)).toThrow(
      InvalidHistoryPage,
    );
    const duplicate = detail();
    duplicate.messages[1] = duplicate.messages[0];
    expect(() => parseMessageHistoryPage(duplicate, CONVERSATION)).toThrow(
      InvalidHistoryPage,
    );
    const shuffledList = listing();
    shuffledList.conversations.reverse();
    expect(() => parseConversationHistoryPage(shuffledList)).toThrow(
      InvalidHistoryPage,
    );
  });

  it.each([
    { has_more: 'true' },
    { has_more: true, next_cursor: null },
    { has_more: false, next_cursor: 'opaque_first' },
    { start_cursor: null },
    { end_cursor: null },
    { next_cursor: 'opaque_last' },
    { start_cursor: '' },
    { start_cursor: '*' },
    { start_cursor: 'λ' },
    { start_cursor: 'a'.repeat(1025) },
    { end_cursor: 'opaque_first' },
  ])('rejects incoherent or malformed message metadata %s', (patch) => {
    const value = detail();
    Object.assign(value.message_page, patch);
    expect(() => parseMessageHistoryPage(value, CONVERSATION)).toThrow(
      InvalidHistoryPage,
    );
  });

  it('rejects stalled continuations even if the current page is terminal', () => {
    expect(() =>
      parseMessageHistoryPage(detail(), CONVERSATION, 'opaque_first'),
    ).toThrow(InvalidHistoryPage);
    expect(() =>
      parseMessageHistoryPage(detail(2, false), CONVERSATION, 'opaque_first'),
    ).toThrow(InvalidHistoryPage);
    expect(() =>
      parseConversationHistoryPage(listing(), 'opaque_last'),
    ).toThrow(InvalidHistoryPage);
    // An empty page after a deletion is a valid terminal observation.
    expect(
      parseMessageHistoryPage(detail(0, false), CONVERSATION, 'opaque_first')
        .page.has_more,
    ).toBe(false);
  });

  it('refuses page metadata that invents more rows from an empty page', () => {
    const value = detail(0, false);
    value.message_page = {
      has_more: true,
      start_cursor: 'a',
      end_cursor: 'a',
      next_cursor: 'a',
    };
    expect(() => parseMessageHistoryPage(value, CONVERSATION)).toThrow(
      InvalidHistoryPage,
    );
  });

  it.each([-1, 1, 1.5, Number.MAX_SAFE_INTEGER + 1, '3'])(
    'rejects invalid full totals %s',
    (total) => {
      expect(() =>
        parseConversationHistoryPage({ ...listing(), total }),
      ).toThrow(InvalidHistoryPage);
    },
  );

  it('validates conversation owners and boundary fields', () => {
    const value = listing();
    value.conversations[1].user_id = id(99);
    expect(() => parseConversationHistoryPage(value)).toThrow(
      InvalidHistoryPage,
    );
    for (const patch of [
      { pinned: null },
      { title: undefined },
      { id: '' },
      { last_activity_at: undefined },
    ]) {
      const candidate = listing();
      Object.assign(candidate.conversations[1], patch);
      expect(() => parseConversationHistoryPage(candidate)).toThrow(
        InvalidHistoryPage,
      );
    }
  });
});

describe('display-only persisted prefix and SDK-owned tail', () => {
  const persisted = parseMessageHistoryPage(
    detail(5, false),
    CONVERSATION,
  ).messages;
  const local = (id: string): DaemonMessage => ({
    id,
    role: 'user',
    parts: [{ type: 'text', text: 'repeated synthetic text' }],
  });

  it('freezes ownership at adoption, not at the latest polling page', () => {
    const cut = persisted[2];
    const tail = [persisted[2], local('local-user'), local('local-reply')];
    const incoming = parseMessageHistoryPage(
      { ...detail(1, false), messages: [rawMessage(50)] },
      CONVERSATION,
    ).messages;
    const display = projectDisplayedHistory(
      [...persisted, ...incoming],
      tail,
      cut,
    );
    expect(display.map((item) => item.id)).toEqual([
      id(10),
      id(11),
      id(12),
      'local-user',
      'local-reply',
    ]);
    // Saved forms with identical text but different IDs in the owned range are
    // not appended, nor heuristically matched to the live local messages.
    expect(display).not.toContain(persisted[3]);
    expect(display).not.toContain(incoming[0]);
    expect(tail).toHaveLength(3);
    expect(display[3]).toBe(tail[1]);
  });

  it('prepends older rows without altering SDK outbound messages or their order', () => {
    const sdk = Object.freeze([persisted[2], local('local')]);
    const beforePayload = JSON.stringify({ messages: sdk });
    const initial = projectDisplayedHistory([persisted[2]], sdk, persisted[2]);
    const extended = projectDisplayedHistory(persisted, sdk, persisted[2]);
    expect(initial).toHaveLength(2);
    expect(extended).toHaveLength(4);
    expect(JSON.stringify({ messages: sdk })).toBe(beforePayload);
    expect(extended.slice(-2)).toEqual(sdk);
  });

  it('uses no persisted prefix for a new chat before its first safe tail adoption', () => {
    const tail = [local('live-new-user'), local('live-new-reply')];
    expect(projectDisplayedHistory(persisted, tail, null)).toEqual(tail);
  });

  it('deduplicates only actual shared IDs and advances the cut only on explicit adoption', () => {
    const tail = [persisted[1], persisted[3]];
    const display = projectDisplayedHistory(
      [...persisted].reverse().concat(persisted[0]),
      tail,
      persisted[3],
    );
    expect(display.map((message) => message.id)).toEqual([
      id(10),
      id(12),
      id(11),
      id(13),
    ]);
    const adopted = projectDisplayedHistory(
      persisted,
      [persisted[4]],
      persisted[4],
    );
    expect(adopted).toEqual(persisted);
  });
});
