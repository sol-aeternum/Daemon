'use client';

import { discardAllPersistedDrafts } from './draftPersistence';
import { clearPendingSubmissions } from './pendingSubmission';
import {
  AUTH_TRANSACTION_MS,
  AuthEpochChangedError,
  assertAuthCoordinationAvailable,
  cancelAuthCookieOperations,
  readAuthCoordination,
  runAuthCookieTransaction,
  subscribeAuthCoordination,
  type CookieOperationKind,
  type CookieTransaction,
} from './authCookieCoordinator';
import {
  ensureBrowserCacheCleared,
  getBrowserCacheState,
  invalidateBrowserCache,
} from './browserCache';

export interface AuthTokens {
  accessToken: string;
  expiresAt: number;
}

export interface RefreshResult {
  success: boolean;
  error?: string;
  // Tab-local completion fence for navigation/cleanup after an awaited result.
  generation?: number;
  // Recovery from a local fence/epoch race must not discard a protected deep link.
  preserveProtectedUrl?: boolean;
}

export interface LogoutResult {
  success: boolean;
  error?: string;
  generation?: number;
}

interface BackendAuthDevice {
  id: string;
  display_name?: string | null;
  platform?: string | null;
  created_at: string;
  last_seen_at?: string | null;
  current?: boolean;
  revoked?: boolean;
}

let _accessToken: string | null = null;
let _expiresAt: number = 0;
let _authEpoch: string | null = null;
let _localSignedOut = false;
export const LOCAL_SIGNOUT_KEY = 'daemon:local-signout';
const persistenceWarning =
  'Local sign-out could not be saved. Reloading may restore this session. Retry saving before leaving this page.';
let _requiresIdentityFence = false;
let _fenceRemovalFailed = false;
let _startupRaceProblem = false;
let _identityFenceOwner: {
  result: RefreshResult;
  epoch: string;
  signal?: AbortSignal;
} | null = null;

function _localFenceProblem(): string | null {
  if (_fenceRemovalFailed)
    return 'Sign-in is validated, but local sign-out could not be removed. Retry to finish sign-in.';
  try {
    if (sessionStorage.getItem(LOCAL_SIGNOUT_KEY) !== null)
      return 'This tab is signed out locally. Sign in explicitly to continue.';
  } catch {
    _requiresIdentityFence = true;
    return 'Local sign-out storage cannot be read. Protected content stays unavailable.';
  }
  return _requiresIdentityFence
    ? 'This tab is signed out locally. Sign in explicitly to continue.'
    : null;
}

// Local cleanup is unconditional, including when persistence fails. The caller
// may navigate ONLY after this synchronous write/read-back succeeds.
export function signOutLocally(): LogoutResult {
  _requiresIdentityFence = true;
  _fenceRemovalFailed = false;
  let saved = false;
  try {
    sessionStorage.setItem(LOCAL_SIGNOUT_KEY, '1');
    saved = sessionStorage.getItem(LOCAL_SIGNOUT_KEY) === '1';
  } catch {
    // Storage recovery after a forced reload cannot recreate an unwritten fence.
  }
  // A verified persisted marker itself closes every exposure path. Retain an
  // in-memory fence only for the unwritten case (and unverified removal below).
  _requiresIdentityFence = !saved;
  clearAuthState();
  return {
    success: saved,
    error: saved ? undefined : persistenceWarning,
    generation: _authGeneration,
  };
}

// Called by the still-current public credential owner AFTER cache readiness.
// A refresh, peer notice or manually installed token has no retirement proof.
export function completeIdentityReadiness(
  result: RefreshResult,
  signal: AbortSignal,
): boolean {
  if (!result.success || signal.aborted || !isAuthResultCurrent(result))
    return false;
  if (getBrowserCacheState().status !== 'ready') return false;
  const fenced = _localFenceProblem();
  if (!fenced) return _sharedTokenReady();
  const owner = _identityFenceOwner;
  if (
    !owner ||
    owner.result !== result ||
    owner.signal?.aborted ||
    _authEpoch !== owner.epoch ||
    !_rawTokenValid()
  )
    return false;
  try {
    const record = readAuthCoordination();
    assertAuthCoordinationAvailable();
    if (record?.status !== 'stable' || record.epoch !== owner.epoch)
      return false;
    _requiresIdentityFence = true;
    sessionStorage.removeItem(LOCAL_SIGNOUT_KEY);
    if (sessionStorage.getItem(LOCAL_SIGNOUT_KEY) !== null) {
      _fenceRemovalFailed = true;
      return false;
    }
  } catch {
    _fenceRemovalFailed = true;
    return false;
  }
  _requiresIdentityFence = false;
  _fenceRemovalFailed = false;
  _identityFenceOwner = null;
  _notifyAuthGeneration();
  return true;
}
let _coordinationListenerInstalled = false;
// Nonsecret, tab-local sign-in lifetime. Token rotation is not a new sign-in.
let _authGeneration = 0;
// Local login/logout changes cancel queued refreshes. A remote refreshed notice
// discards drafts but still permits the established cookie-refresh follow-up.
let _authMutation = 0;
const _generationListeners = new Set<() => void>();
let _generationAuthListenerInstalled = false;

