'use client';

export interface AuthTokens {
  accessToken: string;
  expiresAt: number;
}

export interface RefreshResult {
  success: boolean;
  error?: string;
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

export function getAccessToken(): string | null {
  return _accessToken;
}

export function setAccessToken(token: string, expiresAtMs: number): void {
  _accessToken = token;
  _expiresAt = expiresAtMs;
}

export function clearLocalAuthState(): void {
  _accessToken = null;
  _expiresAt = 0;
}

export function clearAuthState(): void {
  clearLocalAuthState();
  _broadcastAuthEvent('cleared');
}

export function hasValidAccessToken(): boolean {
  if (!_accessToken) return false;
  return Date.now() < _expiresAt - 30_000;
}

export function getAuthHeader(): string | null {
  if (!hasValidAccessToken()) return null;
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
  const event: AuthEvent = { type, tabId: _getTabId() };
  try {
    channel.postMessage(event);
  } catch {
    return;
  }
}

const _LOCK_KEY = 'daemon:refresh-lock';
const _LOCK_LEASE_MS = 15_000;
const _LOCK_SETTLE_MIN_MS = 25;
const _LOCK_SETTLE_JITTER_MS = 25;
const _LOCK_POLL_INTERVAL_MS = 100;
const _LOCK_WAIT_TIMEOUT_MS = 30_000;

interface LockState {
  ownerTabId: string;
  nonce: string;
  expiresAt: number;
}

function _getLockState(): LockState | null {
  if (typeof localStorage === 'undefined') return null;
  try {
    const raw = localStorage.getItem(_LOCK_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as LockState;
    if (
      typeof parsed.ownerTabId !== 'string' ||
      typeof parsed.nonce !== 'string' ||
      typeof parsed.expiresAt !== 'number'
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function _sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function _lockMatches(state: LockState | null, lock: LockState): boolean {
  return state?.ownerTabId === lock.ownerTabId && state.nonce === lock.nonce;
}

async function _tryAcquireLocalStorageLock(): Promise<LockState | null> {
  const myTabId = _getTabId();
  const existing = _getLockState();
  if (existing && existing.expiresAt > Date.now()) {
    return null;
  }

  const nonce = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  const lock: LockState = {
    ownerTabId: myTabId,
    nonce,
    expiresAt: Date.now() + _LOCK_LEASE_MS,
  };

  try {
    localStorage.setItem(_LOCK_KEY, JSON.stringify(lock));
  } catch {
    return null;
  }

  await _sleep(
    _LOCK_SETTLE_MIN_MS + Math.floor(Math.random() * _LOCK_SETTLE_JITTER_MS),
  );

  const state = _getLockState();
  if (!_lockMatches(state, lock)) {
    return null;
  }
  return lock;
}

async function _waitForRefreshCompletion(): Promise<RefreshResult | null> {
  return new Promise((resolve) => {
    const deadline = Date.now() + _LOCK_WAIT_TIMEOUT_MS;
    let timerId: ReturnType<typeof setTimeout> | null = null;
    let done = false;

    const cleanup = () => {
      if (timerId !== null) {
        clearTimeout(timerId);
        timerId = null;
      }
      done = true;
      unsubscribe();
    };

    const resolveOnce = (value: RefreshResult | null) => {
      if (done) return;
      cleanup();
      resolve(value);
    };

    const handler = (type: AuthEventType, _tabId: string) => {
      if (type === 'refreshed') {
        void doRefresh()
          .then((result) => resolveOnce(result))
          .catch(() => resolveOnce(null));
        return;
      }
      if (type === 'cleared') {
        resolveOnce({ success: false, error: 'Session expired' });
        return;
      }
    };

    const poll = () => {
      if (done) return;
      const state = _getLockState();
      if (hasValidAccessToken()) {
        resolveOnce({ success: true });
        return;
      }
      if (!state || Date.now() > state.expiresAt) {
        resolveOnce(null);
        return;
      }
      if (Date.now() >= deadline) {
        resolveOnce(null);
        return;
      }
      timerId = setTimeout(poll, _LOCK_POLL_INTERVAL_MS);
    };

    const unsubscribe = listenForAuthEvents(handler);
    poll();
  });
}

async function _releaseLocalStorageLock(lock: LockState): Promise<void> {
  try {
    const state = _getLockState();
    if (_lockMatches(state, lock)) {
      localStorage.removeItem(_LOCK_KEY);
    }
  } catch {
    return;
  }
}

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

    if (type === 'cleared') {
      clearLocalAuthState();
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

export async function refreshAccessToken(): Promise<RefreshResult> {
  if (hasValidAccessToken()) {
    return { success: true };
  }

  if (typeof navigator !== 'undefined' && navigator.locks) {
    try {
      let result: RefreshResult | null = null;
      await navigator.locks.request('daemon-refresh', async () => {
        if (hasValidAccessToken()) {
          result = { success: true };
          return;
        }
        if (_refreshPromise) {
          result = await _refreshPromise;
          return;
        }
        _refreshPromise = doRefresh();
        try {
          result = await _refreshPromise;
        } finally {
          _refreshPromise = null;
        }
      });
      if (result) return result;
      return { success: hasValidAccessToken() };
    } catch {}
  }

  if (_refreshPromise) {
    return _refreshPromise;
  }

  for (let attempt = 0; attempt < 2; attempt += 1) {
    const acquired = await _tryAcquireLocalStorageLock();
    if (!acquired) {
      const waited = await _waitForRefreshCompletion();
      if (waited) return waited;
      if (hasValidAccessToken()) return { success: true };
      continue;
    }

    try {
      if (hasValidAccessToken()) {
        return { success: true };
      }
      _refreshPromise = doRefresh();
      return await _refreshPromise;
    } finally {
      _refreshPromise = null;
      await _releaseLocalStorageLock(acquired);
    }
  }

  return { success: false, error: 'Refresh coordination timed out' };
}

async function doRefresh(): Promise<RefreshResult> {
  if (hasValidAccessToken()) {
    return { success: true };
  }

  const response = await _fetchAuthProxy('/refresh', {
    method: 'POST',
    credentials: 'include',
  });

  if (response.ok) {
    try {
      const data = await response.json();
      const accessToken = data.access_token as string;
      const expiresIn = (data.expires_in as number) || 1800;
      const expiresAtMs = Date.now() + expiresIn * 1000;
      setAccessToken(accessToken, expiresAtMs);
      _broadcastAuthEvent('refreshed');
      return { success: true };
    } catch {
      return { success: false, error: 'Invalid refresh response' };
    }
  }

  if (response.status === 401) {
    clearAuthState();
    return { success: false, error: 'Session expired' };
  }

  return { success: false, error: `Refresh failed: ${response.status}` };
}

export async function refreshIfNeeded(): Promise<string | null> {
  if (hasValidAccessToken()) {
    return _accessToken;
  }
  const result = await refreshAccessToken();
  return result.success ? _accessToken : null;
}

export async function ensureAuthHeader(): Promise<string | null> {
  const header = getAuthHeader();
  if (header) return header;
  await refreshIfNeeded();
  return getAuthHeader();
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
): Promise<{ success: boolean; error?: string }> {
  try {
    const response = await _fetchAuthProxy('/google/complete', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        challenge_id: challengeId,
        nonce,
        id_token: idToken,
        client_kind: 'web',
        device_persistence: devicePersistence,
      }),
    });

    if (response.ok) {
      const data = await response.json();
      const accessToken = data.access_token as string;
      const expiresAt = data.expires_at as number;
      const expiresAtMs = expiresAt * 1000;
      setAccessToken(accessToken, expiresAtMs);
      return { success: true };
    }

    if (response.status === 401) {
      return {
        success: false,
        error: 'Google sign-in failed. Please try again.',
      };
    }

    const errorData = await response.json().catch(() => ({}));
    return {
      success: false,
      error:
        (errorData as { error?: string; detail?: string }).error ||
        (errorData as { error?: string; detail?: string }).detail ||
        `Google sign-in failed: ${response.status}`,
    };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Google sign-in failed',
    };
  }
}

export async function completeEmailSignIn(
  challengeId: string,
  code: string,
  devicePersistence: 'private' | 'temporary',
): Promise<{ success: boolean; error?: string }> {
  try {
    const response = await _fetchAuthProxy('/email/complete', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        challenge_id: challengeId,
        code,
        client_kind: 'web',
        device_persistence: devicePersistence,
      }),
    });

