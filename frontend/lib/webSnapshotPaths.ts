// Shared by the authenticated client and service worker. Source metadata URLs
// are never request destinations for retained snapshot operations.
const uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const uuidPattern = new RegExp(`^${uuid}$`, 'i');
const snapshotPath = new RegExp(
  `^/conversations/${uuid}/web-snapshots(?:/${uuid}/export)?/?$`,
  'i',
);

export function isSnapshotId(value: string): boolean {
  return uuidPattern.test(value);
}

export function configuredSnapshotApiBase(): string {
  return (
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '')
  ).replace(/\/+$/, '');
}

function resolveBase(origin: string, configured: string): URL {
  const base = new URL(configured || origin);
  if (
    !['http:', 'https:'].includes(base.protocol) ||
    base.username ||
    base.password ||
    base.search ||
    base.hash
  )
    throw new Error('Snapshot API configuration unavailable.');
  base.pathname = base.pathname.replace(/\/+$/, '');
  return base;
}

export function webSnapshotApiUrl(
  conversationId: string,
  snapshotId?: string,
  exporting = false,
): string {
  if (
    !isSnapshotId(conversationId) ||
    (snapshotId !== undefined && !isSnapshotId(snapshotId)) ||
    (exporting && !snapshotId)
  )
    throw new Error('Snapshot request unavailable.');
  const base = resolveBase(window.location.origin, configuredSnapshotApiBase());
  base.pathname = `${base.pathname.replace(/\/$/, '')}/conversations/${conversationId}/web-snapshots${snapshotId ? `/${snapshotId}${exporting ? '/export' : ''}` : ''}`;
  return base.href;
}

export function isWebSnapshotRequest(
  url: URL,
  appOrigin: string,
  configured = configuredSnapshotApiBase(),
): boolean {
  // Also protect the supported relative-route form, but never arbitrary origins
  // or lookalike paths. Query parameters don't change snapshot cache policy.
  if (url.origin === appOrigin && snapshotPath.test(url.pathname)) return true;
  try {
    const base = resolveBase(appOrigin, configured);
    const prefix = base.pathname.replace(/\/$/, '');
    return (
      url.origin === base.origin &&
      url.pathname.startsWith(`${prefix}/`) &&
      snapshotPath.test(url.pathname.slice(prefix.length))
    );
  } catch {
    return false;
  }
}

export async function clearLegacySnapshotEntries(
  storage: CacheStorage,
  appOrigin: string,
  configured = configuredSnapshotApiBase(),
): Promise<void> {
  // Don't create a cache or clear unrelated entries/namespaces. Old controllers
  // must be replaced before privacy acceptance; this cannot undo old-worker work.
  if (!(await storage.keys()).includes('others')) return;
  const cache = await storage.open('others');
  const requests = await cache.keys();
  await Promise.all(
    requests
      .filter((request) =>
        isWebSnapshotRequest(new URL(request.url), appOrigin, configured),
      )
      .map((request) => cache.delete(request)),
  );
}
