// Observed production page-link prefetches outside screenshot targets; no APIs.
export function isAdditionalRscPrefetch(
  url: URL,
  method: string,
  headers: Record<string, string>,
  appOrigin: string,
) {
  return (
    url.origin === appOrigin &&
    method === 'GET' &&
    ['/settings/voice', '/settings/devices', '/auth'].includes(url.pathname) &&
    url.searchParams.has('_rsc') &&
    headers.rsc === '1' &&
    headers['next-router-prefetch'] === '1'
  );
}

// Headers acknowledge this specific prefetch, not consumption of its full body.
export function isAcknowledgedRscPrefetch(
  url: URL,
  method: string,
  headers: Record<string, string>,
  appOrigin: string,
  allowedPaths: string[],
  status: number,
  contentType: string,
) {
  return (
    url.origin === appOrigin &&
    method === 'GET' &&
    allowedPaths.includes(url.pathname) &&
    url.pathname !== '/api' &&
    !url.pathname.startsWith('/api/') &&
    url.searchParams.has('_rsc') &&
    headers.rsc === '1' &&
    headers['next-router-prefetch'] === '1' &&
    status === 200 &&
    contentType.split(';')[0].trim() === 'text/x-component'
  );
}

export function isAcknowledgedPrefetchCancellation(
  acknowledged: boolean,
  error: string | null,
) {
  return acknowledged && error === 'net::ERR_ABORTED';
}