    if (response.ok) {
      const data = await response.json();
      const accessToken = data.access_token as string;
      const expiresAt = data.expires_at as number;
      const expiresAtMs = expiresAt * 1000;
      setAccessToken(accessToken, expiresAtMs);
      return { success: true };
    }

    if (response.status === 401) {
      return {
        success: false,
        error: 'Invalid or expired code. Please try again.',
      };
    }

    const errorData = await response.json().catch(() => ({}));
    return {
      success: false,
      error:
        (errorData as { error?: string }).error ||
        `Email sign-in failed: ${response.status}`,
    };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Email sign-in failed',
    };
  }
}

export async function completeSetup(
  setupToken: string,
  displayName?: string,
): Promise<{ success: boolean; error?: string }> {
  try {
    const response = await _fetchAuthProxy('/setup', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        setup_token: setupToken,
        ...(displayName ? { device_name: displayName } : {}),
      }),
    });

    if (response.ok) {
      const data = await response.json();
      const accessToken = data.access_token as string;
      const expiresIn = (data.expires_in as number) || 1800;
      const expiresAtMs = Date.now() + expiresIn * 1000;
      setAccessToken(accessToken, expiresAtMs);
      return { success: true };
    }

    if (response.status === 401) {
      return { success: false, error: 'Invalid setup token' };
    }

    if (response.status === 409) {
      return { success: false, error: 'Setup already completed' };
    }

    const errorData = await response.json().catch(() => ({}));
    return {
      success: false,
      error:
        (errorData as { error?: string }).error ||
        `Setup failed: ${response.status}`,
    };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Setup failed',
    };
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
): Promise<{ success: boolean; error?: string }> {
  try {
    const response = await _fetchAuthProxy('/enroll/complete', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pending_id: pendingId,
        code,
        client_kind: 'web',
      }),
    });

    if (response.ok) {
      const data = await response.json();
      const accessToken = data.access_token as string;
      const expiresIn = (data.expires_in as number) || 1800;
      const expiresAtMs = Date.now() + expiresIn * 1000;
      setAccessToken(accessToken, expiresAtMs);
      return { success: true };
    }

    if (response.status === 401) {
      return { success: false, error: 'Invalid enrollment code' };
    }

    if (response.status === 410) {
      return { success: false, error: 'Enrollment expired or already used' };
    }

    const errorData = await response.json().catch(() => ({}));
    return {
      success: false,
      error:
        (errorData as { error?: string }).error ||
        `Enrollment failed: ${response.status}`,
    };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Enrollment failed',
    };
  }
}

