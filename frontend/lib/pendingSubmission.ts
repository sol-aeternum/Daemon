/**
 * Idempotency keys that survive an ambiguous response or a reload.
 *
 * Each submitted turn gets its own storage item, keyed by its idempotency
 * key, kept until that submission's outcome is known. If the response is
 * lost (network error, closed tab, disconnect) and the same request is sent
 * again to the same conversation, its key and original request scope are
 * reused, so the backend returns the task it already accepted instead of
 * running it twice.
 *
 * - One item per submission: tabs never read-modify-write a shared list, so
 *   concurrent submissions cannot overwrite or resurrect each other, and
 *   settling one leaves the others.
 * - A request matches if its text and attachments match. The model and
 *   provider it was accepted with are stored and replayed exactly, so a resend
 *   after a reload (when the picker may have reset to auto) reattaches to the
 *   accepted task instead of starting a second one.
 * - A new chat's item follows the conversation the backend names (its match
 *   scope), while remembering the request's original scope (no conversation)
 *   so a resend replays exactly the accepted request.
 * - Only salted SHA-256 fingerprints are stored, never the request content.
 *   The random per-browser salt is replaced on sign-in/sign-out. Without
 *   WebCrypto (an insecure context) every request gets a fresh key.
 * - Items are cleared on sign-in/sign-out; the backend scopes keys per
 *   account in any case.
 * - Storage failures (private mode, blocked storage) fall back to a fresh key
 *   per request, which was the behaviour before durable tasks.
 */

const ITEM_PREFIX = 'daemon.pendingSubmission.v4:';
const SALT_ITEM = 'daemon.pendingSubmission.salt';
/**
 * How long an unresolved submission stays retryable under its key. The
 * backend keeps keys for the task's lifetime; this only bounds local storage.
 * There is deliberately no count limit: evicting an unresolved key could let
 * a retry run accepted work twice. Entries leave when settled or expired.
 */
export const PENDING_SUBMISSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;

type PendingSubmission = {
  fingerprint: string;
  /** Conversation a resend must come from to match (follows promotion). */
  scope: string | null;
  /** Conversation the original request named (null for a new chat). */
  requestConversationId: string | null;
  createdAt: number;
  /** The model and provider it was accepted with, replayed on a resend. */
  model?: unknown;
  provider?: unknown;
  /** The durable task the backend created for it, once known. */
  taskId?: string;
};

export type SubmissionIdentity = {
  text: string;
  model?: unknown;
  provider?: unknown;
  attachments?: unknown;
};

export type PendingKey = {
  key: string;
  /** The conversation id the original request was sent with. */
  requestConversationId: string | null;
  /** The model and provider to send (the original ones on a resend). */
  model: unknown;
  provider: unknown;
};

function hex(bytes: ArrayBuffer | Uint8Array): string {
  return [...new Uint8Array(bytes)]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}

/** This browser's random salt, created on first use. */
function salt(store: Storage): string {
  let value = store.getItem(SALT_ITEM);
  if (!value || !/^[0-9a-f]{64}$/.test(value)) {
    value = hex(crypto.getRandomValues(new Uint8Array(32)));
    store.setItem(SALT_ITEM, value);
  }
  return value;
}

function attachmentShape(attachments: unknown): unknown[] {
  if (!Array.isArray(attachments)) return [];
  return attachments.map((attachment) => {
    if (typeof attachment !== 'object' || attachment === null) return null;
    const record = attachment as Record<string, unknown>;
    const content = [
      record.text_content,
      record.data_url,
      record.content,
      record.data,
      record.url,
    ].find((value) => typeof value === 'string');
    // The backend fingerprints the whole serialized attachment, including
    // its per-selection id: a reselected file is a different request.
    return {
      id: record.id ?? null,
      kind: record.kind ?? null,
      name: record.name ?? null,
      type: record.mime_type ?? record.type ?? record.mimeType ?? null,
      size: record.size ?? null,
      content: content ?? null,
    };
  });
}

