import { describe, expect, it } from 'vitest';

import {
  isSameOriginApiRequest,
  isPrivateSpeechRequest,
  isPrivateArtifactRequest,
  clearCachedPrivateEntries,
  isBackendApiRequest,
  isPrivateTaskRequest,
  shouldUseGeneralRuntimeCache,
} from '@/lib/pwaCaching';

describe('PWA runtime cache boundaries', () => {
  it('excludes protected speech and capabilities on direct backend origins', () => {
    for (const path of [
      '/tts/capabilities',
      '/tts/stream/v1',
      '/generated-audio/fixture.mp3',
    ]) {
      const url = new URL(`https://backend.fixture${path}`);
      expect(isPrivateSpeechRequest(url)).toBe(true);
      expect(shouldUseGeneralRuntimeCache(url, false)).toBe(false);
    }
    expect(
      isPrivateSpeechRequest(new URL('https://backend.fixture/tts-example')),
    ).toBe(false);
  });
  it('excludes durable task reads on direct backend origins', () => {
    for (const path of [
      '/tasks/by-key/k1',
      '/tasks/0b0c1f9e',
      '/tasks/0b0c1f9e/events',
    ]) {
      const url = new URL(`https://backend.fixture${path}`);
      expect(isPrivateTaskRequest(url)).toBe(true);
      expect(shouldUseGeneralRuntimeCache(url, false)).toBe(false);
    }
    expect(
      isPrivateTaskRequest(new URL('https://backend.fixture/tasksheet.png')),
    ).toBe(false);
  });

  it('excludes task reads under a path-prefixed API base', () => {
    const base = 'https://backend.fixture/daemon';
    for (const path of ['/daemon/tasks/by-key/k1', '/daemon/tasks/0b0c1f9e']) {
      const url = new URL(`https://backend.fixture${path}`);
      expect(isPrivateTaskRequest(url, base)).toBe(true);
    }
    expect(
      isPrivateTaskRequest(
        new URL('https://backend.fixture/daemon/taskbar'),
        base,
      ),
    ).toBe(false);
    expect(
      isPrivateTaskRequest(
        new URL('https://elsewhere.fixture/daemon/tasks/1'),
        base,
      ),
    ).toBe(false);
  });

  it('classifies same-origin /api/* requests as network-only', () => {
    expect(
      isSameOriginApiRequest(new URL('https://daemon.test/api/chat'), true),
    ).toBe(true);
    expect(
      isSameOriginApiRequest(
        new URL('https://daemon.test/api/v1/models'),
        true,
      ),
    ).toBe(true);
  });

  it('does not classify cross-origin or non-matching paths as private APIs', () => {
    expect(
      isSameOriginApiRequest(
        new URL('https://api.example.test/api/chat'),
        false,
      ),
    ).toBe(false);
    expect(
      isSameOriginApiRequest(new URL('https://daemon.test/api'), true),
    ).toBe(false);
    expect(
      isSameOriginApiRequest(new URL('https://daemon.test/apiary'), true),
    ).toBe(false);
  });

  it('keeps same-origin API requests out of the general runtime cache', () => {
    expect(
      shouldUseGeneralRuntimeCache(
        new URL('https://daemon.test/api/conversations'),
        true,
      ),
    ).toBe(false);
    expect(
      shouldUseGeneralRuntimeCache(new URL('https://daemon.test/chat'), true),
    ).toBe(true);
    expect(
      shouldUseGeneralRuntimeCache(
        new URL('https://cdn.example.test/app.js'),
        false,
      ),
    ).toBe(true);
  });
});

describe('service worker upgrade', () => {
  it('purges task responses an earlier worker cached, and nothing else', async () => {
    const entries = new Map<string, true>([
      ['https://backend.fixture/tasks/abc', true],
      ['https://backend.fixture/tasks/by-key/k1', true],
      ['https://daemon.test/icons/icon.png', true],
    ]);
    const cache = {
      keys: async () => [...entries.keys()].map((url) => new Request(url)),
      delete: async (request: Request) => entries.delete(request.url),
    };
    const storage = {
      keys: async () => ['others'],
      open: async () => cache,
    } as unknown as CacheStorage;
    await clearCachedPrivateEntries(
      storage,
      'https://daemon.test',
      'https://other-backend.fixture',
    );
    expect([...entries.keys()]).toEqual(['https://daemon.test/icons/icon.png']);
  });
});

