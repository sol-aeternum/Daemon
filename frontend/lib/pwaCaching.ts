import { isWebSnapshotRequest } from './webSnapshotPaths';

export function isSameOriginApiRequest(url: URL, sameOrigin: boolean): boolean {
  return sameOrigin && url.pathname.startsWith('/api/');
}

export function shouldUseGeneralRuntimeCache(
  url: URL,
  sameOrigin: boolean,
  appOrigin = sameOrigin ? url.origin : '',
): boolean {
  return (
    !isSameOriginApiRequest(url, sameOrigin) &&
    !isWebSnapshotRequest(url, appOrigin)
  );
}
