import type { UIMessage } from 'ai';

import type { ChatEvent } from './events';
import {
  parseTurnFinish,
  readTerminalFinish,
  TURN_FINISH_NOTICE_TEXTS,
  turnFinishNoticeForReason,
  type TurnFinish,
} from './turnFinish';

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
  /** Account-qualified original task returned by saved history, not message metadata. */
  task_id?: string | null;
};

const TASK_UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

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
    task_id:
      typeof record.task_id === 'string' && TASK_UUID.test(record.task_id)
        ? record.task_id
        : null,
    parts: content.length > 0 ? [{ type: 'text', text: content }] : [],
  } as DaemonMessage;
}

/**
 * Plain-language outcomes for durable tasks that ended without a normal
 * answer, keyed by the server's terminal code. Unknown or free-text legacy
 * reasons are left alone.
 */
const TASK_TERMINAL_NOTICES: Record<string, string> = {
  inference_dispatch_unresolved:
    'This response was interrupted. Daemon will not restart it automatically because the provider may still be working or charging. Any saved partial answer is incomplete.',
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
  deadline_exceeded: 'This request stopped because its time limit was reached.',
  cancelled: 'Stopped.',
};

const TASK_CODE = /^[a-z][a-z0-9_]{0,63}$/;
const CANCELLED_PARTIAL_NOTICE = 'Stopped before the answer was finished.';

export const INFERENCE_INTERRUPTED_NOTICE =
  TASK_TERMINAL_NOTICES.inference_dispatch_unresolved;
export const INFERENCE_NEW_REQUEST_WARNING =
  'Starting a new request may overlap with provider work or charges from the interrupted response. This does not resume the earlier request.';
const INFERENCE_STOP_NOTICE =
  'Daemon stopped local work; provider activity or charges may continue.';

/** A saved server disposition, not a provider-stopped or release certificate. */
export function hasUnresolvedInference(message: DaemonMessage): boolean {
  return (
    message.metadata?.terminal_reason === 'inference_dispatch_unresolved' ||
    message.metadata?.inference_dispatch_possible === true
  );
}

const REGENERATED_NOTICE = 'This answer was regenerated after an interruption.';

/**
 * The backend's explicit regeneration count this live stream disclosed on
 * its task/reset frames (#477). A bare reset is not regeneration evidence:
 * same-generation corrections and deferred attempts reset without a
 * positive count, and historical lifecycle metadata must not imply one.
 */
function liveRegenerationCount(message: DaemonMessage): number {
  let count = 0;
  for (const part of message.parts) {
    if (part.type !== 'data-event') continue;
    const data = part.data as {
      type?: string;
      regenerated_after_interruption?: unknown;
    };
    if (
      (data.type === 'task' || data.type === 'task_reset') &&
      typeof data.regenerated_after_interruption === 'number' &&
      Number.isInteger(data.regenerated_after_interruption) &&
      data.regenerated_after_interruption > count
    ) {
      count = data.regenerated_after_interruption;
    }
  }
  return count;
}

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
  // Finish evidence is rendered as its own status line (#759), never baked
  // into saved content. Keep the legacy code-in-content path below only for
  // rows that carry no versioned envelope.
  if (parseTurnFinish(metadata.turn_finish) !== undefined) {
    return content ? withRegeneration(content) : content;
  }
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
  if (code === 'cancelled' && metadata.inference_dispatch_possible === true) {
    return content
      ? `${content}\n\n${INFERENCE_STOP_NOTICE} Any saved partial answer is incomplete.`
      : INFERENCE_STOP_NOTICE;
  }
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
  // Regeneration is disclosed from the backend's explicit count on the
  // live frames (#477), like the persisted copy does from its metadata.
  const liveText =
    liveRegenerationCount(message) > 0 && text
      ? `${text}\n\n${REGENERATED_NOTICE}`
      : text;
  // Versioned finish evidence is rendered outside the reply text (#759), so
  // its prose must not be duplicated here.
  if (getTurnFinish(message) !== undefined) return liveText;
  // A task cancelled elsewhere (another device) ends this live view: say so,
  // as the persisted copy will.
  if (getDaemonTaskStatus(message) !== 'cancelled') return liveText;
  // Cancelled before the first token: the persisted copy says "Stopped."
  return liveText
    ? `${liveText}\n\n${CANCELLED_PARTIAL_NOTICE}`
    : TASK_TERMINAL_NOTICES.cancelled;
}

/** Keep client-added status prose outside a possibly unclosed code fence. */
export function splitDaemonMessageNotice(
  message: DaemonMessage,
  displayText: string,
): { source: string; notice: string } {
  if (message.content) {
    // Saved messages are normalized with this same presentation formatter.
    const suffix = withTaskNotice(
      message.role,
      'x',
      message.metadata ?? {},
      message.status,
    ).slice(1);
    if (suffix && displayText.endsWith(suffix)) {
      return {
        source: displayText.slice(0, -suffix.length),
        notice: suffix.trim(),
      };
    }
  } else {
    const source = currentParts(message)
      .filter((part) => part.type === 'text')
      .map((part) => part.text)
      .join('');
    if (source && displayText.startsWith(source)) {
      return { source, notice: displayText.slice(source.length).trim() };
    }
  }
  return { source: displayText, notice: '' };
}

