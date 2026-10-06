/**
 * Idempotency keys that survive an ambiguous response or a reload.
 *
 * Each submitted turn gets an entry keyed by its idempotency key and kept
 * until that submission's outcome is known. If the response is lost (network
 * error, closed tab, disconnect) and the same request is sent again to the
 * same conversation, its key is reused, so the backend returns the task it
 * already accepted instead of running it twice.
 *
 * - Entries are per submission, so another conversation or tab never
 *   overwrites an earlier unresolved one, and settling one leaves the others.
 * - A request matches only if text, model, provider and attachments match:
 *   a changed request is a new submission (the backend would otherwise answer
 *   409 idempotency_conflict).
 * - A new chat's entry is promoted to its conversation id once the backend
 *   names it, so a resend from the promoted conversation still matches.
 * - Only non-reversible fingerprints are stored, never the request content.
 * - Entries are cleared on sign-in/sign-out; the backend scopes keys per
 *   account in any case.
 * - Storage failures (private mode, blocked storage) fall back to a fresh key
 *   per request, which was the behaviour before durable tasks.
 */

const STORAGE_KEY = 'daemon.pendingSubmissions.v2';
/**
 * How long an unresolved submission stays retryable under its key. The
 * backend keeps keys for the task's lifetime; this only bounds local storage.
 */
export const PENDING_SUBMISSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;
export const MAX_PENDING_SUBMISSIONS = 50;

type PendingSubmission = {
  key: string;
  fingerprint: string;
  conversationId: string | null;
  createdAt: number;
};

export type SubmissionIdentity = {
  text: string;
  model?: unknown;
  provider?: unknown;
  attachments?: unknown;
};

/** FNV-1a over a canonical string: stable and non-reversible enough for matching. */
function fnv(text: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `${text.length}:${hash.toString(16)}`;
}

function attachmentShape(attachments: unknown): unknown[] {
  if (!Array.isArray(attachments)) return [];
  return attachments.map((attachment) => {
    if (typeof attachment !== 'object' || attachment === null) return null;
    const record = attachment as Record<string, unknown>;
    const content = [record.content, record.data, record.url].find(
      (value) => typeof value === 'string',
    ) as string | undefined;
    return {
      name: record.name ?? null,
      type: record.type ?? record.mimeType ?? null,
      content: content ? fnv(content) : null,
    };
  });
}

/** Fingerprint of everything that decides whether a resend is the same request. */
export function submissionFingerprint(identity: SubmissionIdentity): string {
  return fnv(
    JSON.stringify([
      identity.text,
      identity.model ?? 'auto',
      identity.provider ?? null,
      attachmentShape(identity.attachments),
    ]),
  );
}

function storage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

function read(now: number): PendingSubmission[] {
  try {
    const raw = storage()?.getItem(STORAGE_KEY);
    if (!raw) return [];
    const value = JSON.parse(raw) as { entries?: PendingSubmission[] };
    return (value.entries ?? []).filter(
      (entry) =>
        typeof entry?.key === 'string' &&
        typeof entry.fingerprint === 'string' &&
        typeof entry.createdAt === 'number' &&
        now - entry.createdAt < PENDING_SUBMISSION_TTL_MS,
    );
  } catch {
    return [];
  }
}

function write(entries: PendingSubmission[]): void {
  const store = storage();
  if (!store) return;
  try {
    if (entries.length === 0) {
      store.removeItem(STORAGE_KEY);
    } else {
      const kept = entries.slice(-MAX_PENDING_SUBMISSIONS);
      store.setItem(STORAGE_KEY, JSON.stringify({ entries: kept }));
    }
  } catch {
    // Storage unavailable: keys are simply not reused across reloads.
  }
}

/**
 * The idempotency key for this submission: the key of an unresolved matching
 * submission to the same conversation, or a new one recorded as pending.
 */
export function keyForSubmission(
  identity: SubmissionIdentity,
  conversationId: string | null,
  now: number = Date.now(),
): string {
  const fingerprint = submissionFingerprint(identity);
  const entries = read(now);
  const match = entries.find(
    (entry) =>
      entry.fingerprint === fingerprint &&
      entry.conversationId === conversationId,
  );
  if (match) return match.key;
  const key = crypto.randomUUID();
  write([...entries, { key, fingerprint, conversationId, createdAt: now }]);
  return key;
}

/** A new chat's submission now belongs to the conversation the backend named. */
export function promotePendingSubmission(
  key: string,
  conversationId: string,
  now: number = Date.now(),
): void {
  const entries = read(now);
  const entry = entries.find((candidate) => candidate.key === key);
  if (!entry || entry.conversationId === conversationId) return;
  entry.conversationId = conversationId;
  write(entries);
}

/** Forget one submission once its outcome is known; others stay pending. */
export function settlePendingSubmission(
  key: string,
  now: number = Date.now(),
): void {
  const entries = read(now);
  const remaining = entries.filter((entry) => entry.key !== key);
  if (remaining.length !== entries.length) write(remaining);
}

/** Forget every pending submission (sign-in or sign-out). */
export function clearPendingSubmissions(): void {
  write([]);
}
