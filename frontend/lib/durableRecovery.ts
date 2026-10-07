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
  const now = deps.currentId();
  if (now === task.conversationId) {
    await deps.showSaved(task.id);
  } else if (startedIn === null && now === null) {
    deps.open(task.conversationId);
  }
}
