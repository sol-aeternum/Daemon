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
  shouldUseGeneralRuntimeCache,
} from '../lib/pwaCaching';

declare global {
  interface WorkerGlobalScope extends SerwistGlobalConfig {
    __SW_MANIFEST: (PrecacheEntry | string)[] | undefined;
  }
}

declare const self: ServiceWorkerGlobalScope;

const runtimeCaching: RuntimeCaching[] = [
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
      shouldUseGeneralRuntimeCache(url, sameOrigin),
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
    Promise.all(
      legacyApiCacheNames.map((cacheName) => caches.delete(cacheName)),
    ),
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
