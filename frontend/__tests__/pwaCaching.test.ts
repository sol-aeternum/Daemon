import { describe, expect, it } from 'vitest';

import {
  isSameOriginApiRequest,
  isPrivateSpeechRequest,
  clearCachedTaskEntries,
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
    await clearCachedTaskEntries(storage, 'https://backend.fixture');
    expect([...entries.keys()]).toEqual(['https://daemon.test/icons/icon.png']);
  });
});
