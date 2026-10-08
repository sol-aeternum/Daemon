import { describe, expect, it } from 'vitest';
import { NetworkOnly } from 'serwist';
import type { RuntimeCaching } from 'serwist';

import { createRuntimeCaching } from '@/lib/pwaRuntimeCaching';

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

function fetchCacheMode(rule: RuntimeCaching | undefined) {
  return (rule?.handler as { fetchOptions?: RequestInit }).fetchOptions?.cache;
}

describe('runtime caching order (#484 review)', () => {
  it('fetches every private artifact and speech request with no-store', () => {
    for (const origin of [app, 'https://backend.fixture']) {
      for (const path of [
        '/generated-audio/a.mp3',
        '/generated-images/a.png',
        '/generated-files/report.csv',
        '/generated-files/report.js',
        '/%67enerated-audio/b.mp3',
        '/tts/stream/v1',
      ]) {
        const rule = selectedRule(`${origin}${path}`);
        expect(rule?.handler, path).toBeInstanceOf(NetworkOnly);
        expect(fetchCacheMode(rule), path).toBe('no-store');
      }
    }
  });

  it("still caches the app's own static assets", () => {
    expect(selectedRule(`${app}/icons/icon.png`)?.handler).not.toBeInstanceOf(
      NetworkOnly,
    );
    expect(
      selectedRule(`${app}/_next/static/app.js`)?.handler,
    ).not.toBeInstanceOf(NetworkOnly);
  });
});