export function getAuthGeneration(): number {
  return _authGeneration;
}

export function subscribeAuthGeneration(listener: () => void): () => void {
  _installCoordinationListener();
  _generationListeners.add(listener);
  if (!_generationAuthListenerInstalled) {
    _generationAuthListenerInstalled = true;
    // Observe remote invalidation even when only a draft consumer is mounted.
    listenForAuthEvents(() => {});
  }
  return () => _generationListeners.delete(listener);
}

function _notifyAuthGeneration(): void {
  for (const listener of _generationListeners) listener();
}

export function getAccessToken(): string | null {
  return _sharedTokenReady() ? _accessToken : null;
}

export function setAccessToken(
  token: string,
  expiresAtMs: number,
  expectedEpoch?: string,
): void {
  // External/manual installation cannot create shared readiness outside the
  // writer lock. Production credential helpers use the owned commit below.
  cancelAuthCookieOperations();
  const record = readAuthCoordination();
  if (
    !record ||
    record.status !== 'stable' ||
    record.epoch !== (expectedEpoch ?? _authEpoch ?? record.epoch)
  ) {
    throw new Error('Safe session coordination is unavailable');
  }
  _installIdentityToken(token, expiresAtMs, record.epoch);
}

function _installIdentityToken(
  token: string,
  expiresAtMs: number,
  epoch: string,
): void {
  _startupRaceProblem = false;
  // New sign-in: invalidate the browser-cache barrier synchronously BEFORE
  // any auth notification, so no auth listener can see the prior lifetime's
  // cleared-cache readiness and no stale protected request sneaks out.
  invalidateBrowserCache();
  discardAllPersistedDrafts();
  // Sign-in: another account's unresolved submission keys must not be
  // reused or promoted. Token rotation never comes through here.
  clearPendingSubmissions();
  _authGeneration += 1;
  _authMutation += 1;
  _accessToken = token;
  _expiresAt = expiresAtMs;
  _authEpoch = epoch;
  _localSignedOut = false;
  _installCoordinationListener();
  _notifyAuthGeneration();
}

export function clearLocalAuthState(): void {
  cancelAuthCookieOperations();
  _refreshPromise = null;
  _clearOwnedLocalAuthState();
}

function _clearOwnedLocalAuthState(): void {
  _identityFenceOwner = null;
  // Sign-out/401: discard the barrier readiness so the next initialization
  // or protected consumer re-clears against the new (empty) lifetime.
  invalidateBrowserCache();
  discardAllPersistedDrafts();
  clearPendingSubmissions();
  _authGeneration += 1;
  _authMutation += 1;
  _accessToken = null;
  _expiresAt = 0;
  _localSignedOut = true;
  _notifyAuthGeneration();
}

export function clearAuthState(): void {
  clearLocalAuthState();
  _broadcastAuthEvent('cleared');
}

export function hasValidAccessToken(): boolean {
  return _rawTokenValid() && _sharedTokenReady();
}

function _rawTokenValid(): boolean {
  return !!_accessToken && Date.now() < _expiresAt - 30_000;
}

function _sharedTokenReady(): boolean {
  if (_localFenceProblem() || _startupRaceProblem) return false;
  try {
    assertAuthCoordinationAvailable();
    const record = readAuthCoordination();
    return (
      !!_authEpoch && record?.epoch === _authEpoch && record.status === 'stable'
    );
  } catch {
    return false;
  }
}

function _installCoordinationListener(): void {
  if (_coordinationListenerInstalled) return;
  _coordinationListenerInstalled = true;
  subscribeAuthCoordination(() => {
    if (!_accessToken) {
      return;
    }
    let changed = false;
    try {
      const record = readAuthCoordination();
      changed = record?.epoch !== _authEpoch || record.status === 'signed-out';
    } catch {
      changed = true;
    }
    if (changed) {
      clearLocalAuthState();
    }
    // Same-epoch readiness is not a new sign-in lifetime. The provider observes
    // coordination directly; generation consumers must retain drafts and work.
  });
}

export function isAuthResultCurrent(result: RefreshResult): boolean {
  return (
    result.generation !== undefined && result.generation === _authGeneration
  );
}