export async function listDevices(): Promise<{
  success: boolean;
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
  try {
    const authHeader = await ensureAuthHeader();
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
      const rawDevices: BackendAuthDevice[] = Array.isArray(data.devices)
        ? (data.devices as BackendAuthDevice[])
        : [];
      return {
        success: true,
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

export async function revokeDevice(deviceId: string): Promise<{
  success: boolean;
  error?: string;
}> {
  try {
    const authHeader = await ensureAuthHeader();
    const headers = new Headers();
    if (authHeader) {
      headers.set('Authorization', authHeader);
    }

    const response = await _fetchAuthProxy(`/devices/${deviceId}`, {
      method: 'DELETE',
      credentials: 'include',
      headers,
    });

    if (response.status === 204) {
      return { success: true };
    }

    if (response.status === 404) {
      return { success: false, error: 'Device not found' };
    }

    const errorData = await response.json().catch(() => ({}));
    return {
      success: false,
      error:
        (errorData as { error?: string }).error ||
        `Failed to revoke device: ${response.status}`,
    };
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : 'Failed to revoke device',
    };
  }
}

export async function attemptPageLoadRefresh(): Promise<boolean> {
  if (hasValidAccessToken()) {
    return true;
  }

  const result = await refreshAccessToken();
  if (!result.success && result.error === 'Session expired') {
    if (typeof window !== 'undefined') {
      window.location.href = '/setup';
    }
    return false;
  }
  return result.success;
}
