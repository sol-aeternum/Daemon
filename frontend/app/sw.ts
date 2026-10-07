import type {
  PrecacheEntry,
  RuntimeCaching,
  SerwistGlobalConfig,
} from 'serwist';
import {
  CacheFirst,
  ExpirationPlugin,
  NetworkFirst,
  NetworkOnly,
  Serwist,
  StaleWhileRevalidate,
} from 'serwist';

import {
  isSameOriginApiRequest,
  isPrivateSpeechRequest,
  clearCachedPrivateEntries,
  isBackendApiRequest,
  isPrivateTaskRequest,
  shouldUseGeneralRuntimeCache,
} from '../lib/pwaCaching';
import {
  clearLegacySnapshotEntries,
  isWebSnapshotRequest,
} from '../lib/webSnapshotPaths';

declare global {
  interface WorkerGlobalScope extends SerwistGlobalConfig {
    __SW_MANIFEST: (PrecacheEntry | string)[] | undefined;
  }
}

declare const self: ServiceWorkerGlobalScope;

const runtimeCaching: RuntimeCaching[] = [
  {
    matcher: ({ url }) => isPrivateSpeechRequest(url),
    handler: new NetworkOnly(),
  },
  {
    // Nothing from the backend is ever cached: its responses are
    // authenticated, account-scoped and often plaintext content (#474).
    matcher: ({ url }) => isBackendApiRequest(url, self.location.origin),
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
    matcher: ({ url }) => isWebSnapshotRequest(url, self.location.origin),
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
      shouldUseGeneralRuntimeCache(url, sameOrigin, self.location.origin),
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

const legacyApiCacheNames = [
  'api-chat-cache',
  'api-data-cache',
  'api-no-cache',
] as const;

self.addEventListener('activate', (event) => {
  event.waitUntil(
    Promise.all([
      ...legacyApiCacheNames.map((cacheName) => caches.delete(cacheName)),
      clearLegacySnapshotEntries(caches, self.location.origin),
      clearCachedPrivateEntries(caches, self.location.origin),
    ]),
  );
});

const serwist = new Serwist({
  precacheEntries: self.__SW_MANIFEST,
  skipWaiting: true,
  clientsClaim: true,
  navigationPreload: true,
  runtimeCaching,
  disableDevLogs: true,
});

serwist.addEventListeners();
