import { expect, test, type Page } from '@playwright/test';

const conversation = '11111111-1111-4111-8111-111111111111';
const snapshot = '22222222-2222-4222-8222-000000000001';
const path = `/conversations/${conversation}/web-snapshots`;
const direct = 'http://127.0.0.1:3103/daemon';

async function controlled(page: Page) {
  await page.goto('/landing');
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller)
      await new Promise<void>((resolve) =>
        navigator.serviceWorker.addEventListener(
          'controllerchange',
          () => resolve(),
          { once: true },
        ),
      );
  });
  expect(
    await page.evaluate(() => navigator.serviceWorker.controller?.scriptURL),
  ).toContain('/sw.js');
}

test('production SW never caches same-origin or configured-origin snapshot GETs', async ({
  page,
  context,
}) => {
  await controlled(page);
  const urls = [
    `${path}?limit=20`,
    `${path}/${snapshot}/export`,
    `${direct}${path}?limit=20`,
    `${direct}${path}/${snapshot}/export`,
  ];
  const statuses = await page.evaluate(async (targets) => {
    const results = [];
    for (const url of targets) {
      const response = await fetch(url, {
        headers: { Authorization: 'Bearer sources-fixture-token' },
        cache: 'no-store',
      });
      await response.arrayBuffer();
      results.push(response.status);
    }
    return results;
  }, urls);
  expect(statuses).toEqual([200, 200, 200, 200]);
  const keys = await page.evaluate(async () => {
    const results: string[] = [];
    for (const name of await caches.keys())
      results.push(
        ...(await (await caches.open(name)).keys()).map(
          (request) => request.url,
        ),
      );
    return results;
  });
  expect(keys.filter((key) => key.includes('/web-snapshots'))).toEqual([]);

  // A leftover entry inserted after activation still must never serve as an
  // offline fallback. This is fictional, not a read of anyone's private cache.
  await page.evaluate(async (url) => {
    const cache = await caches.open('others');
    await cache.put(
      url,
      new Response('FICTIONAL OLD SNAPSHOT', {
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  }, `${direct}${path}/${snapshot}/export`);
  await context.setOffline(true);
  expect(
    await page.evaluate(async (url) => {
      try {
        await fetch(url, {
          headers: { Authorization: 'Bearer sources-fixture-token' },
        });
        return 'unexpected response';
      } catch {
        return 'network denied';
      }
    }, `${direct}${path}/${snapshot}/export`),
  ).toBe('network denied');
  await context.setOffline(false);
});

test('new worker activation removes only matching old snapshot cache entries', async ({
  page,
  context,
}) => {
  await controlled(page);
  await page.evaluate(
    async ({ path, snapshot, direct }) => {
      const cache = await caches.open('others');
      for (const url of [
        `${location.origin}${path}?offset=20`,
        `${direct}${path}/${snapshot}/export`,
        `${location.origin}/fixture-sentinel`,
        `${location.origin}/assets/web-snapshots.png`,
        `https://foreign.test${path}`,
      ])
        await cache.put(url, new Response('FICTIONAL CACHE SENTINEL'));
      const assets = await caches.open('static-resources');
      await assets.put(
        `${location.origin}/fixture-asset.js`,
        new Response('FICTIONAL ASSET SENTINEL'),
      );
      for (const registration of await navigator.serviceWorker.getRegistrations())
        await registration.unregister();
    },
    { path, snapshot, direct },
  );
  await page.close();
  const fresh = await context.newPage();
  await controlled(fresh);
  const state = await fresh.evaluate(async () => {
    const cache = await caches.open('others');
    const keys = (await cache.keys()).map((request) => request.url);
    const sentinel = await (await cache.match('/fixture-sentinel'))?.text();
    const asset = await (
      await (await caches.open('static-resources')).match('/fixture-asset.js')
    )?.text();
    return { keys, sentinel, asset };
  });
  expect(
    state.keys.filter(
      (key) =>
        key.includes('/web-snapshots') &&
        !key.includes('/assets/') &&
        !key.startsWith('https://foreign.test'),
    ),
  ).toEqual([]);
  expect(state.keys).toContain('https://foreign.test' + path);
  expect(state.sentinel).toBe('FICTIONAL CACHE SENTINEL');
  expect(state.asset).toBe('FICTIONAL ASSET SENTINEL');
});

test('live production upgrade reconciles a prior worker in-flight snapshot write', async ({
  page,
  request,
}) => {
  await controlled(page);
  await page.evaluate(async () => {
    await navigator.serviceWorker.register('/legacy-snapshot-worker.js', {
      scope: '/',
    });
  });
  await expect
    .poll(() =>
      page.evaluate(() => navigator.serviceWorker.controller?.scriptURL),
    )
    .toContain('/legacy-snapshot-worker.js');
  const pending = page.evaluate(async (url) => {
    const response = await fetch(url, {
      headers: { Authorization: 'Bearer sources-fixture-token' },
    });
    await response.text();
    return response.status;
  }, `${direct}${path}/${snapshot}/export?hold=true`);
  await expect
    .poll(
      async () =>
        (await (await request.get('/__fixture/status')).json()).heldExports,
    )
    .toBe(1);
  try {
    await page.evaluate(async () => {
      await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    });
    await request.get('/__fixture/release');
    expect(await pending).toBe(200);
    await expect
      .poll(() =>
        page.evaluate(() => navigator.serviceWorker.controller?.scriptURL),
      )
      .toContain('/sw.js');
    // Activation cannot be accepted on controller identity alone: wait for the
    // async event's cleanup, and ensure the completed old write was removed.
    await expect
      .poll(() =>
        page.evaluate(
          async () =>
            (await (await caches.open('others')).keys()).filter((entry) =>
              entry.url.includes('/web-snapshots'),
            ).length,
        ),
      )
      .toBe(0);
  } finally {
    await request.get('/__fixture/release');
  }
});
