/**
 * The service worker's runtime caching rules, in match order (the first
 * matching rule handles a request). Kept apart from ``app/sw.ts`` so the
 * order itself is tested.
 */
import type { RouteHandlerCallback, RuntimeCaching } from 'serwist';
import {
  CacheFirst,
  ExpirationPlugin,
  NetworkFirst,
  StaleWhileRevalidate,
} from 'serwist';

import {
  isBackendApiRequest,
  isPrivateArtifactRequest,
  isPrivateSpeechRequest,
  isPrivateTaskRequest,
  isSameOriginApiRequest,
  shouldUseGeneralRuntimeCache,
} from './pwaCaching';
import {
  configuredSnapshotApiBase,
  isWebSnapshotRequest,
} from './webSnapshotPaths';

/**
 * Fetches a private response (an account's artifact, speech, or anything from
 * the backend) straight from the network, never from the browser's HTTP
 * cache. A plain ``NetworkOnly``
 * strategy is not enough: Serwist drops its ``fetchOptions`` for navigations
 * and may answer one with the navigation-preload response, which the browser
 * can serve from its HTTP cache (an entry from before these responses were
 * marked no-store, or another account's). So every request, a navigation
 * included, is refetched here with ``cache: 'no-store'`` and the preload is
 * ignored. Navigations a service worker sees are always to its own origin.
 */
export const fetchPrivateUncached: RouteHandlerCallback = async ({
  request,
}) => {
  if (request.mode === 'navigate') {
    return fetch(
      new Request(request.url, {
        method: request.method,
        headers: request.headers,
        credentials: 'same-origin',
        redirect: 'follow',
        cache: 'no-store',
      }),
    );
  }
  return fetch(request, { cache: 'no-store' });
};

export function createRuntimeCaching(
  appOrigin: string,
  configured = configuredSnapshotApiBase(),
): RuntimeCaching[] {
  return [
    {
      // Generated artifacts belong to one account and are checked on every
      // read, on any origin (ahead of the image cache below). First, so no
      // overlapping rule (``/generated-audio/`` is also speech) can shadow it.
      matcher: ({ url }) => isPrivateArtifactRequest(url, configured),
      // Also bypass the browser's HTTP cache: a stored response for a filename
      // another owner also has must never stand in for the owner check.
      handler: fetchPrivateUncached,
    },
    {
      // Speech is the account's own too: same HTTP-cache bypass.
      matcher: ({ url }) => isPrivateSpeechRequest(url),
      handler: fetchPrivateUncached,
    },
    {
      // Nothing from the backend is ever cached: its responses are
      // authenticated, account-scoped and often plaintext content (#474).
      matcher: ({ url }) => isBackendApiRequest(url, appOrigin, configured),
      handler: fetchPrivateUncached,
    },
    {
      // Task snapshots hold plaintext content and status that must never be
      // served stale or to another account.
      matcher: ({ url }) => isPrivateTaskRequest(url, configured),
      handler: fetchPrivateUncached,
    },
    {
      // HTTP no-store does not constrain Cache API writes. Both direct-backend
      // and relative snapshot GETs must bypass all runtime-cache strategies.
      matcher: ({ url }) => isWebSnapshotRequest(url, appOrigin, configured),
      handler: fetchPrivateUncached,
    },
    {
      // Never cache authenticated responses from same-origin API routes.
      matcher: ({ url, sameOrigin }) => isSameOriginApiRequest(url, sameOrigin),
      handler: fetchPrivateUncached,
    },
    {
      matcher: /\.(?:js|css)$/,
      handler: new CacheFirst({
        cacheName: 'static-resources',
        plugins: [
          new ExpirationPlugin({
            maxEntries: 60,
            maxAgeSeconds: 30 * 24 * 60 * 60,
          }),
        ],
      }),
    },
    {
      matcher: /\.(?:png|jpg|jpeg|svg|gif|webp|ico)$/,
      handler: new StaleWhileRevalidate({
        cacheName: 'images',
        plugins: [
          new ExpirationPlugin({
            maxEntries: 60,
            maxAgeSeconds: 30 * 24 * 60 * 60,
          }),
        ],
      }),
    },
    {
      matcher: /\.(?:woff|woff2|eot|ttf|otf)$/,
      handler: new CacheFirst({
        cacheName: 'fonts',
        plugins: [
          new ExpirationPlugin({
            maxEntries: 20,
            maxAgeSeconds: 365 * 24 * 60 * 60,
          }),
        ],
      }),
    },
    {
      matcher: ({ url, sameOrigin }) =>
        shouldUseGeneralRuntimeCache(url, sameOrigin, appOrigin),
      handler: new NetworkFirst({
        cacheName: 'others',
        plugins: [
          new ExpirationPlugin({
            maxEntries: 32,
            maxAgeSeconds: 24 * 60 * 60,
          }),
        ],
        networkTimeoutSeconds: 10,
      }),
    },
  ];
}
