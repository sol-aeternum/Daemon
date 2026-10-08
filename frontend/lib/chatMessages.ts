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
    record.status,
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

const REGENERATED_NOTICE = 'This answer was regenerated after an interruption.';

function withTaskNotice(
  role: string,
  content: string,
  metadata: Record<string, unknown>,
  status?: unknown,
): string {
  if (role !== 'assistant') return content;
  // A regenerated answer says so (§4: interruptions are disclosed).
  const regenerated =
    typeof metadata.regenerated_after_interruption === 'number' &&
    metadata.regenerated_after_interruption > 0;
  const withRegeneration = (text: string) =>
    regenerated ? `${text}\n\n${REGENERATED_NOTICE}` : text;
  // A cancel that won the completion race leaves no reason code, only the
  // cancelled status; it must read as stopped, like any other cancel.
  const code =
    metadata.terminal_reason ??
    (status === 'cancelled' || metadata.terminal_status === 'cancelled'
      ? 'cancelled'
      : undefined);
  // Durable task codes are snake_case; older rows carry free-text reasons,
  // which are left alone.
  if (typeof code !== 'string' || !TASK_CODE.test(code))
    return content ? withRegeneration(content) : content;
  const notice =
    TASK_TERMINAL_NOTICES[code] ??
    `This request could not be completed (${code.replace(/_/g, ' ')}).`;
  if (!content) return notice;
  // Partial text stays, but why it stopped must remain visible on every
  // device, not only where it happened.
  return withRegeneration(
    `${content}\n\n${code === 'cancelled' ? CANCELLED_PARTIAL_NOTICE : notice}`,
  );
}

export function getDaemonMessageText(message: DaemonMessage): string {
  if (typeof message.content === 'string' && message.content.length > 0) {
    return message.content;
  }

  // A durable task that regenerated after an interruption marks the switch
  // with a task_reset event; only the text written after it is current.
  const text = currentParts(message)
    .filter((part) => part.type === 'text')
    .map((part) => part.text)
    .join('');
  // A task cancelled elsewhere (another device) ends this live view: say so,
  // as the persisted copy will.
  if (getDaemonTaskStatus(message) !== 'cancelled') return text;
  // Cancelled before the first token: the persisted copy says "Stopped."
  return text
    ? `${text}\n\n${CANCELLED_PARTIAL_NOTICE}`
    : TASK_TERMINAL_NOTICES.cancelled;
}

/** Parts after the last durable-task reset (the current attempt's). */
function currentParts(message: DaemonMessage): DaemonMessage['parts'] {
  let lastReset = -1;
  message.parts.forEach((part, index) => {
    if (part.type === 'data-event' && part.data.type === 'task_reset') {
      lastReset = index;
    }
  });
  return message.parts.slice(lastReset + 1);
}

/** The latest status a live durable-task stream reported for this message. */
export function getDaemonTaskStatus(
  message: DaemonMessage | undefined,
): string | null {
  if (!message) return null;
  let status: string | null = null;
  for (const part of message.parts) {
    if (
      part.type === 'data-event' &&
      part.data.type === 'task' &&
      typeof part.data.status === 'string'
    ) {
      status = part.data.status;
    }
  }
  return status;
}

/** Durable-task statuses that mean the task's outcome is settled. */
export const TERMINAL_TASK_STATUSES: ReadonlySet<string> = new Set([
  'completed',
  'failed',
  'cancelled',
  'needs_attention',
]);

/** The backend answered this turn without a durable task (request-bound chat). */
/** The backend's refusal of this request before acceptance, if it refused. */
export function getRequestRejection(
  message: DaemonMessage | undefined,
): { status: number; code?: string } | null {
  for (const part of message?.parts ?? []) {
    if (part.type === 'data-event' && part.data.type === 'request_rejected') {
      const data = part.data as { status?: unknown; code?: unknown };
      return {
        status: typeof data.status === 'number' ? data.status : 0,
        code: typeof data.code === 'string' ? data.code : undefined,
      };
    }
  }
  return null;
}

export function isRequestBound(message: DaemonMessage | undefined): boolean {
  return Boolean(
    message?.parts.some(
      (part) =>
        part.type === 'data-event' && part.data.type === 'request_bound',
    ),
  );
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

/**
 * Events that describe the request rather than one attempt's output: they
 * stay valid across a regeneration (the conversation the backend created,
 * the task and its status, the request-bound marker).
 */
const REQUEST_SCOPED_EVENTS = new Set([
  'conversation',
  'task',
  'request_bound',
]);

export function getDaemonDataEvents(messages: DaemonMessage[]): ChatEvent[] {
  // Events from an attempt that a regeneration replaced (tool calls, routing)
  // no longer describe the shown answer; request-scoped ones always count.
  return messages.flatMap((message) => {
    const current = new Set(currentParts(message));
    return message.parts.flatMap((part) =>
      part.type === 'data-event' &&
      (current.has(part) || REQUEST_SCOPED_EVENTS.has(part.data.type))
        ? [part.data]
        : [],
    );
  });
}
