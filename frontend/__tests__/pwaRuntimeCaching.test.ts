import { afterEach, describe, expect, it, vi } from 'vitest';
import { NetworkOnly } from 'serwist';
import type { RouteHandlerCallback, RuntimeCaching } from 'serwist';

import {
  createRuntimeCaching,
  fetchPrivateUncached,
} from '@/lib/pwaRuntimeCaching';

const app = 'https://daemon.test';

/** The rule the service worker uses for ``href``: the first that matches. */
function selectedRule(
  href: string,
  configured = 'https://backend.fixture',
): RuntimeCaching | undefined {
  const url = new URL(href);
  return createRuntimeCaching(app, configured).find(({ matcher }) => {
    if (typeof matcher === 'function') {
      return Boolean(
        matcher({
          url,
          sameOrigin: url.origin === app,
          request: new Request(url),
          event: {} as ExtendableEvent,
        }),
      );
    }
    if (matcher instanceof RegExp) return matcher.test(url.href);
    return false;
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('runtime caching order (#484 review)', () => {
  it('sends every private artifact and speech request to the uncached fetch', () => {
    for (const origin of [app, 'https://backend.fixture']) {
      for (const path of [
        '/generated-audio/a.mp3',
        '/generated-images/a.png',
        '/generated-files/report.csv',
        '/generated-files/report.js',
        '/%67enerated-audio/b.mp3',
        '/tts/stream/v1',
      ]) {
        expect(selectedRule(`${origin}${path}`)?.handler, path).toBe(
          fetchPrivateUncached,
        );
      }
    }
  });

  it('sends everything under a path-prefixed API base to the uncached fetch', () => {
    // Same-origin and dedicated-origin backends mounted under /daemon.
    for (const base of [`${app}/daemon`, 'https://api.daemon.test/daemon']) {
      for (const path of [
        '/generated-files/report.csv',
        '/%67enerated-images/a.png',
        '/conversations/abc',
        '/tasks/t1',
      ]) {
        expect(selectedRule(`${base}${path}`, base)?.handler, base + path).toBe(
          fetchPrivateUncached,
        );
      }
    }
    // The app shell outside the prefix is not the backend.
    expect(selectedRule(`${app}/chat`, `${app}/daemon`)?.handler).not.toBe(
      fetchPrivateUncached,
    );
  });

  it("still caches the app's own static assets", () => {
    for (const path of ['/icons/icon.png', '/_next/static/app.js']) {
      const handler = selectedRule(`${app}${path}`)?.handler;
      expect(handler).not.toBe(fetchPrivateUncached);
      expect(handler).not.toBeInstanceOf(NetworkOnly);
    }
  });
});

describe('fetching a private response (#484 review)', () => {
  const network = new Response('fresh', { status: 200 });

  function run(request: Request, preload?: Response, configured?: string) {
    const fetchMock = vi.fn(async () => network);
    vi.stubGlobal('fetch', fetchMock);
    const handler = selectedRule(request.url, configured)
      ?.handler as RouteHandlerCallback;
    const event = {
      preloadResponse: Promise.resolve(preload),
    } as unknown as ExtendableEvent;
    return {
      fetchMock,
      result: handler({ request, url: new URL(request.url), event }),
    };
  }

  it('bypasses the HTTP cache for a fetch', async () => {
    const request = new Request(`${app}/generated-audio/a.mp3`);
    const { fetchMock, result } = run(request);
    expect(await result).toBe(network);
    expect(fetchMock).toHaveBeenCalledWith(request, { cache: 'no-store' });
  });

  it('bypasses the HTTP cache and ignores the preload for a navigation', async () => {
    // A navigation's preload can come from the browser's HTTP cache.
    const stale = new Response('cached bytes of another account');
    const navigation = {
      url: `${app}/generated-files/report.csv`,
      mode: 'navigate',
      method: 'GET',
      headers: new Headers({ accept: 'text/html' }),
    } as unknown as Request;
    const { fetchMock, result } = run(navigation, stale);
    expect(await result).toBe(network);
    const [sent] = fetchMock.mock.calls[0] as unknown as [Request];
    expect(sent.url).toBe(navigation.url);
    expect(sent.cache).toBe('no-store');
  });

  it('bypasses the HTTP cache for a navigation under a same-origin API prefix', async () => {
    const stale = new Response('cached bytes of another account');
    const navigation = {
      url: `${app}/daemon/generated-files/report.csv`,
      mode: 'navigate',
      method: 'GET',
      headers: new Headers(),
    } as unknown as Request;
    const { fetchMock, result } = run(navigation, stale, `${app}/daemon`);
    expect(await result).toBe(network);
    const [sent] = fetchMock.mock.calls[0] as unknown as [Request];
    expect(sent.cache).toBe('no-store');
  });
});
