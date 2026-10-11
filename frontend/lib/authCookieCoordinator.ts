'use client';

// Cookie delivery ordering is distinct from the backend outcome. Cancellation
// settles the caller, but the Web Lock stays held until the owned browser
// fetch/body work actually terminates. Never use a lease takeover or keepalive.
export const AUTH_COORDINATION_KEY = 'daemon:auth-coordination';
export const AUTH_COOKIE_LOCK = 'daemon-auth-cookie';
export const AUTH_TRANSACTION_MS = 10_000;

export type CookieOperationKind =
  | 'refresh'
  | 'sign-in'
  | 'setup'
  | 'enrollment'
  | 'logout'
  | 'revoke-device';
export type CoordinationStatus =
  | 'stable'
  | 'pending'
  | 'signed-out'
  | 'uncertain';

export interface AuthCoordinationRecord {
  version: 1;
  epoch: string;
  status: CoordinationStatus;
  // Retained on uncertain records ONLY if the browser operation's termination
  // is unestablished (including an abandoned pending record). Such a record
  // cannot be recovered merely by acquiring a now-free lock.
  attempt?: string;
  kind?: CookieOperationKind;
}

const kinds = new Set<CookieOperationKind>([
  'refresh',
  'sign-in',
  'setup',
  'enrollment',
  'logout',
  'revoke-device',
]);
const statuses = new Set<CoordinationStatus>([
  'stable',
  'pending',
  'signed-out',
  'uncertain',
]);
const identityKinds = new Set<CookieOperationKind>([
  'sign-in',
  'setup',
  'enrollment',
]);

export class AuthCoordinationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AuthCoordinationError';
  }
}

const unavailable = () =>
  new AuthCoordinationError(
    'Safe session coordination is unavailable. Use a supported browser or sign out locally.',
  );
const changed = () => new AuthCoordinationError('Authentication changed');
const uncertain = () =>
  new AuthCoordinationError(
    'The previous session operation is unconfirmed. Sign in again when safe coordination is available, or sign out locally.',
  );

export function readAuthCoordination(): AuthCoordinationRecord | null {
  let raw: string | null;
  try {
    raw = localStorage.getItem(AUTH_COORDINATION_KEY);
  } catch {
    throw unavailable();
  }
  if (raw === null) return null;
  try {
    const record = JSON.parse(raw) as AuthCoordinationRecord;
    if (
      !record ||
      typeof record !== 'object' ||
      record.version !== 1 ||
      typeof record.epoch !== 'string' ||
      !record.epoch ||
      !statuses.has(record.status) ||
      Object.keys(record).some(
        (key) =>
          !['version', 'epoch', 'status', 'attempt', 'kind'].includes(key),
      ) ||
      (record.attempt === undefined) !== (record.kind === undefined) ||
      (record.attempt !== undefined &&
        (typeof record.attempt !== 'string' ||
          !record.attempt ||
          !kinds.has(record.kind!))) ||
      (record.status === 'pending' && record.attempt === undefined) ||
      ((record.status === 'stable' || record.status === 'signed-out') &&
        record.attempt !== undefined)
    )
      throw unavailable();
    return record;
  } catch {
    throw unavailable();
  }
}

const listeners = new Set<() => void>();
let listening = false;
function notify(): void {
  for (const listener of listeners) listener();
}

function installLifecycleListeners(): void {
  if (!listening && typeof window !== 'undefined') {
    listening = true;
    window.addEventListener('storage', (event) => {
      if (event.key === AUTH_COORDINATION_KEY || event.key === null) notify();
    });
    window.addEventListener('pageshow', notify);
    document.addEventListener('visibilitychange', notify);
    window.addEventListener('pagehide', () => cancelAuthCookieOperations());
  }
}

export function subscribeAuthCoordination(listener: () => void): () => void {
  listeners.add(listener);
  installLifecycleListeners();
  return () => listeners.delete(listener);
}

function write(record: AuthCoordinationRecord, onCommit?: () => void): void {
  try {
    const value = JSON.stringify(record);
    localStorage.setItem(AUTH_COORDINATION_KEY, value);
    if (localStorage.getItem(AUTH_COORDINATION_KEY) !== value)
      throw unavailable();
  } catch {
    throw unavailable();
  }
  onCommit?.();
  notify();
}

