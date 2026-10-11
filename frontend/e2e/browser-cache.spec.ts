import { expect, test, type Page } from '@playwright/test';

const API = 'http://127.0.0.1:3143';
const artifacts = [
  '/generated-files/fictional-account-output.txt',
  `${API}/daemon/generated-files/fictional-account-output.txt`,
];

async function readArtifacts(page: Page, owner: 'a' | 'b' = 'a') {
  return page.evaluate(
    async ({ urls, owner }) => {
      const values = [];
      for (const url of urls)
        values.push(
          await (
            await fetch(url, {
              headers: { Authorization: `Bearer fictional-owner-${owner}` },
            })
          ).text(),
        );
      return values;
    },
    { urls: artifacts, owner },
  );
}

test.beforeEach(async ({ page, request }) => {
  await expect
    .poll(async () => {
      try {
        return (await request.get(`${API}/__fixture/status`)).status();
      } catch {
        return 0;
      }
    })
    .toBe(200);
  await request.post('/__fixture/reset');
  await request.post(`${API}/__fixture/reset`);
  await page.goto('/__fixture/seed');
  expect(
    await page.evaluate(() => navigator.serviceWorker.controller),
  ).toBeNull();
  expect(await page.evaluate(() => window.isSecureContext)).toBe(true);
});

test('upgrade and account switch clears genuine old HTTP-cache entries at BOTH origins without a worker', async ({
  page,
  request,
  context,
}) => {
  await context.addCookies([
    {
      name: 'fictional-retained-cookie',
      value: 'retained',
      url: 'http://127.0.0.1:3142',
    },
  ]);
  await page.evaluate(() => {
    localStorage.setItem('fictional-retained-storage', 'retained');
    sessionStorage.setItem('fictional-retained-session', 'retained');
  });
  expect(await readArtifacts(page)).toEqual([
    'FICTIONAL_OLD_OWNER_A',
    'FICTIONAL_OLD_OWNER_A',
  ]);
  expect(await readArtifacts(page)).toEqual([
    'FICTIONAL_OLD_OWNER_A',
    'FICTIONAL_OLD_OWNER_A',
  ]);
  expect((await (await request.get('/__fixture/status')).json()).hits).toBe(1);
  expect(
    (await (await request.get(`${API}/__fixture/status`)).json()).hits,
  ).toBe(1);
  await request.post('/__fixture/upgrade');
  await request.post(`${API}/__fixture/upgrade`);
  // Control: new no-store headers alone do not invalidate already fresh cache.
  expect(await readArtifacts(page, 'b')).toEqual([
    'FICTIONAL_OLD_OWNER_A',
    'FICTIONAL_OLD_OWNER_A',
  ]);
  // Actual Next auth proxy transports the production cache-clear response.
  const clearWire = page.waitForResponse((response) =>
    response.url().endsWith('/api/v1/auth/cache/clear'),
  );
  const clear = await page.evaluate(async () => {
    const response = await fetch('/api/v1/auth/cache/clear', {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
    });
    return {
      status: response.status,
      header: response.headers.get('Clear-Site-Data'),
    };
  });
  expect(clear).toEqual({ status: 204, header: null });
  expect(await (await clearWire).headerValue('clear-site-data')).toBe(
    '"cache"',
  );
  // Discriminator: frontend response does NOT clear the separate API origin.
  expect(await readArtifacts(page, 'b')).toEqual([
    'FICTIONAL_NEW_OWNER_B',
    'FICTIONAL_OLD_OWNER_A',
  ]);
  const directWire = page.waitForResponse(`${API}/daemon/v1/auth/cache/clear`);
  const direct = await page.evaluate(async (url) => {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
    });
    return {
      status: response.status,
      header: response.headers.get('Clear-Site-Data'),
      cacheControl: response.headers.get('Cache-Control'),
    };
  }, `${API}/daemon/v1/auth/cache/clear`);
  expect(direct).toEqual({
    status: 204,
    header: null,
    cacheControl: 'no-store',
  });
  expect(await (await directWire).headerValue('clear-site-data')).toBe(
    '"cache"',
  );
  expect(await readArtifacts(page, 'b')).toEqual([
    'FICTIONAL_NEW_OWNER_B',
    'FICTIONAL_NEW_OWNER_B',
  ]);
  expect(
    await page.evaluate(() => ({
      local: localStorage.getItem('fictional-retained-storage'),
      session: sessionStorage.getItem('fictional-retained-session'),
    })),
  ).toEqual({ local: 'retained', session: 'retained' });
  expect(
    (await context.cookies()).find(
      (cookie) => cookie.name === 'fictional-retained-cookie',
    )?.value,
  ).toBe('retained');
  expect(
    await page.evaluate(() => navigator.serviceWorker.controller),
  ).toBeNull();
});

