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
    !isWebSnapshotRequest(url, appOrigin)
  );
}

/**
 * Remove task responses an earlier service worker may have cached in the
 * general runtime cache (before /tasks was network-only). Other entries and
 * caches are left alone; nothing is created.
 */
export async function clearCachedTaskEntries(
  storage: CacheStorage,
  configured = configuredSnapshotApiBase(),
): Promise<void> {
  if (!(await storage.keys()).includes('others')) return;
  const cache = await storage.open('others');
  const requests = await cache.keys();
  await Promise.all(
    requests
      .filter((request) =>
        isPrivateTaskRequest(new URL(request.url), configured),
      )
      .map((request) => cache.delete(request)),
  );
}