export function getSessionCoordinationProblem(): string | null {
  const fence = _localFenceProblem();
  if (fence)
    return _identityFenceOwner &&
      isAuthResultCurrent(_identityFenceOwner.result)
      ? 'Validated sign-in is waiting for local sign-out removal. Retry to finish sign-in.'
      : fence;
  if (_startupRaceProblem)
    return 'Session startup changed. Sign in explicitly, or sign out locally.';
  try {
    assertAuthCoordinationAvailable();
    const record = readAuthCoordination();
    if (record?.status === 'signed-out' || _localSignedOut)
      return 'No active session. Sign in again, or sign out locally.';
    if (record?.status === 'pending') return 'Finishing session coordination.';
    if (record?.status === 'uncertain')
      return 'The previous session operation is unconfirmed. Sign in again, or sign out locally.';
    if (_accessToken && !_sharedTokenReady())
      return 'Authentication changed. Sign in again, or sign out locally.';
    return null;
  } catch (error) {
    return error instanceof Error
      ? error.message
      : 'Safe session coordination is unavailable';
  }
}

export function getAuthHeader(): string | null {
  if (!hasValidAccessToken()) return null;
  // The barrier is synchronous: a sync consumer cannot be handed a Bearer
  // header for the current auth lifetime before that lifetime's cache clear
  // has been acknowledged by the module. invalidateBrowserCache() clears the
  // module's ready state on every auth-lifetime change, so 'ready' can never
  // refer to a prior lifetime, and drift fails closed here (no header) rather
  // than passing stale readiness.
  if (getBrowserCacheState().status !== 'ready') {
    return null;
  }
  return `Bearer ${_accessToken}`;
}

let _refreshPromise: Promise<RefreshResult> | null = null;

export function getRefreshPromise(): Promise<RefreshResult> | null {
  return _refreshPromise;
}

type AuthEventType = 'refresh-needed' | 'cleared' | 'refreshed';

interface AuthEvent {
  type: AuthEventType;
  tabId: string;
  epoch: string | null;
}

let _tabId: string | null = null;

function _getTabId(): string {
  if (_tabId) return _tabId;
  _tabId = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  return _tabId;
}

let _channel: BroadcastChannel | null = null;

export function _getChannel(): BroadcastChannel | null {
  if (typeof window === 'undefined') return null;
  if (_channel) return _channel;
  try {
    _channel = new BroadcastChannel('daemon-auth');
    return _channel;
  } catch {
    return null;
  }
}

function _broadcastAuthEvent(type: AuthEventType): void {
  const channel = _getChannel();
  if (!channel) return;
  const event: AuthEvent = { type, tabId: _getTabId(), epoch: _authEpoch };
  try {
    channel.postMessage(event);
  } catch {
    return;
  }
}

// Each delivered event invalidates once, regardless of how many observers exist.
const _handledAuthEvents = new WeakSet<object>();

export function listenForAuthEvents(
  callback: (event: AuthEventType, tabId: string) => void,
): () => void {
  const channel = _getChannel();
  if (!channel) return () => {};

  const handler = (e: MessageEvent<unknown>) => {
    const raw = e.data;
    if (!raw || typeof raw !== 'object') return;
    const event = raw as Record<string, unknown>;
    if (typeof event.type !== 'string' || typeof event.tabId !== 'string')
      return;
    if (event.tabId === _getTabId()) return;

    const type = event.type as AuthEventType;
    const tabId = event.tabId;
    // Old/unscoped notices cannot clear or resume a newer identity. Shared
    // metadata is checked independently; the notice never grants readiness.
    if (event.epoch !== _authEpoch) return;
    if (!['cleared', 'refreshed', 'refresh-needed'].includes(type)) return;

    if (!_handledAuthEvents.has(e)) {
      _handledAuthEvents.add(e);
      if (type === 'cleared') {
        clearLocalAuthState();
      } else if (type === 'refreshed') {
        // A secret-free broadcast cannot prove which account owns the cookie.
        // Discard drafts conservatively, without clearing the local token.
        _authGeneration += 1;
        // Conservative revalidation: a remote token change is treated like a
        // lifetime change for the browser-cache barrier (the completed
        // barrier is only kept across ordinary same-tab rotations).
        invalidateBrowserCache();
        _notifyAuthGeneration();
      }
    }

    callback(type, tabId);
  };

  channel.addEventListener('message', handler);
  return () => channel.removeEventListener('message', handler);
}

async function _fetchAuthProxy(
  path: string,
  init: RequestInit,
): Promise<Response> {
  const requestHeaders = new Headers(init.headers);
  return fetch(`/api/v1/auth${path}`, {
    ...init,
    headers: requestHeaders,
    credentials: 'include',
  });
}

