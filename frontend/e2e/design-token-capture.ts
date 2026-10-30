import { expect, type Page, type Request } from '@playwright/test';
import { test as base } from './online-ui-fixture';
import {
  isAcknowledgedPrefetchCancellation,
  isAcknowledgedRscPrefetch,
} from './settings-rsc-fixture';

type HttpFailure = { url: string; status: number };

// #490 identifies CSP-blocked Next DevTools styles even with devIndicators:false.
// These exact hashes come from the preserved qualification console. Keep the raw log;
// this is NOT permission to ignore application CSP errors or new DevTools errors.
const knownDevtoolsStyleHashes = new Set([
  '1Rgj9Tmw9SU4h9nklK6l294O2abdhNs1l+3ZoCwzxUA=',
  '3+bKrR/84X2JP+JJRRFiCWBPPJc3WfBS5KLjDVMO5+g=',
  '47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=',
  '4GzqRlwcGp7cD3kANBs5f/CO/iqLwfJq0WYAq59xN6E=',
  'AjcL04oAi53k+lKnUCWkPFYAHHf6g47FT8ZMT/uIQcg=',
  'CHox7CF+XF5itK7YJMt8fpMltQv4/krVGqYhlFzvJys=',
  'EGxSJ+22silg1bQ9JENEw9K11R7B+X79u4xwD5Qlxik=',
  'F6SKLtWtm7VErmrq0jDQtzJvpz6+LcrSfbyczc2xd6o=',
  'Fu0iZjYNW/YipXDINQODnnUG4Hfm6DrmPbF1ACKuddg=',
  'HKMGAqMmqgowMaAiaHmP7z5275Kf1EEgtYH4KLhGvEU=',
  'KBtJ4JWhXHy2qCIZXBJwmfNBZsC2vng7RL8uqWTaVCo=',
  'NCr0hKo6+EaNbDF54ltHki5tjN4BpNFiX5hQdhMF4uc=',
  'OpIODBPkpoiPLrmWVWQAuUTdbHmO2qBKzuQ9qR3th9M=',
  'RVffQIpfFsSm9AH75W6q/PI66qRwUOG/U3CG59GQaxI=',
  'exsY7OwkzwRZ1WX8UbmUyt2R4OnJuqdLLVh33pnRh+M=',
  'h/GKjXBq1QiwvO1y7p0JkvVZk4w7xTPz2rn2eswXu3k=',
  'sHwQzC2ZsVrt1faUYCjF/eo8aIoBlQbGjVstzanL9CU=',
  'tzBMNZ7l8nZ/8oEITbEGeJAgNHg7KpJOduvhc+LO138=',
  'ycf8S3nPxP73PrOfAPF4GELfJeuuJLvbx+CMMPsWj60=',
]);

function knownDevtoolsCsp(text: string, source: string) {
  if (
    source !==
    'webpack-internal:///(app-pages-browser)/./node_modules/next/dist/compiled/next-devtools/index.js'
  )
    return false;
  const match = text.match(
    /^Applying inline style violates the following Content Security Policy directive 'style-src 'self' 'nonce-[0-9a-f]{32}' https:\/\/accounts\.google\.com\/gsi\/style'\. Either the 'unsafe-inline' keyword, a hash \('sha256-([^']+)'\), or a nonce \('nonce-\.\.\.'\) is required to enable inline execution\. The action has been blocked\.$/,
  );
  return !!match && knownDevtoolsStyleHashes.has(match[1]);
}

