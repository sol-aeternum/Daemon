import {
  getDaemonTaskId,
  isRequestBound,
  TERMINAL_TASK_STATUSES,
  type DaemonMessage,
} from './chatMessages';

/** Files of the latest submitted turn and the conversation they were sent to. */
export type LastTurn = {
  conversationId: string | null;
  attachments: unknown[];
} | null;

/**
 * Files to re-send when regenerating the latest turn of ``conversationId``:
 * that turn's own files, never another conversation's.
 */
export function attachmentsForRetry(
  lastTurn: LastTurn,
  conversationId: string | null,
): unknown[] {
  return lastTurn?.conversationId === conversationId
    ? lastTurn.attachments
    : [];
}

/** Delays before each by-key lookup: acceptance can commit after a dropped connection. */
export const RECONCILE_LOOKUP_DELAYS_MS = [0, 500, 1500];

type KeyedTask = { id: string; conversationId: string; status: string };

type ReconcileDeps = {
  /** The open conversation id now (``null`` for an unnamed new chat). */
  currentId: () => string | null;
  /** ``null``: no task for the key (yet); ``undefined``: unknown. */
  taskForKey: (key: string) => Promise<KeyedTask | null | undefined>;
  settle: (key: string) => void;
  /** Remember the submission's task, so its key is settled once it ends. */
  record: (key: string, taskId: string) => void;
  /** The submission belongs to the task's conversation (a new chat's is named). */
  promote: (key: string, conversationId: string) => void;
  open: (conversationId: string) => void;
  /** No task exists for the key (after the retries): it was never accepted. */
  notFound?: (key: string) => void;
  /** Show the server's copy of the open conversation's turn for this task. */
  showSaved: (taskId: string) => Promise<void>;
  lookupDelaysMs?: number[];
};

const sleep = (ms: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Show the server's copy of the turn a submission key created.
 *
 * - The lookup is retried briefly: "no task" may only mean "not committed
 *   yet". The key is kept whatever the result, so a resend still replays.
 * - A terminal task settles the key: its outcome is known, so a later
 *   identical request is a new run rather than a replay. A running task is
 *   recorded on the submission and settled once a conversation shows it ended.
 * - A task in another conversation is opened only when the submission was
 *   an unnamed new chat and the user is still there; the user's own
 *   navigation, or a stale key from another conversation, never moves them.
 */
export async function reconcileSubmission(
  key: string | null,
  deps: ReconcileDeps,
): Promise<void> {
  if (!key) return;
  const startedIn = deps.currentId();
  let task: KeyedTask | null | undefined = null;
  for (const delayMs of deps.lookupDelaysMs ?? RECONCILE_LOOKUP_DELAYS_MS) {
    if (delayMs) await sleep(delayMs);
    task = await deps.taskForKey(key);
    if (task !== null) break; // found, or the lookup itself failed
  }
  if (task === null) deps.notFound?.(key);
  if (!task) return;
  if (TERMINAL_TASK_STATUSES.has(task.status)) {
    deps.settle(key);
  } else {
    deps.promote(key, task.conversationId);
    deps.record(key, task.id);
  }
  const now = deps.currentId();
  if (now === task.conversationId) {
    await deps.showSaved(task.id);
  } else if (startedIn === null && now === null) {
    deps.open(task.conversationId);
  }
}

/**
 * Whether this client should detach from the durable task it is streaming:
 * the user has opened another conversation than the one the stream belongs
 * to. Detaching only aborts the observer; the task keeps running. Request-
 * bound streams (where aborting would cancel) are never detached here.
 */
export function shouldDetachStream(
  isStreaming: boolean,
  latest: DaemonMessage | undefined,
  streamConversationId: string | null,
  openConversationId: string | null,
): boolean {
  return (
    isStreaming &&
    getDaemonTaskId(latest) !== null &&
    !isRequestBound(latest) &&
    streamConversationId !== openConversationId
  );
}