export async function refreshAccessToken(
  options: { signal?: AbortSignal } = {},
): Promise<RefreshResult> {
  _installCoordinationListener();
  if (options.signal?.aborted)
    return { success: false, error: 'Authentication changed' };
  const fence = _localFenceProblem();
  if (fence || _startupRaceProblem)
    return {
      success: false,
      error: fence ?? getSessionCoordinationProblem()!,
      generation: _authGeneration,
      preserveProtectedUrl: true,
    };
  if (hasValidAccessToken()) {
    return { success: true, generation: _authGeneration };
  }
  if (_localSignedOut)
    return {
      success: false,
      error: 'No active session',
      generation: _authGeneration,
    };
  if (!_refreshPromise) {
    const mutation = _authMutation;
    const startupGeneration = _authGeneration;
    const work = (async (): Promise<RefreshResult> => {
      const deadline = Date.now() + AUTH_TRANSACTION_MS;
      let unbound = false;
      let epochRace = false;
      let capturedEpoch: string | null | undefined;
      try {
        const prior = readAuthCoordination();
        unbound = !_accessToken && !_authEpoch && !prior && !_localSignedOut;
        const epoch = _authEpoch ?? prior?.epoch ?? null;
        capturedEpoch = epoch;
        const current = () =>
          mutation === _authMutation &&
          (!unbound || startupGeneration === _authGeneration) &&
          !_localFenceProblem() &&
          !_localSignedOut;
        const run = (expectedEpoch: string | null) =>
          runAuthCookieTransaction(
            'refresh',
            {
              epoch: expectedEpoch,
              deadline,
              isCurrent: current,
            },
            async (tx) => {
              const result: RefreshResult = await _refreshWithinTransaction(tx);
              tx.finish(
                result.success
                  ? 'stable'
                  : result.error === 'Session expired'
                    ? 'signed-out'
                    : 'uncertain',
                () => {
                  if (result.success) {
                    _broadcastAuthEvent('refreshed');
                  } else if (result.error === 'Session expired') {
                    _clearOwnedLocalAuthState();
                    _broadcastAuthEvent('cleared');
                  }
                  result.generation = _authGeneration;
                },
              );
              return result;
            },
          );
        try {
          return await run(epoch);
        } catch (error) {
          if (!(error instanceof AuthEpochChangedError)) throw error;
          epochRace = true;
          if (
            !unbound ||
            !error.stableEpoch ||
            !current() ||
            _accessToken ||
            _authEpoch ||
            Date.now() >= deadline
          )
            throw error;
          // Exactly one new lock request. Its enqueue and lock-time epoch checks
          // reject a second change; the original finite budget is never renewed.
          return await run(error.stableEpoch);
        }
      } catch (error) {
        if (capturedEpoch !== undefined) {
          try {
            epochRace ||=
              (readAuthCoordination()?.epoch ?? null) !== capturedEpoch;
          } catch {
            // Unreadable metadata supplies no stable epoch or retry authority.
          }
        }
        if ((epochRace || unbound) && mutation === _authMutation) {
          _startupRaceProblem = true;
          _notifyAuthGeneration();
        }
        return {
          success: false,
          error: error instanceof Error ? error.message : 'Refresh failed',
          generation: mutation === _authMutation ? _authGeneration : undefined,
          preserveProtectedUrl: epochRace || unbound,
        };
      }
    })();
    _refreshPromise = work;
    void work.finally(() => {
      if (_refreshPromise === work) _refreshPromise = null;
    });
  }
  // Waiter cancellation detaches ONLY this caller; it cannot abort the owner or
  // clear the same-lifetime single-flight pointer underneath remaining waiters.
  const work = _refreshPromise;
  if (!options.signal) return work;
  const signal = options.signal;
  return new Promise((resolve) => {
    const cancelled = () =>
      resolve({ success: false, error: 'Authentication changed' });
    signal.addEventListener('abort', cancelled, { once: true });
    if (signal.aborted) cancelled();
    void work
      .then(resolve)
      .finally(() => signal.removeEventListener('abort', cancelled));
  });
}

