import { TERMINAL_TASK_STATUSES } from './chatMessages';

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

type ReconcileDeps = {
  /** The open conversation id now (``null`` for an unnamed new chat). */
  currentId: () => string | null;
  taskForKey: (
    key: string,
  ) => Promise<
    { id: string; conversationId: string; status: string } | null | undefined
  >;
  settle: (key: string) => void;
  /** A still-running task: settle its key when the follower sees it end. */
  track: (submission: TrackedSubmission) => void;
  open: (conversationId: string) => void;
  /** Show the server's copy of the open conversation's turn for this task. */
  showSaved: (taskId: string) => Promise<void>;
};

/**
 * Show the server's copy of the turn a submission key created.
 *
 * - A terminal task settles the key: its outcome is known, so a later
 *   identical request is a new run rather than a replay.
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
  const task = await deps.taskForKey(key);
  if (!task) return;
  if (TERMINAL_TASK_STATUSES.has(task.status)) deps.settle(key);
  else deps.track({ key, taskId: task.id });
  const now = deps.currentId();
  if (now === task.conversationId) {
    await deps.showSaved(task.id);
  } else if (startedIn === null && now === null) {
    deps.open(task.conversationId);
  }
}

/** A submission whose task was still running when last seen. */
export type TrackedSubmission = { key: string; taskId: string };

type FollowedConversation = {
  activeTask?: { id: string } | null;
  latestTask?: { id: string; status: string } | null;
};

/**
 * The key to settle once a followed conversation shows the tracked task has
 * ended: no longer active, and the latest task is that one in a terminal
 * state. ``null`` while it runs or when the conversation says nothing of it.
 */
export function settledSubmission(
  tracked: TrackedSubmission | null,
  conversation: FollowedConversation,
): string | null {
  if (!tracked || conversation.activeTask?.id === tracked.taskId) return null;
  const latest = conversation.latestTask;
  return latest?.id === tracked.taskId &&
    TERMINAL_TASK_STATUSES.has(latest.status)
    ? tracked.key
    : null;
}