interface OwnedOperation {
  controller: AbortController;
  deadline: number;
}
const operations = new Set<OwnedOperation>();

export function cancelAuthCookieOperations(): void {
  for (const operation of operations) operation.controller.abort(changed());
}

export interface CookieTransaction {
  readonly epoch: string;
  readonly signal: AbortSignal;
  readonly deadline: number;
  assertCurrent(): void;
  request(path: string, init: RequestInit): Promise<Response>;
  finish(
    status: Exclude<CoordinationStatus, 'pending'>,
    onCommit?: (committedEpoch: string) => void,
  ): void;
  // Identity-only completion after auth.ts verifies the fixed endpoint's
  // exact status/detail rejection. Never grants readiness to an unknown prior
  // session, and cannot be used for refresh/logout/device cookie outcomes.
  rejectIdentity(onCommit?: () => void): void;
}

interface TransactionOptions {
  // Captured at enqueue; a token-bearing operation may never adopt another epoch.
  // Credential sign-in captures the current shared record instead of an old
  // token's epoch; it still cannot silently rebase while queued behind a writer.
  epoch: string | null;
  replaceIdentity?: boolean;
  deadline?: number;
  signal?: AbortSignal;
  isCurrent?: () => boolean;
}

export function assertAuthCoordinationAvailable(): void {
  if (
    typeof navigator === 'undefined' ||
    typeof navigator.locks?.request !== 'function' ||
    typeof crypto === 'undefined' ||
    typeof crypto.randomUUID !== 'function'
  )
    throw unavailable();
}