async function _refreshWithinTransaction(
  tx: CookieTransaction,
): Promise<RefreshResult> {
  if (_localFenceProblem()) throw new Error('This tab is signed out locally');
  const response = await tx.request('/api/v1/auth/refresh', { method: 'POST' });
  tx.assertCurrent();
  if (response.ok) {
    const data = await response.json().catch(() => null);
    tx.assertCurrent();
    if (
      !data ||
      typeof data.access_token !== 'string' ||
      !data.access_token ||
      (data.expires_in !== undefined &&
        (typeof data.expires_in !== 'number' ||
          !Number.isFinite(data.expires_in) ||
          data.expires_in <= 0))
    ) {
      return { success: false, error: 'Invalid refresh response' };
    }
    // Intermediate token is private while pending. Finalization controls public
    // exposure; no nested coordinator is needed for refresh+revoke.
    _accessToken = data.access_token;
    _expiresAt = Date.now() + (data.expires_in ?? 1800) * 1000;
    _authEpoch = tx.epoch;
    return { success: true };
  }
  return {
    success: false,
    error:
      response.status === 401
        ? 'Session expired'
        : `Refresh failed: ${response.status}`,
  };
}

export async function refreshIfNeeded(): Promise<string | null> {
  const generation = _authGeneration;
  if (!hasValidAccessToken()) await refreshAccessToken();
  if (generation !== _authGeneration || !hasValidAccessToken()) return null;
  try {
    await ensureBrowserCacheCleared();
  } catch {
    return null;
  }
  return generation === _authGeneration && getAuthHeader()
    ? _accessToken
    : null;
}

export async function ensureAuthHeader(): Promise<string | null> {
  const generation = _authGeneration;
  const header = getAuthHeader();
  if (!header && hasValidAccessToken()) {
    // A valid token exists but the sync read is withheld by the barrier:
    // wait for the current auth lifetime's cache-clear acknowledgement
    // before returning anything, and stay fail-closed on its failure.
    try {
      await ensureBrowserCacheCleared();
    } catch {
      return null;
    }
    return generation === _authGeneration ? getAuthHeader() : null;
  }
  if (!header) {
    const token = await refreshIfNeeded();
    return token && generation === _authGeneration ? getAuthHeader() : null;
  }
  // getAuthHeader() only returns under an acknowledged barrier, so a
  // non-null result is already barrier-consistent for this auth lifetime.
  return header;
}

export async function logoutCurrentSession(
  options: { allowRefresh?: boolean } = {},
): Promise<LogoutResult> {
  _installCoordinationListener();
  const mutation = _authMutation;
  const generation = _authGeneration;
  const deadline = Date.now() + 10_000;
  const controller = new AbortController();
  const needsRefresh = !_rawTokenValid();
  if (needsRefresh && options.allowRefresh === false)
    return { success: false, error: 'No active session', generation };
  const refreshDeadline = deadline - 2_000;
  let refreshTimer: ReturnType<typeof setTimeout> | undefined;
  if (needsRefresh)
    refreshTimer = setTimeout(
      () =>
        controller.abort(
          new Error('Session refresh cutoff reached; revocation unconfirmed'),
        ),
      Math.max(0, refreshDeadline - Date.now()),
    );
  try {
    // Terminal sign-out owns its whole refresh+revoke transaction, never a
    // refresh singleton or nested Web Lock. Supersede this tab's older work.
    cancelAuthCookieOperations();
    _refreshPromise = null;
    if (_localSignedOut)
      return { success: false, error: 'No active session', generation };
    const epoch = _authEpoch ?? readAuthCoordination()?.epoch ?? null;
    return await runAuthCookieTransaction(
      'logout',
      {
        epoch,
        deadline,
        signal: controller.signal,
        isCurrent: () => mutation === _authMutation,
      },
      async (tx) => {
        if (needsRefresh) {
          if (Date.now() >= refreshDeadline)
            throw new Error(
              'Session refresh cutoff reached; revocation unconfirmed',
            );
          const refreshed = await _refreshWithinTransaction(tx);
          tx.assertCurrent();
          if (Date.now() >= refreshDeadline)
            throw new Error(
              'Session refresh cutoff reached; revocation unconfirmed',
            );
          if (!refreshed.success) {
            tx.finish(
              refreshed.error === 'Session expired'
                ? 'signed-out'
                : 'uncertain',
              () => {
                if (refreshed.error === 'Session expired') {
                  _clearOwnedLocalAuthState();
                  _broadcastAuthEvent('cleared');
                }
                refreshed.generation = _authGeneration;
              },
            );
            return refreshed;
          }
          clearTimeout(refreshTimer);
        }
        if (!_rawTokenValid() || _authEpoch !== tx.epoch)
          throw new Error('No active session');
        const response = await tx.request('/api/v1/auth/logout', {
          method: 'POST',
          headers: { Authorization: `Bearer ${_accessToken}` },
        });
        tx.assertCurrent();
        const result: LogoutResult =
          response.status === 204
            ? { success: true }
            : {
                success: false,
                error:
                  response.status === 401
                    ? 'Session expired'
                    : `Logout failed: ${response.status}`,
              };
        tx.finish(
          response.status === 204 || response.status === 401
            ? 'signed-out'
            : 'uncertain',
          () => {
            _clearOwnedLocalAuthState();
            _broadcastAuthEvent('cleared');
            result.generation = _authGeneration;
          },
        );
        return result;
      },
    );
  } catch (err) {
    return {
      success: false,
      error:
        controller.signal.aborted && needsRefresh
          ? 'Session refresh cutoff reached; revocation unconfirmed'
          : err instanceof Error
            ? err.message
            : 'Logout failed',
      generation: mutation === _authMutation ? generation : undefined,
    };
  } finally {
    clearTimeout(refreshTimer);
  }
}