export const test = base.extend<{
  acknowledgedPrefetchPaths: string[];
  captureDiagnostics: {
    expectedHttpFailures: HttpFailure[];
    assertClean: () => void;
  };
}>({
  acknowledgedPrefetchPaths: [[], { option: true }],
  captureDiagnostics: [
    async (
      { page, browser, baseURL, acknowledgedPrefetchPaths },
      use,
      testInfo,
    ) => {
      const pageErrors: string[] = [];
      const consoleMessages: {
        type: string;
        text: string;
        location: { url: string; lineNumber: number; columnNumber: number };
      }[] = [];
      const responses: HttpFailure[] = [];
      const bodylessCacheClears = new WeakSet<Request>();
      const acknowledgedPrefetches = new WeakSet<Request>();
      const prefetchResponses: {
        url: string;
        method: string;
        status: number;
        contentType: string;
        rsc: string;
        prefetch: string;
      }[] = [];
      const failedRequests: {
        url: string;
        error: string | null;
        acknowledgedBodylessCacheClear: boolean;
        acknowledgedPrefetchCancellation: boolean;
      }[] = [];
      const expectedHttpFailures: HttpFailure[] = [];
      page.on('pageerror', (error) =>
        pageErrors.push(error.stack ?? error.message),
      );
      page.on('console', (message) => {
        if (['error', 'warning'].includes(message.type())) {
          consoleMessages.push({
            type: message.type(),
            text: message.text(),
            location: message.location(),
          });
        }
      });
      page.on('response', (response) => {
        responses.push({ url: response.url(), status: response.status() });
        const request = response.request();
        const contentType = response.headers()['content-type'] ?? '';
        if (
          baseURL &&
          isAcknowledgedRscPrefetch(
            new URL(request.url()),
            request.method(),
            request.headers(),
            new URL(baseURL).origin,
            acknowledgedPrefetchPaths,
            response.status(),
            contentType,
          )
        ) {
          acknowledgedPrefetches.add(request);
          prefetchResponses.push({
            url: request.url(),
            method: request.method(),
            status: response.status(),
            contentType,
            rsc: request.headers().rsc,
            prefetch: request.headers()['next-router-prefetch'],
          });
        }
        if (
          response.status() === 204 &&
          response.request().method() === 'POST' &&
          response.url() ===
            new URL('/api/v1/auth/cache/clear', page.url()).href
        ) {
          bodylessCacheClears.add(response.request());
        }
      });
      page.on('requestfailed', (request) => {
        failedRequests.push({
          url: request.url(),
          error: request.failure()?.errorText ?? null,
          // Chromium can report ERR_ABORTED after an acknowledged routed 204
          // whose body is not consumed. A standalone headers-only fetch probe
          // reproduces it; a body-consuming control does not. Never exempt a
          // missing response, a different endpoint/status, or another failure.
          acknowledgedBodylessCacheClear:
            bodylessCacheClears.has(request) &&
            request.failure()?.errorText === 'net::ERR_ABORTED',
          acknowledgedPrefetchCancellation: isAcknowledgedPrefetchCancellation(
            acknowledgedPrefetches.has(request),
            request.failure()?.errorText ?? null,
          ),
        });
      });

      const assertClean = () => {
        expect(pageErrors, 'Uncaught browser runtime errors').toEqual([]);
        expect(
          failedRequests.filter(
            (request) =>
              !request.acknowledgedBodylessCacheClear &&
              !request.acknowledgedPrefetchCancellation,
          ),
          'Failed browser requests',
        ).toEqual([]);
        expect(
          responses.filter(
            (response) =>
              response.status >= 400 &&
              !expectedHttpFailures.some(
                (expected) =>
                  expected.url === response.url &&
                  expected.status === response.status,
              ),
          ),
          'Unexpected HTTP failures',
        ).toEqual([]);
        expect(
          consoleMessages.filter(
            (message) =>
              message.type === 'error' &&
              !knownDevtoolsCsp(message.text, message.location.url) &&
              !expectedHttpFailures.some(
                (expected) =>
                  message.location.url === expected.url &&
                  message.text ===
                    `Failed to load resource: the server responded with a status of ${expected.status} (${expected.status === 404 ? 'Not Found' : 'Service Unavailable'})`,
              ),
          ),
          'Unexpected browser console errors (see attached diagnostics)',
        ).toEqual([]);
      };

      await use({ expectedHttpFailures, assertClean });
      await testInfo.attach('browser-diagnostics.json', {
        body: JSON.stringify(
          {
            browserVersion: browser.version(),
            pageErrors,
            consoleMessages,
            knownDevtoolsCspCount: consoleMessages.filter((message) =>
              knownDevtoolsCsp(message.text, message.location.url),
            ).length,
            responses,
            failedRequests,
            prefetchResponses,
            expectedHttpFailures,
          },
          null,
          2,
        ),
        contentType: 'application/json',
      });
      if (pageErrors.length) {
        await testInfo.attach('runtime-error-visible', {
          body: await page.screenshot({ fullPage: true }),
          contentType: 'image/png',
        });
      }
      // Check again after the screenshot/assertion, including on test failure.
      assertClean();
    },
    { auto: true },
  ],
});

export async function captureSurface(page: Page, nestedScroller: boolean) {
  await page.evaluate(() => document.fonts.ready);
  await expect(
    page.getByRole('button', { name: 'Open Next.js Dev Tools', exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Open issues overlay', exact: true }),
  ).toHaveCount(0);
  await expect(page.locator('[data-nextjs-dialog]')).toHaveCount(0);

  const measure = () =>
    page.evaluate(() => {
      const main = document.querySelector('main');
      if (!main)
        throw new Error('Capture requires the application main surface');
      const rect = main.getBoundingClientRect();
      return {
        viewport: {
          width: innerWidth,
          height: innerHeight,
          dpr: devicePixelRatio,
        },
        locale: navigator.language,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        now: new Date().toISOString(),
        fonts: document.fonts.status,
        fontFamily: getComputedStyle(main).fontFamily,
        imagesLoaded: [...document.images].every(
          (image) => image.complete && image.naturalWidth > 0,
        ),
        document: {
          width: document.documentElement.scrollWidth,
          height: document.documentElement.scrollHeight,
          top: scrollY,
        },
        main: {
          x: rect.x,
          y: rect.y,
          width: rect.width,
          height: rect.height,
          clientHeight: main.clientHeight,
          scrollHeight: main.scrollHeight,
          scrollTop: main.scrollTop,
          overflowY: getComputedStyle(main).overflowY,
        },
      };
    });
  const surface = await measure();
  expect(surface.viewport).toEqual({ width: 1440, height: 1000, dpr: 1 });
  expect(surface.locale).toBe('en-US');
  expect(surface.timezone).toBe('UTC');
  expect(surface.now).toBe('2026-09-22T12:00:00.000Z');
  expect(surface.fonts).toBe('loaded');
  expect(surface.fontFamily).not.toBe('');
  expect(surface.imagesLoaded).toBe(true);
  expect(surface.document.top).toBe(0);
  expect(surface.main.scrollTop).toBe(0);
  expect(surface.main.width).toBeGreaterThan(0);
  expect(surface.main.height).toBeGreaterThan(0);
  if (nestedScroller) {
    // fullPage captures the document, NOT the offscreen contents of MAIN.
    // Preserve the original top viewport; additional scrolled goldens need approval.
    expect(surface.main.overflowY).toBe('auto');
    expect(surface.main.clientHeight).toBe(1000);
    expect(surface.main.scrollHeight).toBeGreaterThan(
      surface.main.clientHeight,
    );
    expect(surface.document.height).toBe(1000);
  }
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  expect(
    await measure(),
    'Capture geometry must be stable across animation frames',
  ).toEqual(surface);
  return surface;
}
