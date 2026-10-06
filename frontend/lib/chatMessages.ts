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
  const metadata = toRecord(record.metadata) || {};
  const content = withTaskNotice(
    role,
    typeof record.content === 'string' ? record.content : '',
    metadata,
  );
  const id =
    typeof record.id === 'string' && record.id.length > 0
      ? record.id
      : `persisted-message-${index}`;

  return {
    ...record,
    id,
    role,
    content,
    metadata,
    parts: content.length > 0 ? [{ type: 'text', text: content }] : [],
  } as DaemonMessage;
}

/**
 * Plain-language outcomes for durable tasks that ended without a normal
 * answer, keyed by the server's terminal code. Unknown or free-text legacy
 * reasons are left alone.
 */
const TASK_TERMINAL_NOTICES: Record<string, string> = {
  uncertain_effect:
    'This request was interrupted after an action that may already have happened. Check before retrying.',
  interrupted: 'This request was interrupted and could not be completed.',
  account_suspended: 'This request stopped because the account is suspended.',
  internal_error: 'This request could not be completed.',
  budget_exceeded:
    'This request stopped because the compute budget for this period is used up.',
  trial_exhausted:
    'This request stopped because the trial allowance is used up.',
  cancelled: 'Stopped.',
};

function withTaskNotice(
  role: string,
  content: string,
  metadata: Record<string, unknown>,
): string {
  if (role !== 'assistant') return content;
  const code = metadata.terminal_reason;
  if (typeof code !== 'string') return content;
  const notice = TASK_TERMINAL_NOTICES[code];
  if (!notice) return content;
  if (!content) return notice;
  // An uncertain effect must stay visible even when partial text exists.
  return code === 'uncertain_effect' ? `${content}\n\n${notice}` : content;
}

export function getDaemonMessageText(message: DaemonMessage): string {
  if (typeof message.content === 'string' && message.content.length > 0) {
    return message.content;
  }

  // A durable task that regenerated after an interruption marks the switch
  // with a task_reset event; only the text written after it is current.
  let lastReset = -1;
  message.parts.forEach((part, index) => {
    if (part.type === 'data-event' && part.data.type === 'task_reset') {
      lastReset = index;
    }
  });
  return message.parts
    .slice(lastReset + 1)
    .filter((part) => part.type === 'text')
    .map((part) => part.text)
    .join('');
}

/** The durable task a streamed assistant message belongs to, if any. */
export function getDaemonTaskId(
  message: DaemonMessage | undefined,
): string | null {
  if (!message) return null;
  for (const part of message.parts) {
    if (part.type === 'data-event' && part.data.type === 'task') {
      return part.data.task_id;
    }
  }
  return null;
}

export function getDaemonDataEvents(messages: DaemonMessage[]): ChatEvent[] {
  return messages.flatMap((message) =>
    message.parts.flatMap((part) =>
      part.type === 'data-event' ? [part.data] : [],
    ),
  );
}