export async function startEmailSignIn(email: string): Promise<{
  success: boolean;
  challengeId?: string;
  expiresAt?: number;
  error?: string;
}> {
  try {
    const response = await _fetchAuthProxy('/email/start', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });

    if (response.status === 202 || response.ok) {
      const data = await response.json();
      return {
        success: true,
        challengeId: data.challenge_id as string,
        expiresAt: data.expires_at as number,
      };
    }

    const errorData = await response.json().catch(() => ({}));
    return {
      success: false,
      error:
        (errorData as { error?: string }).error ||
        `Email sign-in start failed: ${response.status}`,
    };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Email sign-in start failed',
    };
  }
}

export async function startGoogleSignIn(): Promise<{
  success: boolean;
  challengeId?: string;
  nonce?: string;
  expiresAt?: number;
  error?: string;
}> {
  try {
    const response = await _fetchAuthProxy('/google/start', {
      method: 'POST',
      credentials: 'include',
    });

    if (response.status === 202 || response.ok) {
      const data = await response.json();
      return {
        success: true,
        challengeId: data.challenge_id as string,
        nonce: data.nonce as string,
        expiresAt: data.expires_at as number,
      };
    }

    const errorData = await response.json().catch(() => ({}));
    return {
      success: false,
      error:
        (errorData as { error?: string; detail?: string }).error ||
        (errorData as { error?: string; detail?: string }).detail ||
        `Google sign-in start failed: ${response.status}`,
    };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Google sign-in start failed',
    };
  }
}

export async function completeGoogleSignIn(
  challengeId: string,
  nonce: string,
  idToken: string,
  devicePersistence: 'private' | 'temporary',
  inviteToken?: string,
  signal?: AbortSignal,
): Promise<RefreshResult> {
  return _completeIdentity(
    'sign-in',
    '/google/complete',
    {
      challenge_id: challengeId,
      nonce,
      id_token: idToken,
      client_kind: 'web',
      device_persistence: devicePersistence,
      ...(inviteToken?.trim() ? { invite_token: inviteToken.trim() } : {}),
    },
    'Google sign-in',
    { 401: 'Google sign-in failed. Please try again.' },
    signal,
  );
}

export async function completeEmailSignIn(
  challengeId: string,
  code: string,
  devicePersistence: 'private' | 'temporary',
  inviteToken?: string,
  signal?: AbortSignal,
): Promise<RefreshResult> {
  return _completeIdentity(
    'sign-in',
    '/email/complete',
    {
      challenge_id: challengeId,
      code,
      client_kind: 'web',
      device_persistence: devicePersistence,
      ...(inviteToken?.trim() ? { invite_token: inviteToken.trim() } : {}),
    },
    'Email sign-in',
    { 401: 'Invalid or expired code. Please try again.' },
    signal,
  );
}

export async function completeSetup(
  setupToken: string,
  displayName?: string,
  signal?: AbortSignal,
): Promise<RefreshResult> {
  return _completeIdentity(
    'setup',
    '/setup',
    {
      setup_token: setupToken,
      ...(displayName ? { device_name: displayName } : {}),
    },
    'Setup',
    { 401: 'Invalid setup token', 409: 'Setup already completed' },
    signal,
  );
}

