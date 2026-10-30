/** Synthetic UI cursor responses for browser fixtures; no backend/provider calls. */
export const historyFixtureId = (n: number) =>
  `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
export const historyFixtureConversationId = historyFixtureId(1000);
const owner = historyFixtureId(9999);

type FixtureConversation = {
  id: string;
  created_at: string;
  updated_at: string;
  messages?: Array<{
    id: string;
    role: string;
    content: string;
    created_at?: string;
    metadata?: Record<string, unknown>;
    tool_calls?: unknown[];
    tool_results?: unknown[];
  }>;
  [key: string]: unknown;
};

function row(conversation: FixtureConversation) {
  return {
    ...conversation,
    user_id: owner,
    last_activity_at: conversation.last_activity_at ?? conversation.updated_at,
    message_count: conversation.messages?.length ?? 0,
  };
}

export function historyFixtureList(conversations: FixtureConversation[]) {
  const rows = conversations.map(row);
  return {
    conversations: rows,
    total: rows.length,
    page: {
      has_more: false,
      next_cursor: null,
      start_cursor: rows[0]?.id ?? null,
      end_cursor: rows.at(-1)?.id ?? null,
    },
  };
}

export function historyFixtureDetail(
  conversation: FixtureConversation,
  url: URL,
) {
  const rows = (conversation.messages ?? []).map((message, index) => ({
    ...message,
    id: historyFixtureId(
      message.id === 'system-context'
        ? 0
        : /^message-\d+$/.test(message.id)
          ? Number(message.id.slice(8))
          : index + 1,
    ),
    conversation_id: conversation.id,
    user_id: owner,
    created_at: message.created_at ?? conversation.created_at,
  }));
  const before = url.searchParams.get('before');
  const through = url.searchParams.get('through');
  const lower =
    through === null ? 0 : rows.findIndex((message) => message.id === through);
  const upper =
    before === null
      ? rows.length
      : rows.findIndex((message) => message.id === before);
  if (lower < 0 || upper < 0)
    throw new Error('Unknown synthetic history cursor');
  const candidates = rows.slice(lower, upper);
  const limit = Math.min(
    100,
    Math.max(1, Number(url.searchParams.get('limit') ?? 100)),
  );
  const selected = candidates.slice(-limit);
  const more = candidates.length > selected.length;
  return {
    ...row(conversation),
    messages: selected,
    message_page: {
      has_more: more,
      next_cursor: more ? selected[0].id : null,
      start_cursor: selected[0]?.id ?? null,
      end_cursor: selected.at(-1)?.id ?? null,
    },
  };
}
