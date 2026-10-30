import { expect, test as base, type Page } from '@playwright/test';

// Deliberate non-PWA capability model, not successful registration emulation.
export function withoutServiceWorker() {
  if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
    throw new Error(
      'Online UI fixture must not have a service-worker controller',
    );
  }
  if (
    !Reflect.deleteProperty(Navigator.prototype, 'serviceWorker') ||
    'serviceWorker' in navigator
  ) {
    throw new Error(
      'Could not establish unsupported service-worker capability',
    );
  }
}

export const test = base.extend({
  serviceWorkers: 'block',
  page: async ({ page }, providePage) => {
    await page.addInitScript(withoutServiceWorker);
    await providePage(page);
    expect(await page.evaluate(() => 'serviceWorker' in navigator)).toBe(false);
  },
});

export function isCacheClearRequest(
  url: URL,
  method: string,
  appOrigin: string,
) {
  return (
    url.origin === appOrigin &&
    method === 'POST' &&
    url.pathname === '/api/v1/auth/cache/clear' &&
    url.search === ''
  );
}

export async function mockCacheClear(page: Page) {
  await page.route('**/api/v1/auth/cache/clear', async (route) => {
    const request = route.request();
    if (
      !isCacheClearRequest(
        new URL(request.url()),
        request.method(),
        new URL(page.url()).origin,
      )
    ) {
      await route.abort('blockedbyclient');
      return;
    }
    await route.fulfill({
      status: 204,
      headers: { 'Cache-Control': 'no-store', 'Clear-Site-Data': '"cache"' },
    });
  });
}
