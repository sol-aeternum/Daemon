import { afterEach, describe, expect, it, vi } from 'vitest';
import { NetworkOnly } from 'serwist';
import type { RouteHandlerCallback, RuntimeCaching } from 'serwist';

import {
  createRuntimeCaching,
  fetchPrivateUncached,
} from '@/lib/pwaRuntimeCaching';

const app = 'https://daemon.test';

/** The rule the service worker uses for ``href``: the first that matches. */
function selectedRule(href: string): RuntimeCaching | undefined {
  const url = new URL(href);
  return createRuntimeCaching(app).find(({ matcher }) => {
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

  function run(request: Request, preload?: Response) {
    const fetchMock = vi.fn(async () => network);
    vi.stubGlobal('fetch', fetchMock);
    const handler = selectedRule(request.url)?.handler as RouteHandlerCallback;
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
});
