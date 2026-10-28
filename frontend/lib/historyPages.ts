import { normalizeDaemonMessage, type DaemonMessage } from './chatMessages';

export const HISTORY_PAGE_SIZE = 100;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const STAMP =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2}))$/;

export class InvalidHistoryPage extends Error {
  constructor() {
    super('Invalid history page; previous history was retained');
    this.name = 'InvalidHistoryPage';
  }
}

function requirePage(condition: unknown): asserts condition {
  if (!condition) throw new InvalidHistoryPage();
}

function record(value: unknown): Record<string, unknown> {
  requirePage(
    typeof value === 'object' && value !== null && !Array.isArray(value),
  );
  return value as Record<string, unknown>;
}

function identity(value: unknown): string {
  requirePage(typeof value === 'string' && UUID.test(value));
  return value;
}

/** Exact PostgreSQL microseconds. Never round-trip opaque cursors through Date. */
export function historyTimestamp(value: unknown): bigint {
  requirePage(typeof value === 'string');
  const parts = STAMP.exec(value);
  requirePage(parts);
  const [year, month, day, hour, minute, second] = parts
    .slice(1, 7)
    .map(Number);
  const offsetHour = Number(parts[10] ?? 0);
  const offsetMinute = Number(parts[11] ?? 0);
  requirePage(
    year >= 1 &&
      month >= 1 &&
      month <= 12 &&
      day >= 1 &&
      day <= 31 &&
      hour <= 23 &&
      minute <= 59 &&
      second <= 59 &&
      offsetHour <= 23 &&
      offsetMinute <= 59,
  );
  // setUTCFullYear avoids Date.UTC's special interpretation of years 0..99.
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  date.setUTCHours(hour, minute, second, 0);
  requirePage(
    date.getUTCFullYear() === year &&
      date.getUTCMonth() === month - 1 &&
      date.getUTCDate() === day,
  );
  const offset = (offsetHour * 60 + offsetMinute) * (parts[9] === '-' ? -1 : 1);
  return (
    BigInt(date.getTime() - offset * 60000) * BigInt(1000) +
    BigInt((parts[7] ?? '').padEnd(6, '0'))
  );
}

export interface MessageBoundary {
  readonly id: string;
  readonly created_at: string;
}

export type PersistedHistoryMessage = DaemonMessage & MessageBoundary;

