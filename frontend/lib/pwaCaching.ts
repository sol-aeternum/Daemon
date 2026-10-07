import { isWebSnapshotRequest } from './webSnapshotPaths';

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
export function isPrivateTaskRequest(url: URL): boolean {
  return /^\/tasks(?:\/|$)/.test(url.pathname);
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
