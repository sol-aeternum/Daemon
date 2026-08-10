export function isSameOriginApiRequest(url: URL, sameOrigin: boolean): boolean {
  return sameOrigin && url.pathname.startsWith('/api/');
}

export function shouldUseGeneralRuntimeCache(
  url: URL,
  sameOrigin: boolean,
): boolean {
  return !isSameOriginApiRequest(url, sameOrigin);
}