export function compareMessageBoundary(
  a: MessageBoundary,
  b: MessageBoundary,
): number {
  const at = historyTimestamp(a.created_at);
  const bt = historyTimestamp(b.created_at);
  return at < bt ? -1 : at > bt ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

export interface HistoryPage {
  has_more: boolean;
  next_cursor: string | null;
  start_cursor: string | null;
  end_cursor: string | null;
}

function cursor(value: unknown): string | null {
  if (value === null) return null;
  requirePage(
    typeof value === 'string' && /^[A-Za-z0-9_-]{1,1024}$/.test(value),
  );
  return value;
}

function pageMetadata(
  value: unknown,
  length: number,
  continuation: 'start_cursor' | 'end_cursor',
  previousCursor?: string | null,
): HistoryPage {
  const raw = record(value);
  requirePage(typeof raw.has_more === 'boolean');
  const page = {
    has_more: raw.has_more,
    next_cursor: cursor(raw.next_cursor),
    start_cursor: cursor(raw.start_cursor),
    end_cursor: cursor(raw.end_cursor),
  };
  requirePage(page.has_more === (page.next_cursor !== null));
  if (length === 0) {
    requirePage(
      !page.has_more && page.start_cursor === null && page.end_cursor === null,
    );
  } else {
    requirePage(page.start_cursor !== null && page.end_cursor !== null);
    requirePage((length === 1) === (page.start_cursor === page.end_cursor));
    if (page.has_more) requirePage(page.next_cursor === page[continuation]);
    // A server continuation must advance even if every displayed row duplicates
    // an earlier page or gets hidden as an empty draft by the list renderer.
    if (previousCursor != null)
      requirePage(page[continuation] !== previousCursor);
  }
  return page;
}

export interface ConversationHistoryRow {
  id: string;
  user_id: string;
  title: string | null;
  created_at: string;
  pinned: boolean;
  last_activity_at: string | null;
  updated_at: string;
  [key: string]: unknown;
}

export function compareConversationBoundary(
  a: ConversationHistoryRow,
  b: ConversationHistoryRow,
): number {
  if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
  const at = historyTimestamp(a.last_activity_at ?? a.updated_at);
  const bt = historyTimestamp(b.last_activity_at ?? b.updated_at);
  return at > bt ? -1 : at < bt ? 1 : a.id > b.id ? -1 : a.id < b.id ? 1 : 0;
}

/** Validate the entire page before it can update content OR continuation. */
export function parseConversationHistoryPage(
  value: unknown,
  previousCursor?: string | null,
) {
  const raw = record(value);
  requirePage(
    Array.isArray(raw.conversations) &&
      raw.conversations.length <= HISTORY_PAGE_SIZE,
  );
  requirePage(
    Number.isSafeInteger(raw.total) &&
      (raw.total as number) >= raw.conversations.length,
  );
  const ids = new Set<string>();
  let owner: string | undefined;
  const conversations = raw.conversations.map(
    (candidate): ConversationHistoryRow => {
      const row = record(candidate);
      const id = identity(row.id);
      const userId = identity(row.user_id);
      requirePage(!ids.has(id) && (owner === undefined || owner === userId));
      ids.add(id);
      owner = userId;
      requirePage(
        typeof row.pinned === 'boolean' &&
          (row.title === null || typeof row.title === 'string'),
      );
      historyTimestamp(row.created_at);
      historyTimestamp(row.updated_at);
      if (row.last_activity_at !== null) historyTimestamp(row.last_activity_at);
      return { ...row, id, user_id: userId } as ConversationHistoryRow;
    },
  );
  requirePage(
    conversations.every(
      (row, index) =>
        index === 0 ||
        compareConversationBoundary(conversations[index - 1], row) < 0,
    ),
  );
  return {
    conversations,
    total: raw.total as number,
    page: pageMetadata(
      raw.page,
      conversations.length,
      'end_cursor',
      previousCursor,
    ),
  };
}

export function parseMessageHistoryPage(
  value: unknown,
  conversationId: string,
  previousCursor?: string | null,
) {
  const raw = record(value);
  requirePage(identity(raw.id) === identity(conversationId));
  const owner = identity(raw.user_id);
  requirePage(
    Array.isArray(raw.messages) && raw.messages.length <= HISTORY_PAGE_SIZE,
  );
  const ids = new Set<string>();
  const messages = raw.messages.map((candidate): PersistedHistoryMessage => {
    const row = record(candidate);
    const id = identity(row.id);
    requirePage(!ids.has(id));
    ids.add(id);
    requirePage(
      identity(row.conversation_id) === conversationId &&
        identity(row.user_id) === owner,
    );
    historyTimestamp(row.created_at);
    requirePage(typeof row.content === 'string');
    requirePage(
      row.role === 'system' || row.role === 'user' || row.role === 'assistant',
    );
    const normalized = normalizeDaemonMessage(row);
    requirePage(normalized);
    return normalized as PersistedHistoryMessage;
  });
  requirePage(
    messages.every(
      (row, index) =>
        index === 0 || compareMessageBoundary(messages[index - 1], row) < 0,
    ),
  );
  return {
    conversation: raw,
    messages,
    page: pageMetadata(
      raw.message_page,
      messages.length,
      'start_cursor',
      previousCursor,
    ),
  };
}

/**
 * The cut is captured when a persisted tail is adopted into SDK state, never
 * inferred from local messages or recomputed from a newer polling response.
 * SDK owns everything on/after that cut, even when its live IDs differ from DB
 * IDs. Loading older pages cannot change SDK submission/regeneration payloads.
 */
export function projectDisplayedHistory(
  persisted: readonly PersistedHistoryMessage[],
  sdkTail: readonly DaemonMessage[],
  adoptedOldest: MessageBoundary | null,
): DaemonMessage[] {
  if (adoptedOldest === null) return [...sdkTail];
  const sdkIds = new Set(sdkTail.map((message) => message.id));
  const prefix = new Map<string, PersistedHistoryMessage>();
  for (const message of persisted) {
    if (
      compareMessageBoundary(message, adoptedOldest) < 0 &&
      !sdkIds.has(message.id)
    ) {
      prefix.set(message.id, message);
    }
  }
  return [...[...prefix.values()].sort(compareMessageBoundary), ...sdkTail];
}
