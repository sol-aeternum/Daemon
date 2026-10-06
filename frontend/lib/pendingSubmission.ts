/**
 * Idempotency keys that survive an ambiguous response or a reload.
 *
 * A key is created when a turn is submitted and kept until the outcome is
 * known. If the response is lost (network error, closed tab) and the same
 * text is sent again to the same conversation, the same key is reused, so
 * the backend returns the task it already accepted instead of running it
 * twice. A deliberate new submission after a known outcome gets a new key.
 *
 * Only a non-reversible fingerprint of the text is stored, never the text.
 * Storage failures (private mode, blocked storage) fall back to a fresh key,
 * which is today's behaviour.
 */

const STORAGE_KEY = 'daemon.pendingSubmission.v1';
/** How long an unconfirmed submission may be retried under the same key. */
export const PENDING_SUBMISSION_TTL_MS = 15 * 60 * 1000;

type PendingSubmission = {
  key: string;
  fingerprint: string;
  conversationId: string | null;
  createdAt: number;
};

/** FNV-1a: a stable, non-reversible fingerprint for matching a resend. */
function fingerprint(text: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `${text.length}:${hash.toString(16)}`;
}

function read(): PendingSubmission | null {
  try {
    const raw = globalThis.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as PendingSubmission;
    if (typeof value?.key !== 'string' || typeof value.createdAt !== 'number') {
      return null;
    }
    return value;
  } catch {
    return null;
  }
}

function write(value: PendingSubmission | null): void {
  try {
    if (value) {
      globalThis.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    } else {
      globalThis.localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Storage unavailable: keys are simply not reused across reloads.
  }
}

/** The key for submitting ``text`` to ``conversationId``: reused while unconfirmed. */
export function keyForSubmission(
  text: string,
  conversationId: string | null,
  now: number = Date.now(),
): string {
  const pending = read();
  const print = fingerprint(text);
  if (
    pending &&
    pending.fingerprint === print &&
    pending.conversationId === conversationId &&
    now - pending.createdAt < PENDING_SUBMISSION_TTL_MS
  ) {
    return pending.key;
  }
  const key = crypto.randomUUID();
  write({ key, fingerprint: print, conversationId, createdAt: now });
  return key;
}

/** Forget the pending key once the submission's outcome is known. */
export function settlePendingSubmission(): void {
  write(null);
}