test('production client barrier clears both legacy caches before reads and deduplicates concurrent callers', async ({
  page,
  request,
}) => {
  expect(await readArtifacts(page)).toEqual([
    'FICTIONAL_OLD_OWNER_A',
    'FICTIONAL_OLD_OWNER_A',
  ]);
  await request.post('/__fixture/upgrade');
  await request.post(`${API}/__fixture/upgrade`);
  const state = await page.evaluate(async () => {
    const moduleUrl = '/__fixture/cache-client.js';
    const cache = (await import(
      moduleUrl
    )) as typeof import('../lib/browserCache');
    await Promise.all([
      cache.ensureBrowserCacheCleared(),
      cache.ensureBrowserCacheCleared(),
    ]);
    return cache.getBrowserCacheState();
  });
  expect(state.status).toBe('ready');
  // The direct call and app proxy each reach the actual production API once.
  expect(
    (await (await request.get(`${API}/__fixture/status`)).json()).clears,
  ).toBe(2);
  expect(await readArtifacts(page)).toEqual([
    'FICTIONAL_NEW_OWNER_B',
    'FICTIONAL_NEW_OWNER_B',
  ]);
});

test('production client failed cleanup stays blocked until explicit retry', async ({
  page,
  request,
}) => {
  expect(await readArtifacts(page)).toEqual([
    'FICTIONAL_OLD_OWNER_A',
    'FICTIONAL_OLD_OWNER_A',
  ]);
  await request.post('/__fixture/upgrade');
  await request.post(`${API}/__fixture/upgrade`);
  await request.post(`${API}/__fixture/fail/1`);
  const failure = await page.evaluate(async () => {
    const moduleUrl = '/__fixture/cache-client.js';
    const cache = (await import(
      moduleUrl
    )) as typeof import('../lib/browserCache');
    let released = false;
    try {
      await cache.ensureBrowserCacheCleared();
      released = true;
    } catch {
      /* expected */
    }
    return { released, state: cache.getBrowserCacheState() };
  });
  expect(failure.released).toBe(false);
  expect(failure.state.status).toBe('failed');
  expect(await readArtifacts(page)).toEqual([
    'FICTIONAL_OLD_OWNER_A',
    'FICTIONAL_OLD_OWNER_A',
  ]);
  await request.post(`${API}/__fixture/fail/0`);
  const retry = await page.evaluate(async () => {
    const moduleUrl = '/__fixture/cache-client.js';
    const cache = (await import(
      moduleUrl
    )) as typeof import('../lib/browserCache');
    await cache.startBrowserCacheClearAttempt({ reason: 'retry' });
    await cache.ensureBrowserCacheCleared();
    return cache.getBrowserCacheState();
  });
  expect(retry.status).toBe('ready');
  expect(await readArtifacts(page)).toEqual([
    'FICTIONAL_NEW_OWNER_B',
    'FICTIONAL_NEW_OWNER_B',
  ]);
});

test('production client rejects an insecure non-loopback API configuration', async ({
  page,
  request,
}) => {
  const result = await page.evaluate(async () => {
    const moduleUrl = '/__fixture/cache-client.js?variant=insecure';
    const cache = (await import(
      moduleUrl
    )) as typeof import('../lib/browserCache');
    let released = false;
    try {
      await cache.ensureBrowserCacheCleared();
      released = true;
    } catch {
      /* expected */
    }
    return { released, status: cache.getBrowserCacheState().status };
  });
  expect(result).toEqual({ released: false, status: 'failed' });
  expect(
    (await (await request.get(`${API}/__fixture/status`)).json()).clears,
  ).toBe(0);
});

for (const mode of ['missing-no-store', 'redirect', 'stall']) {
  test(`production client fails closed for real ${mode} responses`, async ({
    page,
    request,
  }) => {
    await request.post(`${API}/__fixture/mode/${mode}`);
    const result = await page.evaluate(async () => {
      const moduleUrl = '/__fixture/cache-client.js';
      const cache = (await import(
        moduleUrl
      )) as typeof import('../lib/browserCache');
      let released = false;
      try {
        await cache.ensureBrowserCacheCleared();
        released = true;
      } catch {
        /* expected */
      }
      return { released, status: cache.getBrowserCacheState().status };
    });
    expect(result).toEqual({ released: false, status: 'failed' });
    expect(
      (await (await request.get(`${API}/__fixture/status`)).json()).hits,
    ).toBe(0);
  });
}

