import {
  configuredSnapshotApiBase,
  isWebSnapshotRequest,
} from './webSnapshotPaths';

export function isSameOriginApiRequest(url: URL, sameOrigin: boolean): boolean {
  return sameOrigin && url.pathname.startsWith('/api/');
}

/** Speech is private even when the protected download uses the backend origin. */
export function isPrivateSpeechRequest(url: URL): boolean {
  return /^\/(?:tts(?:\/|$)|generated-audio\/)/.test(url.pathname);
}

/**
 * Durable task reads carry message content and live status for the signed-in
 * account, also when they go straight to the backend origin.
 */
export function isPrivateTaskRequest(
  url: URL,
  configured = configuredSnapshotApiBase(),
): boolean {
  const taskPath = /^\/tasks(?:\/|$)/;
  if (taskPath.test(url.pathname)) return true;
  // The API base may carry a path prefix (``https://host/daemon``).
  try {
    const base = new URL(configured);
    const prefix = base.pathname.replace(/\/+$/, '');
    return (
      url.origin === base.origin &&
      prefix !== '' &&
      url.pathname.startsWith(`${prefix}/`) &&
      taskPath.test(url.pathname.slice(prefix.length))
    );
  } catch {
    return false;
  }
}

/**
 * Every request to the configured backend is private (#474): authenticated
 * reads such as conversations, memories and tasks must never be stored by
 * the service worker, and routes added later are covered without a list.
 * The backend is the configured API base's origin, limited to its path
 * prefix when it has one. A base on the app's own origin without a prefix
 * cannot be told apart from the app shell, so the path rules apply there.
 */
export function isBackendApiRequest(
  url: URL,
  appOrigin: string,
  configured = configuredSnapshotApiBase(),
): boolean {
  try {
    const base = new URL(configured);
    if (url.origin !== base.origin) return false;
    const prefix = base.pathname.replace(/\/+$/, '');
    if (prefix === '') return base.origin !== appOrigin;
    return url.pathname === prefix || url.pathname.startsWith(`${prefix}/`);
  } catch {
    return false;
  }
}

export function shouldUseGeneralRuntimeCache(
  url: URL,
  sameOrigin: boolean,
  appOrigin = sameOrigin ? url.origin : '',
): boolean {
  return (
    !isSameOriginApiRequest(url, sameOrigin) &&
    !/^\/home-suggestions(?:\/|$)/.test(url.pathname) &&
    !isPrivateSpeechRequest(url) &&
    !isPrivateTaskRequest(url) &&
    !isBackendApiRequest(url, appOrigin) &&
    !isWebSnapshotRequest(url, appOrigin)
  );
}

/**
 * Remove private responses an earlier service worker may have cached in the
 * general runtime cache: task reads and anything from the backend (before
 * they were network-only). Other entries and caches are left alone; nothing
 * is created.
 */
export async function clearCachedPrivateEntries(
  storage: CacheStorage,
  appOrigin: string,
  configured = configuredSnapshotApiBase(),
): Promise<void> {
  if (!(await storage.keys()).includes('others')) return;
  const cache = await storage.open('others');
  const requests = await cache.keys();
  await Promise.all(
    requests
      .filter((request) => {
        const url = new URL(request.url);
        return (
          isPrivateTaskRequest(url, configured) ||
          isBackendApiRequest(url, appOrigin, configured)
        );
      })
      .map((request) => cache.delete(request)),
  );
}
