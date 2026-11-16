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
 * Generated images, files and audio are the account's own artifacts, served
 * only to their owner. They are never cached, neither by the service worker
 * nor by the browser's HTTP cache, whether they come from the backend or
 * through the app's own origin (the ``/generated-files/`` proxy route),
 * where the backend-origin rule cannot see them.
 */
export function isPrivateArtifactRequest(
  url: URL,
  configured = configuredSnapshotApiBase(),
): boolean {
  // The backend decodes the path before routing, so ``/%67enerated-images/``
  // and ``/generated-images%2Fa.png`` still reach the artifact routes: match
  // the decoded path. A path that cannot be decoded is treated as private.
  let path: string;
  try {
    path = decodeURIComponent(url.pathname);
  } catch {
    return true;
  }
  const artifact =
    /^\/+(?:generated-(?:images|files|audio)\/|resources(?:\/|$))/;
  if (artifact.test(path)) return true;
  // Under a path-prefixed API base (``https://host/daemon``) too.
  try {
    const base = new URL(configured);
    const prefix = base.pathname.replace(/\/+$/, '');
    return (
      url.origin === base.origin &&
      prefix !== '' &&
      path.startsWith(`${prefix}/`) &&
      artifact.test(path.slice(prefix.length))
    );
  } catch {
    return false;
  }
}

/**
 * Ordinary private account reads (#B1): the conversation subtree and the two
 * exact account settings/entitlement reads. They are authenticated,
 * account-scoped responses and must never be stored by the service worker.
 *
 * The backend decodes the path before routing, so a single decode is applied
 * here too: ``/%63onversations`` and ``/users/me/settings%2F`` still reach the
 * private routes. A path that cannot be decoded is treated as private. No
 * second decode is applied, so a doubly-encoded path is not broadened into a
 * new private match; it is left to the ordinary lookalike rules below.
 */
const ORDINARY_PRIVATE_ACCOUNT_PATHS = new Set([
  '/users/me/settings',
  '/users/me/entitlements',
]);

function isOrdinaryPrivatePath(path: string): boolean {
  if (/^\/conversations(?:\/|$)/.test(path)) return true;
  // ``/users/me/settings/`` and the exact root path, nothing below it.
  return ORDINARY_PRIVATE_ACCOUNT_PATHS.has(path.replace(/\/+$/, ''));
}

export function isPrivateAccountRequest(
  url: URL,
  appOrigin: string,
  configured = configuredSnapshotApiBase(),
): boolean {
  // Root paths are ours only on the app origin. A foreign site's similarly
  // named response is not an application account entry to erase on upgrade.
  let base: URL | null = null;
  try {
    base = new URL(configured);
  } catch {
    // Relative/root requests can still be classified without an API base.
  }
  if (url.origin !== appOrigin && url.origin !== base?.origin) return false;
  let path: string;
  try {
    path = decodeURIComponent(url.pathname);
  } catch {
    return true;
  }
  if (url.origin === appOrigin && isOrdinaryPrivatePath(path)) return true;
  // Under a path-prefixed API base (``https://host/daemon``) too.
  try {
    if (!base) return false;
    const prefix = base.pathname.replace(/\/+$/, '');
    return (
      url.origin === base.origin &&
      prefix !== '' &&
      path.startsWith(`${prefix}/`) &&
      isOrdinaryPrivatePath(path.slice(prefix.length))
    );
  } catch {
    return false;
  }
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
    !isPrivateAccountRequest(url, appOrigin) &&
    !isPrivateSpeechRequest(url) &&
    !isPrivateArtifactRequest(url) &&
    !isPrivateTaskRequest(url) &&
    !isBackendApiRequest(url, appOrigin) &&
    !isWebSnapshotRequest(url, appOrigin)
  );
}

/**
 * Every runtime cache an earlier worker could have stored private responses
 * in: the extension-based caches matched any origin and path, so a
 * protected ``/generated-files/x.js`` or ``.woff2`` could sit in them too.
 */
const PRIVATE_ENTRY_CACHES = ['others', 'images', 'static-resources', 'fonts'];

/**
 * Remove private responses an earlier service worker may have cached in any
 * of its runtime caches: ordinary private account reads, task reads, generated
 * artifacts and anything from the backend (before they were network-only).
 * Other entries, and the precache, are left alone; nothing is created.
 */
export async function clearCachedPrivateEntries(
  storage: CacheStorage,
  appOrigin: string,
  configured = configuredSnapshotApiBase(),
): Promise<void> {
  const present = await storage.keys();
  await Promise.all(
    PRIVATE_ENTRY_CACHES.filter((name) => present.includes(name)).map(
      async (name) => {
        const cache = await storage.open(name);
        const requests = await cache.keys();
        await Promise.all(
          requests
            .filter((request) => {
              const url = new URL(request.url);
              return (
                isPrivateAccountRequest(url, appOrigin, configured) ||
                isPrivateTaskRequest(url, configured) ||
                isPrivateArtifactRequest(url, configured) ||
                isBackendApiRequest(url, appOrigin, configured)
              );
            })
            .map((request) => cache.delete(request)),
        );
      },
    ),
  );
}