export async function runAuthCookieTransaction<T>(
  kind: CookieOperationKind,
  options: TransactionOptions,
  action: (transaction: CookieTransaction) => Promise<T>,
): Promise<T> {
  assertAuthCoordinationAvailable();
  if (options.replaceIdentity && !identityKinds.has(kind)) throw changed();
  installLifecycleListeners();
  // Fail before enqueuing if shared storage cannot even be read. Writes are
  // verified under the lock before any network operation is dispatched.
  const enqueuedRecord = readAuthCoordination();
  if ((enqueuedRecord?.epoch ?? null) !== options.epoch) throw changed();
  const controller = new AbortController();
  const deadline = options.deadline ?? Date.now() + AUTH_TRANSACTION_MS;
  const operation = { controller, deadline };
  operations.add(operation);
  const abortExternal = () => controller.abort(changed());
  options.signal?.addEventListener('abort', abortExternal, { once: true });
  if (options.signal?.aborted) abortExternal();
  const check = () => {
    if (Date.now() >= deadline && !controller.signal.aborted) {
      controller.abort(
        new AuthCoordinationError('Session operation timed out'),
      );
    }
    controller.signal.throwIfAborted();
    if (options.isCurrent && !options.isCurrent()) throw changed();
  };
  let rejectCancelled!: (error: unknown) => void;
  const cancelled = new Promise<never>((_, reject) => {
    rejectCancelled = reject;
  });
  const onAbort = () => rejectCancelled(controller.signal.reason);
  controller.signal.addEventListener('abort', onAbort, { once: true });
  if (controller.signal.aborted) onAbort();
  const timer = setTimeout(
    () =>
      controller.abort(
        new AuthCoordinationError('Session operation timed out'),
      ),
    Math.max(0, deadline - Date.now()),
  );

  const work = (async () => {
    check();
    return navigator.locks.request(
      AUTH_COOKIE_LOCK,
      { signal: controller.signal },
      async () => {
        check();
        let previous = readAuthCoordination();
        if (previous?.status === 'pending') {
          write({ ...previous, status: 'uncertain' });
          throw uncertain();
        }
        if (previous?.status === 'uncertain' && previous.attempt)
          throw uncertain();
        if ((previous?.epoch ?? null) !== options.epoch) throw changed();
        if (
          !options.replaceIdentity &&
          previous &&
          previous.status !== 'stable'
        )
          throw uncertain();
        // Keep the actual absent-vs-existing prior identity state distinct from
        // refresh's synthetic bootstrap. A rejected credential is not proof
        // that an initially missing cookie session is safe to restore.
        const priorIdentityRecord = previous;
        const epoch = previous?.epoch ?? crypto.randomUUID();
        // Missing storage is only bootstrappable when the caller had no bound epoch.
        if (!previous && !options.replaceIdentity) {
          previous = { version: 1, epoch, status: 'stable' };
          write(previous);
        }
        const attempt = crypto.randomUUID();
        const pending: AuthCoordinationRecord = {
          version: 1,
          epoch,
          status: 'pending',
          attempt,
          kind,
        };
        let dispatched = false;
        const completion: { status: Exclude<CoordinationStatus, 'pending'> } = {
          status: 'uncertain',
        };
        let verifiedRejection = false;
        let onCommit: ((committedEpoch: string) => void) | undefined;
        const requests: Array<Promise<Response>> = [];
        const owns = () => {
          const record = readAuthCoordination();
          return record?.epoch === epoch && record.attempt === attempt;
        };
        const assertCurrent = () => {
          check();
          if (!owns()) throw changed();
        };
        write(pending);
        const markUnterminated = () => {
          try {
            if (owns()) write({ ...pending, status: 'uncertain' });
          } catch {
            /* Storage failure must not release the live browser operation. */
          }
        };
        controller.signal.addEventListener('abort', markUnterminated, {
          once: true,
        });
        try {
          const result = await action({
            epoch,
            signal: controller.signal,
            deadline,
            assertCurrent,
            request(path, init) {
              assertCurrent();
              dispatched = true;
              const request = (async () => {
                const response = await fetch(path, {
                  ...init,
                  credentials: 'include',
                  signal: controller.signal,
                  keepalive: false,
                });
                // Headers may already have changed the cookie. Keep ownership
                // until the browser body terminates too, even if the action
                // neglects to parse it or later abandons this request promise.
                const bytes = await response.arrayBuffer();
                assertCurrent();
                return new Response(
                  [204, 205, 304].includes(response.status) ? null : bytes,
                  {
                    status: response.status,
                    statusText: response.statusText,
                    headers: response.headers,
                  },
                );
              })();
              requests.push(request);
              void request.catch(() => {});
              return request;
            },
            finish(status, commit) {
              assertCurrent();
              completion.status = status;
              verifiedRejection = false;
              onCommit = commit;
            },
            rejectIdentity(commit) {
              assertCurrent();
              if (!options.replaceIdentity || !identityKinds.has(kind))
                throw changed();
              verifiedRejection = true;
              onCommit = commit;
            },
          });
          await Promise.all(requests);
          assertCurrent();
          if (owns()) {
            // Successful replacement alone changes identity. During pending,
            // same-epoch A remains private; rejection completes that attempt
            // without publishing B and then rolling the epoch back to A.
            const committed: AuthCoordinationRecord = verifiedRejection
              ? (priorIdentityRecord ?? {
                  version: 1,
                  epoch,
                  status: 'signed-out',
                })
              : {
                  version: 1,
                  epoch:
                    completion.status === 'signed-out' ||
                    (options.replaceIdentity && completion.status === 'stable')
                      ? crypto.randomUUID()
                      : epoch,
                  status: completion.status,
                };
            write(committed, () => {
              // Install only after persistence succeeds and before observers
              // see the committed record, still inside the writer lock.
              check();
              onCommit?.(committed.epoch);
            });
          }
          return result;
        } catch (error) {
          // Action failure cannot strand an unawaited browser request outside the
          // lock. Cancel it, then await actual termination (not just the observer).
          if (requests.length) controller.abort(error);
          await Promise.allSettled(requests);
          // This is reached only after action/fetch/body termination. The public
          // cancellation race above never performs this finalization or unlock.
          if (owns()) {
            write(
              dispatched
                ? { version: 1, epoch, status: 'uncertain' }
                : (previous ?? { version: 1, epoch, status: 'signed-out' }),
            );
          }
          throw error;
        } finally {
          controller.signal.removeEventListener('abort', markUnterminated);
        }
      },
    );
  })();
  // Retain cancellation/transport ownership after the public observer returns.
  // In particular an abort-ignoring fetch keeps the Web Lock and pending attempt.
  void work
    .finally(() => {
      clearTimeout(timer);
      controller.signal.removeEventListener('abort', onAbort);
      options.signal?.removeEventListener('abort', abortExternal);
      operations.delete(operation);
    })
    .catch(() => {});
  return Promise.race([work, cancelled]);
}