/**
 * Salted SHA-256 of what decides whether a resend is the same request: its
 * text and attachments (the model and provider are replayed, not matched).
 */
export async function submissionFingerprint(
  identity: SubmissionIdentity,
  saltHex: string,
): Promise<string> {
  const canonical = JSON.stringify([
    saltHex,
    identity.text,
    attachmentShape(identity.attachments),
  ]);
  return hex(
    await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical)),
  );
}

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
      typeof value.fingerprint !== 'string' ||
      typeof value.createdAt !== 'number'
    ) {
      return null;
    }
    return value;
  } catch {
    return null;
  }
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
    if (itemKey?.startsWith(ITEM_PREFIX)) itemKeys.push(itemKey);
  }
  for (const itemKey of itemKeys) {
    const entry = readEntry(store, itemKey);
    if (!entry || now - entry.createdAt >= PENDING_SUBMISSION_TTL_MS) {
      store.removeItem(itemKey);
      continue;
    }
    found.push([itemKey.slice(ITEM_PREFIX.length), entry]);
  }
  return found.sort((a, b) => a[1].createdAt - b[1].createdAt);
}

/**
 * The key for this submission: an unresolved matching submission's key,
 * original scope, model and provider, or a new key recorded as pending.
 */
export async function keyForSubmission(
  identity: SubmissionIdentity,
  conversationId: string | null,
  now: number = Date.now(),
): Promise<PendingKey> {
  const fresh: PendingKey = {
    key: crypto.randomUUID(),
    requestConversationId: conversationId,
    model: identity.model,
    provider: identity.provider,
  };
  const store = storage();
  if (!store || !globalThis.crypto?.subtle) return fresh;
  try {
    const fingerprint = await submissionFingerprint(identity, salt(store));
    const live = entries(store, now);
    const match = live.find(
      ([, entry]) =>
        entry.fingerprint === fingerprint && entry.scope === conversationId,
    );
    if (match) {
      const [key, entry] = match;
      return {
        key,
        requestConversationId: entry.requestConversationId,
        model: 'model' in entry ? entry.model : identity.model,
        provider: 'provider' in entry ? entry.provider : identity.provider,
      };
    }
    const entry: PendingSubmission = {
      fingerprint,
      scope: conversationId,
      requestConversationId: conversationId,
      createdAt: now,
      model: identity.model ?? null,
      provider: identity.provider ?? null,
    };
    store.setItem(ITEM_PREFIX + fresh.key, JSON.stringify(entry));
    return fresh;
  } catch {
    return fresh;
  }
}

/** A new chat's submission now matches resends from the conversation the backend named. */
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
    // Storage unavailable: the resend simply gets a new key.
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
 * Settle submissions of ``conversation`` whose task it shows has finished
 * (its latest task, in a terminal state, and not active). Works after a
 * reload or on another visit: the task id is stored with the submission.
 */
export function settleFinishedSubmissions(
  conversation: ConversationTasks,
  isTerminal: (status: string) => boolean,
): void {
  const latest = conversation.latestTask;
  if (
    !latest ||
    !isTerminal(latest.status) ||
    conversation.activeTask?.id === latest.id
  )
    return;
  const store = storage();
  if (!store) return;
  try {
    for (const [key, entry] of entries(store, Date.now())) {
      if (entry.taskId === latest.id && entry.scope === conversation.id) {
        store.removeItem(ITEM_PREFIX + key);
      }
    }
  } catch {
    // Nothing to settle.
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
    storage()?.removeItem(ITEM_PREFIX + key);
  } catch {
    // Nothing to forget.
  }
}

/** Forget every pending submission (sign-in or sign-out). */
export function clearPendingSubmissions(): void {
  const store = storage();
  if (!store) return;
  try {
    for (const [key] of entries(store, Date.now())) {
      store.removeItem(ITEM_PREFIX + key);
    }
    // A new account gets a new salt: fingerprints never correlate across.
    store.removeItem(SALT_ITEM);
  } catch {
    // Nothing to clear.
  }
}
