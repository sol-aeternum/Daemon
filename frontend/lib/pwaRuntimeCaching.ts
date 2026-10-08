/**
 * The service worker's runtime caching rules, in match order (the first
 * matching rule handles a request). Kept apart from ``app/sw.ts`` so the
 * order itself is tested.
 */
import type { RuntimeCaching } from 'serwist';
import {
  CacheFirst,
  ExpirationPlugin,
  NetworkFirst,
  NetworkOnly,
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
import { isWebSnapshotRequest } from './webSnapshotPaths';

export function createRuntimeCaching(appOrigin: string): RuntimeCaching[] {
  return [
    {
      // Generated artifacts belong to one account and are checked on every
      // read, on any origin (ahead of the image cache below). First, so no
      // overlapping rule (``/generated-audio/`` is also speech) can shadow it.
      matcher: ({ url }) => isPrivateArtifactRequest(url),
      // Also bypass the browser's HTTP cache: a stored response for a filename
      // another owner also has must never stand in for the owner check.
      handler: new NetworkOnly({ fetchOptions: { cache: 'no-store' } }),
    },
    {
      // Speech is the account's own too: same HTTP-cache bypass.
      matcher: ({ url }) => isPrivateSpeechRequest(url),
      handler: new NetworkOnly({ fetchOptions: { cache: 'no-store' } }),
    },
    {
      // Nothing from the backend is ever cached: its responses are
      // authenticated, account-scoped and often plaintext content (#474).
      matcher: ({ url }) => isBackendApiRequest(url, appOrigin),
      handler: new NetworkOnly(),
    },
    {
      // Task snapshots hold plaintext content and status that must never be
      // served stale or to another account.
      matcher: ({ url }) => isPrivateTaskRequest(url),
      handler: new NetworkOnly(),
    },
    {
      // HTTP no-store does not constrain Cache API writes. Both direct-backend
      // and relative snapshot GETs must bypass all runtime-cache strategies.
      matcher: ({ url }) => isWebSnapshotRequest(url, appOrigin),
      handler: new NetworkOnly(),
    },
    {
      // Never cache authenticated responses from same-origin API routes.
      matcher: ({ url, sameOrigin }) => isSameOriginApiRequest(url, sameOrigin),
      handler: new NetworkOnly(),
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
