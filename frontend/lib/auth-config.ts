'use client';

export type AuthConfig = {
  mode: 'hosted' | 'self_hosted';
  email: { enabled: boolean };
  google: { enabled: boolean; clientId: string };
};

export type AuthConfigResult =
  | { status: 'resolved'; config: AuthConfig }
  | { status: 'error' };

type Listener = (result: AuthConfigResult) => void;

let _cached: AuthConfig | null = null;
let _inflight: Promise<AuthConfigResult> | null = null;
const _listeners = new Set<Listener>();

const ENDPOINT = '/api/v1/auth/config';
const TIMEOUT_MS = 5_000;

function isAuthConfig(value: unknown): value is AuthConfig {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as Record<string, unknown>;
  if (v.mode !== 'hosted' && v.mode !== 'self_hosted') return false;
  if (typeof v.email !== 'object' || v.email === null) return false;
  if (typeof v.google !== 'object' || v.google === null) return false;
  const e = v.email as Record<string, unknown>;
  const g = v.google as Record<string, unknown>;
  if (typeof e.enabled !== 'boolean') return false;
  if (typeof g.enabled !== 'boolean') return false;
  if (typeof g.clientId !== 'string') return false;
  return true;
}

export function fetchAuthConfig(): Promise<AuthConfigResult> {
  if (_cached) {
    return Promise.resolve({ status: 'resolved', config: _cached });
  }
  if (_inflight) return _inflight;
  _inflight = doFetch().then((result) => {
    _inflight = null;
    if (result.status === 'resolved') {
      _cached = result.config;
      for (const cb of _listeners) cb(result);
    } else {
      for (const cb of _listeners) cb(result);
    }
    return result;
  });
  return _inflight;
}

async function doFetch(): Promise<AuthConfigResult> {
  if (typeof fetch === 'undefined') {
    return { status: 'error' };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(ENDPOINT, {
      method: 'GET',
      signal: controller.signal,
      cache: 'no-store',
    });
    if (!res.ok) return { status: 'error' };
    const body: unknown = await res.json();
    if (!isAuthConfig(body)) return { status: 'error' };
    const config: AuthConfig = {
      mode: body.mode,
      email: { enabled: body.email.enabled },
      google: { enabled: body.google.enabled, clientId: body.google.clientId },
    };
    return { status: 'resolved', config };
  } catch {
    return { status: 'error' };
  } finally {
    clearTimeout(timer);
  }
}

export function getCachedAuthConfig(): AuthConfig | undefined {
  return _cached ?? undefined;
}

export function subscribeAuthConfig(cb: Listener): () => void {
  _listeners.add(cb);
  return () => {
    _listeners.delete(cb);
  };
}

export function _resetAuthConfigForTests(): void {
  _cached = null;
  _inflight = null;
  _listeners.clear();
}
