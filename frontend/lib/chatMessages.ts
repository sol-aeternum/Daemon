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
  extended_budget_exceeded:
    'This request stopped because the extended agent budget for this period is used up.',
  extended_agents_exceeded:
    'This request stopped because the extended agent runs for this period are used up.',
  trial_extended_agents_exhausted:
    'This request stopped because the trial extended agent allowance is used up.',
  rate_limited:
    'This request was not run: too many requests. Try again shortly.',
  concurrency_exceeded:
    'This request was not run: another request was still running. Try again when it finishes.',
  capacity_unavailable:
    'This request was not run: capacity was unavailable. Try again shortly.',
  account_unavailable:
    'This request could not run: the account is unavailable.',
  capability_unavailable:
    'This request could not run: the capability is unavailable for this account.',
  route_unavailable:
    'This request could not run: no approved model route was available.',
  cancelled: 'Stopped.',
};

const TASK_CODE = /^[a-z][a-z0-9_]{0,63}$/;
const CANCELLED_PARTIAL_NOTICE = 'Stopped before the answer was finished.';

function withTaskNotice(
  role: string,
  content: string,
  metadata: Record<string, unknown>,
): string {
  if (role !== 'assistant') return content;
  const code = metadata.terminal_reason;
  // Durable task codes are snake_case; older rows carry free-text reasons,
  // which are left alone.
  if (typeof code !== 'string' || !TASK_CODE.test(code)) return content;
  const notice =
    TASK_TERMINAL_NOTICES[code] ??
    `This request could not be completed (${code.replace(/_/g, ' ')}).`;
  if (!content) return notice;
  // Partial text stays, but an uncertain effect or a stop must remain
  // visible on every device, not only where Stop was pressed.
  if (code === 'uncertain_effect') return `${content}\n\n${notice}`;
  if (code === 'cancelled') return `${content}\n\n${CANCELLED_PARTIAL_NOTICE}`;
  return content;
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

/**
 * Whether the server's copy of a conversation already holds the latest turn
 * this client sent, so replacing the local view with it loses nothing the
 * user typed (a turn the server never accepted stays visible for resending).
 */
export function serverHasLatestTurn(
  server: DaemonMessage[],
  local: DaemonMessage[],
): boolean {
  const lastUser = (messages: DaemonMessage[]) =>
    [...messages].reverse().find((message) => message.role === 'user');
  const localUser = lastUser(local);
  if (!localUser) return server.length > 0;
  const serverUser = lastUser(server);
  return (
    serverUser !== undefined &&
    getDaemonMessageText(serverUser).trim() ===
      getDaemonMessageText(localUser).trim()
  );
}
