/**
 * Index of submissions whose outcome is not yet known, by idempotency key.
 *
 * The key itself belongs to the submitted draft (``lib/chatDrafts``): a
 * resend of that held draft reuses it, so the backend returns the task it
 * already accepted instead of running it twice. This index records only what
 * is needed to resolve a key with the server later, after a reload or on
 * another visit. It never stores message content or anything derived from
 * it.
 *
 * - One item per submission: tabs never read-modify-write a shared list, so
 *   concurrent submissions cannot overwrite or resurrect each other.
 * - A new chat's item follows the conversation the backend names (its
 *   scope), while remembering the request's original scope (no conversation)
 *   so a resend replays exactly the accepted request.
 * - The model and provider the request was sent with are kept, so a resend
 *   after a reload (when the picker may have reset) replays them exactly.
 * - Items are cleared on sign-in/sign-out (lib/auth); the backend scopes keys
 *   per account in any case. Unresolved items are checked with the server on
 *   load and otherwise expire after ``PENDING_SUBMISSION_TTL_MS``.
 */

const ITEM_PREFIX = 'daemon.pendingSubmission.v5:';
/** Older formats (content fingerprints) are removed on sight. */
const LEGACY_PREFIXES = [
  'daemon.pendingSubmission.v3:',
  'daemon.pendingSubmission.v4:',
];
const LEGACY_SALT = 'daemon.pendingSubmission.salt';
/** How long an unresolved submission is kept without a server answer. */
export const PENDING_SUBMISSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;

export type SubmissionMeta = {
  /** Conversation the submission belongs to (follows promotion). */
  scope: string | null;
  /** Conversation the original request named (null for a new chat). */
  requestConversationId: string | null;
  /** The model and provider it was sent with, replayed on a resend. */
  model: unknown;
  provider: unknown;
};

export type PendingSubmission = SubmissionMeta & {
  createdAt: number;
  /** The durable task the backend created for it, once known. */
  taskId?: string;
};

function storage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

function readEntry(store: Storage, itemKey: string): PendingSubmission | null {
  try {
    const value = JSON.parse(
      store.getItem(itemKey) ?? 'null',
    ) as PendingSubmission | null;
    if (
      !value ||
      typeof value.createdAt !== 'number' ||
      !('scope' in value) ||
      'fingerprint' in value
    ) {
      return null;
    }
    return value;
  } catch {
    return null;
  }
}

/**
 * An older (content-fingerprint) record keeps its key: the fingerprint is
 * dropped and the rest moves to the current format, so an unresolved
 * submission from a cached older client can still be resolved by key.
 *
 * The current record is written (and read back) before the older one is
 * removed: if the write fails, the older record stays for a later attempt
 * and this pass still returns the migrated view. A current record for the
 * same key is newer and is never overwritten.
 */
function migrateLegacy(
  store: Storage,
  itemKey: string,
  found: Array<[string, PendingSubmission]>,
): void {
  const prefix = LEGACY_PREFIXES.find((p) => itemKey.startsWith(p));
  let migrated: PendingSubmission | null = null;
  if (prefix) {
    try {
      const value = JSON.parse(store.getItem(itemKey) ?? 'null') as Record<
        string,
        unknown
      > | null;
      if (value && typeof value.createdAt === 'number') {
        const scope = typeof value.scope === 'string' ? value.scope : null;
        migrated = {
          scope,
          // A new chat's original request named no conversation (null).
          requestConversationId:
            typeof value.requestConversationId === 'string' ||
            value.requestConversationId === null
              ? value.requestConversationId
              : scope,
          model: value.model ?? null,
          provider: value.provider ?? null,
          createdAt: value.createdAt,
          ...(typeof value.taskId === 'string' ? { taskId: value.taskId } : {}),
        };
      }
    } catch {
      migrated = null;
    }
  }
  if (!prefix || !migrated) {
    store.removeItem(itemKey); // unreadable, or the old salt: nothing to keep
    return;
  }
  const key = itemKey.slice(prefix.length);
  if (Date.now() - migrated.createdAt >= PENDING_SUBMISSION_TTL_MS) {
    store.removeItem(itemKey);
    return;
  }
  if (readEntry(store, ITEM_PREFIX + key)) {
    // Already migrated (or recreated by a newer tab): that record is found
    // on its own; only the older copy goes.
    store.removeItem(itemKey);
    return;
  }
  try {
    store.setItem(ITEM_PREFIX + key, JSON.stringify(migrated));
  } catch {
    found.push([key, migrated]); // kept in its older form; retried later
    return;
  }
  if (readEntry(store, ITEM_PREFIX + key)) store.removeItem(itemKey);
  found.push([key, migrated]);
}

/** Live entries, oldest first; expired or unreadable items are removed. */
function entries(
  store: Storage,
  now: number,
): Array<[string, PendingSubmission]> {
  const found: Array<[string, PendingSubmission]> = [];
  const itemKeys: string[] = [];
  for (let index = 0; index < store.length; index += 1) {
    const itemKey = store.key(index);
    if (!itemKey) continue;
    if (
      itemKey.startsWith(ITEM_PREFIX) ||
      itemKey === LEGACY_SALT ||
      LEGACY_PREFIXES.some((prefix) => itemKey.startsWith(prefix))
    ) {
      itemKeys.push(itemKey);
    }
  }
  for (const itemKey of itemKeys) {
    if (!itemKey.startsWith(ITEM_PREFIX)) {
      migrateLegacy(store, itemKey, found);
      continue;
    }
    const entry = readEntry(store, itemKey);
    if (!entry || now - entry.createdAt >= PENDING_SUBMISSION_TTL_MS) {
      store.removeItem(itemKey);
      continue;
    }
    found.push([itemKey.slice(ITEM_PREFIX.length), entry]);
  }
  return found.sort((a, b) => a[1].createdAt - b[1].createdAt);
}

