import type { UIMessage } from 'ai';

import type { ChatEvent } from './events';

export type DaemonDataParts = {
  event: ChatEvent;
};

export type DaemonMessage = UIMessage<
  Record<string, unknown>,
  DaemonDataParts
> & {
  content?: string;
  model?: string | null;
  status?: string | null;
  tool_calls?: unknown;
  tool_results?: unknown;
  advisor_traces?: unknown;
  reasoning_text?: string | null;
  reasoning_duration_secs?: number | null;
  reasoning_model?: string | null;
  created_at?: string;
  updated_at?: string | null;
};

const MESSAGE_ROLES: ReadonlySet<string> = new Set([
  'system',
  'user',
  'assistant',
]);

const toRecord = (value: unknown): Record<string, unknown> | undefined => {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
};

export function normalizeDaemonMessages(value: unknown): DaemonMessage[] {
  if (!Array.isArray(value)) return [];

  return value.flatMap((candidate, index) => {
    const message = normalizeDaemonMessage(candidate, index);
    return message ? [message] : [];
  });
}

export function normalizeDaemonMessage(
  value: unknown,
  index = 0,
): DaemonMessage | undefined {
  const record = toRecord(value);
  if (!record) return undefined;

  const rawRole = record.role;
  if (typeof rawRole !== 'string' || !MESSAGE_ROLES.has(rawRole)) {
    return undefined;
  }

  const role = rawRole as DaemonMessage['role'];
  const content = typeof record.content === 'string' ? record.content : '';
  const id =
    typeof record.id === 'string' && record.id.length > 0
      ? record.id
      : `persisted-message-${index}`;
  const metadata = toRecord(record.metadata) || {};

  return {
    ...record,
    id,
    role,
    content,
    metadata,
    parts: content.length > 0 ? [{ type: 'text', text: content }] : [],
  } as DaemonMessage;
}

export function getDaemonMessageText(message: DaemonMessage): string {
  if (typeof message.content === 'string' && message.content.length > 0) {
    return message.content;
  }

  return message.parts
    .filter((part) => part.type === 'text')
    .map((part) => part.text)
    .join('');
}

export function getDaemonDataEvents(messages: DaemonMessage[]): ChatEvent[] {
  return messages.flatMap((message) =>
    message.parts.flatMap((part) =>
      part.type === 'data-event' ? [part.data] : [],
    ),
  );
}