async function _completeIdentity(
  kind: CookieOperationKind,
  path: string,
  body: Record<string, unknown>,
  label: string,
  errors: Record<number, string>,
  signal?: AbortSignal,
): Promise<RefreshResult> {
  // Explicit credentials may recover a terminated uncertain attempt, but never
  // detach a live transport or adopt another writer's epoch while queued.
  if (signal?.aborted)
    return { success: false, error: 'Authentication changed' };
  // Fence the new intent and cancel older owned work, but retain matching A
  // privately. The shared pending status and new cache generation close exposure;
  // only validated B success replaces the token/epoch. Never snapshot/reinstall A.
  cancelAuthCookieOperations();
  _refreshPromise = null;
  invalidateBrowserCache();
  discardAllPersistedDrafts();
  clearPendingSubmissions();
  _authGeneration += 1;
  _authMutation += 1;
  _identityFenceOwner = null;
  _installCoordinationListener();
  _notifyAuthGeneration();
  let mutation = _authMutation;
  try {
    const prior = readAuthCoordination();
    // Explicit credentials may recover a lost/different record, but cannot
    // preserve or rebind the stale token. Clear it before creating the new owner.
    if (
      _accessToken &&
      (!prior || prior.epoch !== _authEpoch || prior.status === 'signed-out')
    ) {
      _clearOwnedLocalAuthState();
      mutation = _authMutation;
    }
    const epoch = prior?.epoch ?? null;
    return await runAuthCookieTransaction(
      kind,
      {
        epoch,
        replaceIdentity: true,
        signal,
        isCurrent: () => mutation === _authMutation,
      },
      async (tx) => {
        const response = await tx.request(`/api/v1/auth${path}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await response.json().catch(() => ({}));
        tx.assertCurrent();
        const result: RefreshResult = { success: response.ok };
        if (response.ok) {
          const expiresAt =
            kind === 'sign-in'
              ? data.expires_at * 1000
              : Date.now() + (data.expires_in ?? 1800) * 1000;
          if (
            typeof data.access_token !== 'string' ||
            !data.access_token ||
            !Number.isFinite(expiresAt) ||
            expiresAt <= Date.now() ||
            (kind === 'sign-in' && typeof data.expires_at !== 'number') ||
            (kind !== 'sign-in' &&
              data.expires_in !== undefined &&
              typeof data.expires_in !== 'number')
          ) {
            throw new Error(`Invalid ${label.toLowerCase()} response`);
          }
          tx.finish('stable', (committedEpoch) => {
            _installIdentityToken(data.access_token, expiresAt, committedEpoch);
            result.generation = _authGeneration;
            _identityFenceOwner = { result, epoch: committedEpoch, signal };
            _notifyAuthGeneration();
          });
        } else {
          result.error =
            errors[response.status] ||
            data.error ||
            data.detail ||
            `${label} failed: ${response.status}`;
          const commit = () => {
            result.generation = _authGeneration;
          };
          if (_verifiedIdentityRejection(path, response.status, data)) {
            tx.rejectIdentity(commit);
          } else {
            tx.finish('uncertain', commit);
          }
        }
        return result;
      },
    );
  } catch (error) {
    return {
      success: false,
      error: error instanceof Error ? error.message : `${label} failed`,
      generation: mutation === _authMutation ? _authGeneration : undefined,
    };
  }
}

function _verifiedIdentityRejection(
  path: string,
  status: number,
  data: unknown,
): boolean {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail !== 'string') return false;
  // Audited no-cookie rejection contracts only. In particular refresh 401,
  // generic 4xx/5xx, malformed bodies and unknown details are NOT proof.
  switch (path) {
    case '/email/complete':
      return status === 401 && detail === 'code_invalid_or_expired';
    case '/google/complete':
      return status === 401 && detail === 'google_sign_in_failed';
    case '/setup':
      return (
        (status === 401 && detail === 'Invalid setup token') ||
        (status === 409 && detail === 'setup_already_complete')
      );
    case '/enroll/complete':
      return (
        (status === 401 &&
          ['Invalid enrollment session', 'Invalid enrollment code'].includes(
            detail,
          )) ||
        (status === 410 &&
          [
            'Enrollment session already consumed',
            'Enrollment session expired',
          ].includes(detail))
      );
    default:
      return false;
  }
}

export async function startEnrollment(): Promise<{
  success: boolean;
  pendingId?: string;
  code?: string;
  expiresAt?: number;
  error?: string;
}> {
  try {
    const authHeader = await ensureAuthHeader();
    if (!authHeader) return { success: false, error: 'No active session' };
    const headers = new Headers();
    if (authHeader) {
      headers.set('Authorization', authHeader);
    }

    const response = await _fetchAuthProxy('/enroll/start', {
      method: 'POST',
      credentials: 'include',
      headers,
    });

    if (response.ok) {
      const data = await response.json();
      return {
        success: true,
        pendingId: data.pending_id as string,
        code: data.code as string,
        expiresAt: data.expires_at as number,
      };
    }

    const errorData = await response.json().catch(() => ({}));
    return {
      success: false,
      error:
        (errorData as { error?: string }).error ||
        `Enrollment start failed: ${response.status}`,
    };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Enrollment start failed',
    };
  }
}

export async function completeEnrollment(
  pendingId: string,
  code: string,
  signal?: AbortSignal,
): Promise<RefreshResult> {
  return _completeIdentity(
    'enrollment',
    '/enroll/complete',
    {
      pending_id: pendingId,
      code,
      client_kind: 'web',
    },
    'Enrollment',
    {
      401: 'Invalid enrollment code',
      410: 'Enrollment expired or already used',
    },
    signal,
  );
}

export async function listDevices(): Promise<{
  success: boolean;
  generation?: number;
  devices?: Array<{
    id: string;
    device_name: string;
    client_kind: string;
    created_at: string;
    last_seen_at: string | null;
    current: boolean;
    revoked: boolean;
  }>;
  error?: string;
}> {
  const generation = _authGeneration;
  try {
    const authHeader = await ensureAuthHeader();
    if (!authHeader || generation !== _authGeneration)
      return { success: false, error: 'Authentication changed' };
    const headers = new Headers();
    if (authHeader) {
      headers.set('Authorization', authHeader);
    }

    const response = await _fetchAuthProxy('/devices', {
      method: 'GET',
      credentials: 'include',
      headers,
    });

    if (response.ok) {
      const data = await response.json();
      if (generation !== _authGeneration || !getAuthHeader())
        return { success: false, error: 'Authentication changed' };
      const rawDevices: BackendAuthDevice[] = Array.isArray(data.devices)
        ? (data.devices as BackendAuthDevice[])
        : [];
      return {
        success: true,
        generation,
        devices: rawDevices.map((device: BackendAuthDevice) => ({
          id: String(device.id),
          device_name: String(device.display_name ?? ''),
          client_kind: String(device.platform ?? 'unknown'),
          created_at: String(device.created_at),
          last_seen_at:
            typeof device.last_seen_at === 'string'
              ? device.last_seen_at
              : null,
          current: Boolean(device.current),
          revoked: Boolean(device.revoked),
        })),
      };
    }

    const errorData = await response.json().catch(() => ({}));
    return {
      success: false,
      error:
        (errorData as { error?: string }).error ||
        `Failed to list devices: ${response.status}`,
    };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Failed to list devices',
    };
  }
}

export async function revokeDevice(
  deviceId: string,
  options: { current?: boolean; signal?: AbortSignal } = {},
): Promise<RefreshResult> {
  _installCoordinationListener();
  const mutation = _authMutation;
  try {
    if (_localSignedOut) return { success: false, error: 'No active session' };
    const epoch = _authEpoch ?? readAuthCoordination()?.epoch ?? null;
    return await runAuthCookieTransaction(
      'revoke-device',
      {
        epoch,
        signal: options.signal,
        isCurrent: () => mutation === _authMutation,
      },
      async (tx) => {
        if (!_rawTokenValid()) {
          const refreshed = await _refreshWithinTransaction(tx);
          if (!refreshed.success) {
            tx.finish(
              refreshed.error === 'Session expired'
                ? 'signed-out'
                : 'uncertain',
              () => {
                if (refreshed.error === 'Session expired') {
                  _clearOwnedLocalAuthState();
                  _broadcastAuthEvent('cleared');
                }
                refreshed.generation = _authGeneration;
              },
            );
            return refreshed;
          }
        }
        tx.assertCurrent();
        if (_authEpoch !== tx.epoch) throw new Error('Authentication changed');
        const response = await tx.request(
          `/api/v1/auth/devices/${encodeURIComponent(deviceId)}`,
          {
            method: 'DELETE',
            headers: { Authorization: `Bearer ${_accessToken}` },
          },
        );
        tx.assertCurrent();
        const result: RefreshResult =
          response.status === 204
            ? { success: true }
            : {
                success: false,
                error:
                  response.status === 404
                    ? 'Device not found'
                    : `Failed to revoke device: ${response.status}`,
              };
        const signedOut =
          response.status === 401 ||
          (result.success && options.current === true);
        tx.finish(
          signedOut
            ? 'signed-out'
            : result.success || response.status === 404
              ? 'stable'
              : 'uncertain',
          () => {
            if (signedOut) {
              _clearOwnedLocalAuthState();
              _broadcastAuthEvent('cleared');
            }
            result.generation = _authGeneration;
          },
        );
        return result;
      },
    );
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Failed to revoke device',
      generation: mutation === _authMutation ? _authGeneration : undefined,
    };
  }
}

export async function attemptPageLoadRefresh(options?: {
  redirectOnExpiredSession?: boolean;
}): Promise<boolean> {
  if (hasValidAccessToken()) {
    return true;
  }

  const result = await refreshAccessToken();
  if (!isAuthResultCurrent(result)) return false;
  if (!result.success && result.error === 'Session expired') {
    if (
      (options?.redirectOnExpiredSession ?? true) &&
      typeof window !== 'undefined'
    ) {
      window.location.replace('/auth');
    }
    return false;
  }
  return result.success;
}