/** Record a submission about to be sent under ``key`` (a resend keeps its record). */
export function registerSubmission(
  key: string,
  meta: SubmissionMeta,
  now: number = Date.now(),
): void {
  const store = storage();
  if (!store) return;
  try {
    if (readEntry(store, ITEM_PREFIX + key)) return;
    const entry: PendingSubmission = {
      ...meta,
      model: meta.model ?? null,
      provider: meta.provider ?? null,
      createdAt: now,
    };
    store.setItem(ITEM_PREFIX + key, JSON.stringify(entry));
  } catch {
    // Storage unavailable: the held draft still carries the key in this tab.
  }
}

/** The recorded submission for ``key``, if it is still unresolved. */
export function pendingSubmission(key: string): PendingSubmission | null {
  const store = storage();
  if (!store) return null;
  try {
    return readEntry(store, ITEM_PREFIX + key);
  } catch {
    return null;
  }
}

/** Unresolved submissions, newest first, for resolving with the server. */
export function unresolvedSubmissions(
  now: number = Date.now(),
): Array<{ key: string; entry: PendingSubmission }> {
  const store = storage();
  if (!store) return [];
  try {
    return entries(store, now)
      .reverse()
      .map(([key, entry]) => ({ key, entry }));
  } catch {
    return [];
  }
}

/** A new chat's submission now belongs to the conversation the backend named. */
export function promotePendingSubmission(
  key: string,
  conversationId: string,
): void {
  const store = storage();
  if (!store) return;
  try {
    const entry = readEntry(store, ITEM_PREFIX + key);
    if (!entry || entry.scope === conversationId) return;
    store.setItem(
      ITEM_PREFIX + key,
      JSON.stringify({ ...entry, scope: conversationId }),
    );
  } catch {
    // Storage unavailable: reconciliation by key still finds the task.
  }
}

/** Remember which task the backend created for a submission. */
export function recordSubmissionTask(key: string, taskId: string): void {
  const store = storage();
  if (!store) return;
  try {
    const entry = readEntry(store, ITEM_PREFIX + key);
    if (!entry || entry.taskId === taskId) return;
    store.setItem(ITEM_PREFIX + key, JSON.stringify({ ...entry, taskId }));
  } catch {
    // Storage unavailable: settlement then relies on the stream's outcome.
  }
}

type ConversationTasks = {
  id: string;
  activeTask?: { id: string } | null;
  latestTask?: { id: string; status: string } | null;
};

/**
 * Keys of ``conversation``'s submissions whose task it shows has finished
 * (its latest task, in a terminal state, and not active).
 */
export function finishedSubmissionKeys(
  conversation: ConversationTasks,
  isTerminal: (status: string) => boolean,
): string[] {
  const latest = conversation.latestTask;
  if (
    !latest ||
    !isTerminal(latest.status) ||
    conversation.activeTask?.id === latest.id
  )
    return [];
  const store = storage();
  if (!store) return [];
  try {
    return entries(store, Date.now()).flatMap(([key, entry]) =>
      entry.taskId === latest.id && entry.scope === conversation.id
        ? [key]
        : [],
    );
  } catch {
    return [];
  }
}

/**
 * Unresolved submissions of ``conversationId`` whose task is known, for
 * checking tasks a newer one has superseded as the conversation's latest.
 */
export function pendingTasksIn(
  conversationId: string,
): Array<{ key: string; taskId: string }> {
  const store = storage();
  if (!store) return [];
  try {
    return entries(store, Date.now()).flatMap(([key, entry]) =>
      entry.scope === conversationId && entry.taskId
        ? [{ key, taskId: entry.taskId }]
        : [],
    );
  } catch {
    return [];
  }
}

/** Forget one submission once its outcome is known; others stay pending. */
export function settlePendingSubmission(key: string): void {
  try {
    const store = storage();
    store?.removeItem(ITEM_PREFIX + key);
    // An older copy kept by a failed migration must not bring it back.
    for (const prefix of LEGACY_PREFIXES) store?.removeItem(prefix + key);
  } catch {
    // Nothing to forget.
  }
}

/** Forget every pending submission (sign-in or sign-out). */
export function clearPendingSubmissions(): void {
  const store = storage();
  if (!store) return;
  try {
    // Every stored form, without migrating first: an older copy kept by a
    // failed migration must not survive into the next account.
    const itemKeys: string[] = [];
    for (let index = 0; index < store.length; index += 1) {
      const itemKey = store.key(index);
      if (
        itemKey &&
        (itemKey.startsWith(ITEM_PREFIX) ||
          itemKey === LEGACY_SALT ||
          LEGACY_PREFIXES.some((prefix) => itemKey.startsWith(prefix)))
      ) {
        itemKeys.push(itemKey);
      }
    }
    for (const itemKey of itemKeys) store.removeItem(itemKey);
  } catch {
    // Nothing to clear.
  }
}
