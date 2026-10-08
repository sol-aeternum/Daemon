import type { PrecacheEntry, SerwistGlobalConfig } from 'serwist';
import { Serwist } from 'serwist';

import { clearCachedPrivateEntries } from '../lib/pwaCaching';
import { createRuntimeCaching } from '../lib/pwaRuntimeCaching';
import { clearLegacySnapshotEntries } from '../lib/webSnapshotPaths';

declare global {
  interface WorkerGlobalScope extends SerwistGlobalConfig {
    __SW_MANIFEST: (PrecacheEntry | string)[] | undefined;
  }
}

declare const self: ServiceWorkerGlobalScope;

const runtimeCaching = createRuntimeCaching(self.location.origin);

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