test('production client same-origin configuration requires only the app proxy', async ({
  page,
  request,
}) => {
  const result = await page.evaluate(async () => {
    const moduleUrl = '/__fixture/cache-client.js?variant=same-origin';
    const cache = (await import(
      moduleUrl
    )) as typeof import('../lib/browserCache');
    await cache.ensureBrowserCacheCleared();
    return cache.getBrowserCacheState().status;
  });
  expect(result).toBe('ready');
  expect(
    (await (await request.get(`${API}/__fixture/status`)).json()).clears,
  ).toBe(1);
});

test('production trusted-origin policy rejects a foreign app origin and the barrier stays blocked', async ({
  page,
}) => {
  // Same fixture bytes, different browser origin. Do not expand the API's
  // allowlist or override browser-generated Origin / Sec-Fetch-Site headers.
  await page.goto('http://localhost:3142/__fixture/seed');
  const rejectedWire = page.waitForResponse((response) =>
    response.url().endsWith('/api/v1/auth/cache/clear'),
  );
  const result = await page.evaluate(async () => {
    const moduleUrl = '/__fixture/cache-client.js';
    const cache = (await import(
      moduleUrl
    )) as typeof import('../lib/browserCache');
    let released = false;
    try {
      await cache.ensureBrowserCacheCleared();
      released = true;
    } catch {
      /* expected */
    }
    return { released, status: cache.getBrowserCacheState().status };
  });
  expect(result).toEqual({ released: false, status: 'failed' });
  const wire = await rejectedWire;
  expect(wire.status()).toBe(403);
  expect(await wire.headerValue('Clear-Site-Data')).toBeNull();
});

test('actual AuthProvider holds protected children on startup failure, retries, then re-clears for sign-in B', async ({
  page,
  request,
}) => {
  expect(await readArtifacts(page)).toEqual([
    'FICTIONAL_OLD_OWNER_A',
    'FICTIONAL_OLD_OWNER_A',
  ]);
  await request.post('/__fixture/upgrade');
  await request.post(`${API}/__fixture/upgrade`);
  await request.post(`${API}/__fixture/fail/1`);
  await page.goto('/auth?cache-fixture');
  await expect(page.getByRole('button', { name: /retry/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible();
  await expect(page.getByTestId('protected-cache-probe')).toHaveCount(0);
  expect(
    (await (await request.get(`${API}/__fixture/status`)).json()).hits,
  ).toBe(1);
  await request.post(`${API}/__fixture/fail/0`);
  await page.getByRole('button', { name: /retry/i }).click();
  await expect(page.getByTestId('protected-cache-probe')).toHaveText(
    'No protected read',
  );
  await request.post(`${API}/__fixture/upstream/plan`, {
    data: { 'email-complete': ['success'] },
  });
  await page.getByRole('button', { name: 'Fictional sign-in as B' }).click();
  await expect(page.getByTestId('protected-cache-probe')).toHaveText(
    'FICTIONAL_NEW_OWNER_B',
  );
  expect(
    (await (await request.get(`${API}/__fixture/status`)).json()).clears,
  ).toBeGreaterThanOrEqual(6);
  expect(
    await page.evaluate(() => navigator.serviceWorker.controller),
  ).toBeNull();
});

test.describe('cache-only service-worker registration scope', () => {
  test.use({ serviceWorkers: 'allow' });
  test('clearing cache preserves an existing registration without controlling reads', async ({
    page,
  }) => {
    await page.evaluate(async () => {
      const registration = await navigator.serviceWorker.register(
        '/__fixture/passive-worker.js',
        { scope: '/__fixture/uncontrolled/' },
      );
      const worker =
        registration.installing || registration.waiting || registration.active;
      if (!worker) throw new Error('Fictional passive worker missing');
      if (worker.state !== 'activated')
        await new Promise<void>((resolve) => {
          worker.addEventListener('statechange', () => {
            if (worker.state === 'activated') resolve();
          });
        });
      const moduleUrl = '/__fixture/cache-client.js';
      const cache = (await import(
        moduleUrl
      )) as typeof import('../lib/browserCache');
      await cache.ensureBrowserCacheCleared();
    });
    expect(
      await page.evaluate(async () =>
        (await navigator.serviceWorker.getRegistrations()).map(
          (registration) => registration.scope,
        ),
      ),
    ).toEqual(['http://127.0.0.1:3142/__fixture/uncontrolled/']);
    expect(
      await page.evaluate(() => navigator.serviceWorker.controller),
    ).toBeNull();
  });
});