describe('backend origin (#474)', () => {
  const app = 'https://daemon.test';

  it('never caches anything from a dedicated backend origin', () => {
    const base = 'https://api.daemon.test';
    for (const path of [
      '/conversations',
      '/conversations/abc',
      '/memories',
      '/x',
    ]) {
      const url = new URL(`${base}${path}`);
      expect(isBackendApiRequest(url, app, base)).toBe(true);
    }
    expect(
      isBackendApiRequest(new URL('https://cdn.daemon.test/x.png'), app, base),
    ).toBe(false);
  });

  it('limits a path-prefixed base to its prefix', () => {
    const base = 'https://daemon.test/daemon/';
    expect(
      isBackendApiRequest(new URL(`${app}/daemon/conversations/1`), app, base),
    ).toBe(true);
    expect(isBackendApiRequest(new URL(`${app}/daemon`), app, base)).toBe(true);
    expect(isBackendApiRequest(new URL(`${app}/daemonic`), app, base)).toBe(
      false,
    );
    expect(isBackendApiRequest(new URL(`${app}/chat`), app, base)).toBe(false);
  });

  it('never treats the app shell as the backend', () => {
    expect(isBackendApiRequest(new URL(`${app}/chat`), app, app)).toBe(false);
  });

  it('purges backend responses an earlier worker cached, and nothing else', async () => {
    const entries = new Map<string, true>([
      ['https://api.daemon.test/conversations/abc', true],
      ['https://api.daemon.test/memories', true],
      ['https://daemon.test/icons/icon.png', true],
    ]);
    const cache = {
      keys: async () => [...entries.keys()].map((url) => new Request(url)),
      delete: async (request: Request) => entries.delete(request.url),
    };
    const storage = {
      keys: async () => ['others'],
      open: async () => cache,
    } as unknown as CacheStorage;
    await clearCachedPrivateEntries(storage, app, 'https://api.daemon.test');
    expect([...entries.keys()]).toEqual(['https://daemon.test/icons/icon.png']);
  });
});

describe('generated artifacts (#481 review)', () => {
  const app = 'https://daemon.test';

  it('are never cached, through the app origin or the backend', () => {
    for (const origin of [app, 'https://backend.fixture']) {
      for (const path of [
        '/generated-images/a.png',
        '/generated-files/report.csv',
        '/generated-audio/b.mp3',
      ]) {
        const url = new URL(`${origin}${path}`);
        expect(isPrivateArtifactRequest(url)).toBe(true);
        expect(shouldUseGeneralRuntimeCache(url, origin === app, app)).toBe(
          false,
        );
      }
    }
    expect(isPrivateArtifactRequest(new URL(`${app}/icons/icon.png`))).toBe(
      false,
    );
    expect(isPrivateArtifactRequest(new URL(`${app}/generated-imagesx`))).toBe(
      false,
    );
  });

  it('are recognised however their path is encoded (#484 review)', () => {
    for (const path of [
      '/%67enerated-images/a.png',
      '/generated-images%2Fa.png',
      '/generated%2Dfiles/report.csv',
      '//generated-audio/b.mp3',
    ]) {
      const url = new URL(`${app}${path}`);
      expect(isPrivateArtifactRequest(url)).toBe(true);
      expect(shouldUseGeneralRuntimeCache(url, true, app)).toBe(false);
    }
    // Undecodable: treated as private rather than cached.
    expect(isPrivateArtifactRequest(new URL(`${app}/%E0%A4%A.png`))).toBe(true);
    expect(isPrivateArtifactRequest(new URL(`${app}/icons/%69con.png`))).toBe(
      false,
    );
  });

  it('are purged from every runtime cache an earlier worker filled', async () => {
    const caches = new Map<string, Map<string, true>>([
      [
        'others',
        new Map([
          [`${app}/generated-files/report.csv`, true],
          [`${app}/chat`, true],
        ]),
      ],
      [
        'images',
        new Map([
          [`${app}/generated-images/a.png`, true],
          [`${app}/%67enerated-images/b.png`, true],
          [`${app}/icons/icon.png`, true],
        ]),
      ],
      [
        'static-resources',
        new Map([
          [`${app}/generated-files/report.js`, true],
          [`${app}/generated-files/theme.css`, true],
          [`${app}/_next/static/app.js`, true],
        ]),
      ],
      [
        'fonts',
        new Map([
          [`${app}/generated-files/odd.woff2`, true],
          [`${app}/fonts/inter.woff2`, true],
        ]),
      ],
      [
        'serwist-precache-v2',
        new Map([[`${app}/generated-files/never-here.js`, true]]),
      ],
    ]);
    const storage = {
      keys: async () => [...caches.keys()],
      open: async (name: string) => {
        const entries = caches.get(name)!;
        return {
          keys: async () => [...entries.keys()].map((url) => new Request(url)),
          delete: async (request: Request) => entries.delete(request.url),
        };
      },
    } as unknown as CacheStorage;
    await clearCachedPrivateEntries(storage, app, app);
    expect([...caches.get('others')!.keys()]).toEqual([`${app}/chat`]);
    expect([...caches.get('images')!.keys()]).toEqual([
      `${app}/icons/icon.png`,
    ]);
    expect([...caches.get('static-resources')!.keys()]).toEqual([
      `${app}/_next/static/app.js`,
    ]);
    expect([...caches.get('fonts')!.keys()]).toEqual([
      `${app}/fonts/inter.woff2`,
    ]);
    // The precache is not a runtime cache and is left alone.
    expect(caches.get('serwist-precache-v2')!.size).toBe(1);
  });
});