/**
 * Versioned finish evidence for this message (#759).
 *
 * Saved ``metadata.turn_finish`` is authoritative and is what a reopened
 * conversation reads. Only when no envelope was saved does the latest live
 * durable-task frame supply it; a terminal frame without evidence (a legacy
 * snapshot) clears earlier provisional evidence rather than guessing a stop.
 * An envelope present but invalid fails closed: it never falls back.
 */
export function getTurnFinish(
  message: DaemonMessage | undefined,
): TurnFinish | undefined {
  if (!message) return undefined;
  const metadata = message.metadata;
  if (
    metadata &&
    Object.prototype.hasOwnProperty.call(metadata, 'turn_finish')
  ) {
    return readTerminalFinish(
      metadata.turn_finish,
      message.status ?? metadata.terminal_status,
      metadata.terminal_reason,
    );
  }
  let latest: TurnFinish | undefined;
  for (const part of currentParts(message)) {
    if (part.type !== 'data-event' || part.data.type !== 'task') continue;
    const data = part.data;
    if (
      typeof data.status !== 'string' ||
      !TERMINAL_TASK_STATUSES.has(data.status)
    ) {
      // Only a terminal snapshot is authoritative evidence.
      continue;
    }
    if (Object.prototype.hasOwnProperty.call(data, 'turn_finish')) {
      latest = readTerminalFinish(
        data.turn_finish,
        data.status,
        data.terminal_code,
      );
    } else {
      // Legacy terminal snapshot: bookkeeping is reliable, the reason is not.
      latest = undefined;
    }
  }
  return latest;
}

function genericCodeNotice(code: string): string {
  return `This request could not be completed (${code.replace(/_/g, ' ')}).`;
}

/**
 * The status line for a turn that did not complete normally, or
 * ``undefined`` when there is nothing honest to add. It is rendered beside
 * the reply, never inside its prose, copy or audio, and existing local
 * control codes keep their specific wording.
 */
export function getTurnFinishNotice(
  message: DaemonMessage | undefined,
): string | undefined {
  if (!message || message.role !== 'assistant') return undefined;
  const finish = getTurnFinish(message);
  const metadata = message.metadata ?? {};
  if (finish === undefined) {
    // An explicit server code still describes the disposition. Legacy saved
    // rows already include their notice in the old display text, but a
    // contradictory versioned envelope must not hide the valid server code.
    if (
      typeof message.content === 'string' &&
      !parseTurnFinish(metadata.turn_finish)
    )
      return undefined;
    const liveReason = metadata.terminal_reason;
    if (typeof liveReason !== 'string' || !TASK_CODE.test(liveReason))
      return undefined;
    if (liveReason === 'cancelled') {
      return getDaemonMessageText(message).trim()
        ? CANCELLED_PARTIAL_NOTICE
        : TASK_TERMINAL_NOTICES.cancelled;
    }
    return TASK_TERMINAL_NOTICES[liveReason] ?? genericCodeNotice(liveReason);
  }
  if (finish.outcome === 'complete') return undefined;
  const rawReason = metadata.terminal_reason;
  const recorded =
    typeof rawReason === 'string' && TASK_CODE.test(rawReason)
      ? rawReason
      : undefined;
  const finishReason = TASK_CODE.test(finish.reason)
    ? finish.reason
    : undefined;
  const code =
    recorded === 'capacity_unavailable' && finishReason === 'deadline_exceeded'
      ? finishReason
      : (recorded ?? finishReason);
  if (code === 'cancelled') {
    if (metadata.inference_dispatch_possible === true) {
      return `${INFERENCE_STOP_NOTICE} Any saved partial answer is incomplete.`;
    }
    return getDaemonMessageText(message).trim()
      ? CANCELLED_PARTIAL_NOTICE
      : TASK_TERMINAL_NOTICES.cancelled;
  }
  if (code && TASK_TERMINAL_NOTICES[code]) return TASK_TERMINAL_NOTICES[code];
  const specific = turnFinishNoticeForReason(finish.reason);
  if (specific) return specific;
  if (code) return genericCodeNotice(code);
  if (finish.outcome === 'unknown') {
    return finish.provider.state === 'conflicting'
      ? TURN_FINISH_NOTICE_TEXTS.finish_evidence_conflict
      : TURN_FINISH_NOTICE_TEXTS.finish_evidence_missing;
  }
  return genericCodeNotice(finish.reason);
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
  const historical = message.task_id;
  if (
    historical != null &&
    (typeof historical !== 'string' || !TASK_UUID.test(historical))
  )
    return null;
  let task: string | null = historical ?? null;
  for (const part of message.parts) {
    if (part.type === 'data-event' && part.data.type === 'task') {
      if (task !== null && task !== part.data.task_id) return null;
      task = part.data.task_id;
    }
  }
  return task;
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
