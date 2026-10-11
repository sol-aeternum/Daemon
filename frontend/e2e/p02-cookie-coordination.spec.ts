import {
  chromium,
  expect,
  test,
  type APIRequestContext,
  type Page,
} from '@playwright/test';
import { bindRendererCrash } from './renderer-crash';

// Actual production auth/provider/coordinator and actual Next proxy, native
// Chromium cookie jar/Web Locks/storage. Upstream identity responses alone are
// synthetic; this proves neither production HTTPS nor server transaction rollback.
const API = 'http://127.0.0.1:3143';
async function boundedOwnedCleanup(work: Promise<unknown>) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      work,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(
          () => reject(new Error('Owned browser cleanup timed out')),
          5000,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}
async function plan(
  request: APIRequestContext,
  value: Record<string, string[]>,
) {
  expect(
    (
      await request.post(`${API}/__fixture/upstream/plan`, { data: value })
    ).status(),
  ).toBe(204);
}
const state = (page: Page) => page.evaluate(() => window.__p02!.read());
const upstream = async (request: APIRequestContext) =>
  (await request.get(`${API}/__fixture/upstream/status`)).json();
async function open(page: Page, settledConfig = false) {
  await page.goto(
    `/auth?cache-fixture${settledConfig ? '&settled-auth-config' : ''}`,
  );
  await expect
    .poll(() => page.evaluate(() => window.__p02?.ready === true))
    .toBe(true);
}
async function signIn(
  page: Page,
  request: APIRequestContext,
  owner: 'a' | 'b',
  behavior = 'success',
) {
  await plan(request, { 'email-complete': [behavior] });
  expect(
    (await page.evaluate((owner) => window.__p02!.signIn(owner), owner))
      .success,
  ).toBe(true);
  await page.evaluate(() => window.__p02!.clearCache());
  await expect
    .poll(async () => (await state(page)).header)
    .toBe(`Bearer fictional-owner-${owner}`);
}
test.beforeEach(async ({ request }) => {
  expect((await request.post('/__fixture/reset')).status()).toBe(204);
  expect((await request.post(`${API}/__fixture/reset`)).status()).toBe(204);
});

test('two tabs keep A private through delayed headers/body, reject without cookie replacement, then fence A on B success', async ({
  page,
  context,
  request,
}) => {
  await open(page);
  await signIn(page, request, 'a');
  const a = await state(page);
  // AuthProvider intentionally permits sign-in UI on /auth. Protected-route
  // acceptance must exercise the private route after identity initialization.
  await page.evaluate(() => history.replaceState(null, '', '/'));
  const cookieA = (await context.cookies()).find(
    (cookie) => cookie.name === 'daemon_refresh',
  );
  expect(cookieA).toMatchObject({
    value: 'fictional-refresh-a',
    httpOnly: true,
  });
  const peer = await context.newPage();
  await open(peer);
  await plan(request, { refresh: ['refresh-success'] });
  expect((await peer.evaluate(() => window.__p02!.refresh())).success).toBe(
    true,
  );
  await peer.evaluate(() => window.__p02!.clearCache());
  await page.evaluate(() => window.__p02!.clearCache());
  await peer.evaluate(() => history.replaceState(null, '', '/'));
  expect((await state(peer)).token).toBe('fictional-rotated-a');
  await plan(request, { 'email-complete': ['reject-delayed'] });
  const rejected = page.evaluate(() => window.__p02!.reject());
  await expect
    .poll(async () => (await state(peer)).problem)
    .toBe('Finishing session coordination.');
  await page.evaluate(() => window.__p02!.clearCache());
  for (const tab of [page, peer]) {
    expect(await state(tab)).toMatchObject({
      token: null,
      header: null,
      cacheStatus: 'ready',
      record: {
        status: 'pending',
        epoch: (a.record as { epoch: string }).epoch,
      },
    });
    await expect(tab.getByTestId('protected-cache-probe')).toHaveCount(0);
  }
  expect((await rejected).success).toBe(false);
  await expect
    .poll(async () => (await state(peer)).header)
    .toBe('Bearer fictional-rotated-a');
  expect(await state(page)).toMatchObject({
    token: 'fictional-owner-a',
    record: a.record,
  });
  expect(
    (await context.cookies()).find(
      (cookie) => cookie.name === 'daemon_refresh',
    ),
  ).toEqual(cookieA);
  await signIn(page, request, 'b');
  await expect.poll(async () => (await state(peer)).token).toBeNull();
  expect((await state(peer)).header).toBeNull();
  expect((await state(page)).record).not.toEqual(a.record);
  expect(
    (await context.cookies()).find((cookie) => cookie.name === 'daemon_refresh')
      ?.value,
  ).toBe('fictional-refresh-b');
  await peer.close();
});

test('held upstream A survives local exit, but cannot deliver cookies across B login, expiry and fresh B refresh', async ({
  page,
  context,
  request,
}) => {
  await open(page);
  await signIn(page, request, 'a');
  await plan(request, {
    'email-complete': ['hold-success', 'success-expiring'],
  });
  const old = page.evaluate(() => window.__p02!.signIn('a'));
  await expect
    .poll(async () => (await upstream(request)).outstandingHolds)
    .toBe(1);
  await page.evaluate(() => window.__p02!.exit());
  expect((await old).success).toBe(false);
  expect((await state(page)).token).toBeNull();
  expect((await page.evaluate(() => window.__p02!.signIn('b'))).success).toBe(
    true,
  );
  expect((await upstream(request)).outstandingHolds).toBe(1); // no proxy/upstream abort propagation
  await expect.poll(async () => (await state(page)).valid).toBe(false); // 31.5s expiry, 30s safety buffer
  await plan(request, { refresh: ['refresh-success'] });
  expect((await page.evaluate(() => window.__p02!.refresh())).success).toBe(
    true,
  );
  expect((await state(page)).token).toBe('fictional-rotated-b');
  expect(
    (await request.post(`${API}/__fixture/upstream/release`)).status(),
  ).toBe(204);
  await expect
    .poll(
      async () =>
        (await upstream(request)).events.filter(
          (event: string[]) =>
            event[1] === 'hold-success' && event[2] === 'step-body',
        ).length,
    )
    .toBe(1);
  expect(
    (await context.cookies()).find((cookie) => cookie.name === 'daemon_refresh')
      ?.value,
  ).toBe('fictional-refresh-b');
  expect((await state(page)).token).toBe('fictional-rotated-b');
});

test('actual proxy retries a dropped upstream once; qualified rejection retains A without an exactly-once claim', async ({
  page,
  context,
  request,
}) => {
  await open(page);
  await signIn(page, request, 'a');
  const before = await state(page);
  await plan(request, { 'email-complete': ['drop', 'reject'] });
  expect((await page.evaluate(() => window.__p02!.reject())).success).toBe(
    false,
  );
  expect(await state(page)).toMatchObject({
    token: 'fictional-owner-a',
    record: before.record,
  });
  const events = (await upstream(request)).events as string[][];
  expect(
    events.filter(
      (event) => event[1] === 'drop' && event[2] === 'dropping-connection',
    ),
  ).toHaveLength(1);
  expect(
    events.filter(
      (event) => event[1] === 'reject' && event[2] === 'attempt-started',
    ),
  ).toHaveLength(1);
  expect(
    (await context.cookies()).find((cookie) => cookie.name === 'daemon_refresh')
      ?.value,
  ).toBe('fictional-refresh-a');
});

for (const capability of ['locks', 'storage'] as const) {
  test(`unsupported ${capability} blocks every cookie writer but leaves local exit usable`, async ({
    page,
    request,
  }) => {
    await page.addInitScript((capability) => {
      if (capability === 'locks')
        Object.defineProperty(navigator, 'locks', { value: undefined });
      else
        Storage.prototype.setItem = () => {
          throw new Error('fictional blocked storage');
        };
    }, capability);
    await open(page, true);
    expect((await page.evaluate(() => window.__p02!.signIn('a'))).success).toBe(
      false,
    );
    expect((await page.evaluate(() => window.__p02!.refresh())).success).toBe(
      false,
    );
    // Check capabilities on the public sign-in route first. Enter the private
    // path and perform local exit atomically: a legitimate mode-aware redirect
    // must not race a second evaluation of a now-destroyed execution context.
    const exited = await page.evaluate(() => {
      history.replaceState(null, '', '/');
      window.__p02!.exit();
      return window.__p02!.read();
    });
    expect(exited).toMatchObject({ token: null, header: null, valid: false });
    expect((await upstream(request)).events).toEqual([]);
    await expect(page.getByTestId('protected-cache-probe')).toHaveCount(0);
  });
}

test('real cookie-writing fetches explicitly forbid keepalive and carry native owned AbortSignals', async ({
  page,
  request,
}) => {
  await page.addInitScript(() => {
    const nativeFetch = window.fetch;
    const calls: Array<{ path: string; keepalive?: boolean; signal: boolean }> =
      [];
    Reflect.set(window, '__p02FetchCalls', calls);
    window.fetch = (input, init) => {
      if (typeof input === 'string' && input === '/api/v1/auth/email/complete')
        calls.push({
          path: input,
          keepalive: init?.keepalive,
          signal: init?.signal instanceof AbortSignal,
        });
      return nativeFetch(input, init); // no delay, interception, altered signal or fake response
    };
  });
  await open(page);
  await signIn(page, request, 'a');
  expect(
    await page.evaluate(() => Reflect.get(window, '__p02FetchCalls')),
  ).toEqual([
    { path: '/api/v1/auth/email/complete', keepalive: false, signal: true },
  ]);
});

for (const termination of ['close', 'crash'] as const) {
  test(`frozen holder cannot be leased away, and ${termination} leaves abandoned uncertainty fail-closed`, async ({
    page: fixturePage,
    context: fixtureContext,
    request,
  }) => {
    let page = fixturePage;
    let context = fixtureContext;
    const server =
      termination === 'crash'
        ? await chromium.launchServer({
            headless: true,
            env: { PATH: process.env.PATH, HOME: process.env.HOME },
          })
        : null;
    let guard: Awaited<ReturnType<typeof bindRendererCrash>> | undefined;
    try {
      if (server) {
        const browser = await chromium.connect(server.wsEndpoint());
        context = await browser.newContext({
          baseURL: 'http://127.0.0.1:3142',
          serviceWorkers: 'block',
        });
        // Playwright's runner automatically traces every context created in
        // the test, including this launchServer context. Do not start a second
        // tracing lifecycle; normal context close retains the runner's trace.
        page = await context.newPage();
      }
      await open(page);
      await signIn(page, request, 'a');
      // The crash case must bind ALL kernel handles before tracing identifies
      // either page. Complete that setup before starting A's timed transaction.
      const earlyPeer = server ? await context.newPage() : null;
      if (server) {
        await open(earlyPeer!);
        guard = await bindRendererCrash(
          server,
          page.context().browser()!,
          page,
          earlyPeer!,
        );
      }
      await plan(request, { 'email-complete': ['hold-success'] });
      // Keep the observer rejection handled even when its execution context dies.
      const held = page
        .evaluate(() => window.__p02!.signIn('a'))
        .catch(() => null);
      await expect
        .poll(async () => (await upstream(request)).outstandingHolds)
        .toBe(1);
      const cdp = await context.newCDPSession(page);
      await cdp.send('Page.setWebLifecycleState', { state: 'frozen' });
      const peer = earlyPeer ?? (await context.newPage());
      if (!earlyPeer) {
        await open(peer);
      }
      // The approved +8s cutoff expires before A's +10s deadline. Racing two
      // +10s owners would legitimately allow B after A's native abort terminates.
      const bounded = await peer.evaluate(() => window.__p02!.logout());
      expect(bounded).toMatchObject({
        success: false,
        error: 'Session refresh cutoff reached; revocation unconfirmed',
      });
      expect(
        (await upstream(request)).events.filter(
          (event: string[]) => event[2] === 'held',
        ),
      ).toHaveLength(1);
      if (termination === 'close') {
        await page.close();
        await held;
      } else {
        await guard!.terminate();
        // A crashed renderer need not settle its evaluate observer. Its rejection
        // is handled above; only the live peer is evidence of post-crash safety.
      }
      expect(
        (await peer.evaluate(() => window.__p02!.signIn('b'))).success,
      ).toBe(false);
      expect(await state(peer)).toMatchObject({ token: null, header: null });
      expect(
        (await upstream(request)).events.filter(
          (event: string[]) => event[2] === 'attempt-started',
        ),
      ).toHaveLength(1); // initial A only
      await request.post(`${API}/__fixture/upstream/release`);
      await peer.close();
    } finally {
      if (server) {
        try {
          await guard?.close();
          await boundedOwnedCleanup(context.close());
        } finally {
          // Only Playwright's handle for the browser THIS case launched owns
          // teardown. No host-wide scan or raw PID cleanup fallback.
          await boundedOwnedCleanup(server.kill());
        }
      }
    }
  });
}

const SIGNOUT_MARKER = 'daemon:local-signout';
async function freshProtected(page: Page, name: string) {
  await page.goto(`/settings/p02-${name}?settled-auth-config`);
  await expect
    .poll(() => page.evaluate(() => window.__p02?.ready === true))
    .toBe(true);
}
const refreshAttempts = async (request: APIRequestContext) =>
  ((await upstream(request)).events as string[][]).filter(
    (event) =>
      event[0] === 'refresh' &&
      ['attempt-started', 'unplanned-arrival', 'dropping-connection'].includes(
        event[2],
      ),
  ).length;

test('persistent local exit fences actual navigation/reload and clones, not independent tabs; only owned acknowledged sign-in retires it', async ({
  page,
  context,
  request,
}) => {
  await open(page, true);
  await signIn(page, request, 'a');
  await plan(request, { refresh: ['refresh-success'] });
  expect((await request.post(`${API}/__fixture/fail/1`)).status()).toBe(204);
  await freshProtected(page, 'exit');
  await expect.poll(async () => (await state(page)).cacheStatus).toBe('failed');
  await expect.poll(async () => (await state(page)).refreshPending).toBe(false);
  await page.getByTestId('auth-cache-gate-signout').click();
  await page.waitForURL('/setup');
  await expect
    .poll(() => page.evaluate(() => window.__p02?.ready === true))
    .toBe(true);
  expect(
    await page.evaluate(() => sessionStorage.getItem('daemon:local-signout')),
  ).toBe('1');
  expect(
    (await context.cookies()).find((cookie) => cookie.name === 'daemon_refresh')
      ?.value,
  ).toBe('fictional-refresh-a');
  const before = await refreshAttempts(request);
  expect((await request.post(`${API}/__fixture/fail/0`)).status()).toBe(204);
  await freshProtected(page, 'exit-later');
  await expect.poll(async () => (await state(page)).cacheStatus).toBe('ready');
  expect(await state(page)).toMatchObject({
    token: null,
    header: null,
    valid: false,
  });
  await expect(page.getByTestId('protected-cache-probe')).toHaveCount(0);
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => window.__p02?.ready === true))
    .toBe(true);
  await expect.poll(async () => (await state(page)).cacheStatus).toBe('ready');
  expect(await refreshAttempts(request)).toBe(before);
  expect(new URL(page.url()).pathname).toBe('/settings/p02-exit-later');

  const popup = page.waitForEvent('popup');
  await page.evaluate(() =>
    window.open('/settings/p02-clone?settled-auth-config', '_blank'),
  );
  const clone = await popup;
  await expect
    .poll(() => clone.evaluate(() => window.__p02?.ready === true))
    .toBe(true);
  expect(
    await clone.evaluate(() => sessionStorage.getItem('daemon:local-signout')),
  ).toBe('1');
  expect((await state(clone)).header).toBeNull();
  await expect(clone.getByTestId('protected-cache-probe')).toHaveCount(0);
  expect(await refreshAttempts(request)).toBe(before);

  const peer = await context.newPage();
  await plan(request, { refresh: ['refresh-success'] });
  await freshProtected(peer, 'independent');
  await expect
    .poll(async () => (await state(peer)).header)
    .toBe('Bearer fictional-rotated-a');
  expect(
    await peer.evaluate(() => sessionStorage.getItem('daemon:local-signout')),
  ).toBeNull();
  await open(peer, true);
  await signIn(peer, request, 'b');
  for (const fenced of [page, clone]) {
    expect(
      await fenced.evaluate(() =>
        sessionStorage.getItem('daemon:local-signout'),
      ),
    ).toBe('1');
    expect((await state(fenced)).header).toBeNull();
  }

  await page.goto('/auth?cache-fixture&settled-auth-config&credential-owner');
  await expect
    .poll(() => page.evaluate(() => typeof window.__p02?.ownedSignIn))
    .toBe('function');
  await plan(request, { 'email-complete': ['success'] });
  expect(
    (await page.evaluate(() => window.__p02!.ownedSignIn!('b'))).success,
  ).toBe(true);
  await expect(page.getByTestId('credential-readiness')).toHaveText('ready');
  expect(
    await page.evaluate(() => sessionStorage.getItem('daemon:local-signout')),
  ).toBeNull();
  await plan(request, { refresh: ['refresh-success'] });
  await freshProtected(page, 'explicit-return');
  await expect
    .poll(async () => (await state(page)).header)
    .toBe('Bearer fictional-rotated-b');
  expect(
    await clone.evaluate(() => sessionStorage.getItem('daemon:local-signout')),
  ).toBe('1');
  await clone.close();
  await peer.close();
});

test('native sessionStorage persistence failure withholds exit navigation until verified retry, despite immediate cleanup', async ({
  page,
  request,
}) => {
  await page.addInitScript(() => {
    Reflect.set(window, '__blockExitWrite', true);
    const native = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (
        this === sessionStorage &&
        key === 'daemon:local-signout' &&
        Reflect.get(window, '__blockExitWrite')
      )
        throw new Error('fictional tab persistence failure');
      native.call(this, key, value);
    };
  });
  await open(page, true);
  await signIn(page, request, 'a');
  await page.evaluate(() =>
    history.replaceState(null, '', '/settings/p02-write-failure'),
  );
  expect((await request.post(`${API}/__fixture/fail/1`)).status()).toBe(204);
  await page.evaluate(() => window.__p02!.clearCache()); // ready lifetime: explicitly invalidate via sign-in next
  await plan(request, { 'email-complete': ['success'] });
  expect((await page.evaluate(() => window.__p02!.signIn('a'))).success).toBe(
    true,
  );
  await expect.poll(async () => (await state(page)).cacheStatus).toBe('failed');
  await page.getByTestId('auth-cache-gate-signout').click();
  await expect(
    page.getByText(/Reloading may restore this session/),
  ).toBeVisible();
  expect(new URL(page.url()).pathname).toBe('/settings/p02-write-failure');
  expect(await state(page)).toMatchObject({ token: null, header: null });
  await expect(page.getByTestId('protected-cache-probe')).toHaveCount(0);
  expect(
    await page.evaluate(() => sessionStorage.getItem('daemon:local-signout')),
  ).toBeNull();
  await page.evaluate(() => Reflect.set(window, '__blockExitWrite', false));
  await page.getByTestId('auth-cache-gate-retry').click();
  await page.waitForURL('/setup');
  expect(
    await page.evaluate(() => sessionStorage.getItem('daemon:local-signout')),
  ).toBe('1');
});

test('corrupt marker and native read failure block fresh-document restoration; failed verified removal is retryable through the public owner', async ({
  page,
  context,
  request,
}) => {
  await context.addCookies([
    {
      name: 'daemon_refresh',
      value: 'fictional-refresh-a',
      url: 'http://127.0.0.1:3142',
      httpOnly: true,
      sameSite: 'Strict',
    },
  ]);
  await page.addInitScript(() => {
    sessionStorage.setItem('daemon:local-signout', 'corrupt');
    Reflect.set(window, '__blockFenceRead', true);
    Reflect.set(window, '__blockFenceRemoval', true);
    const read = Storage.prototype.getItem;
    const remove = Storage.prototype.removeItem;
    Storage.prototype.getItem = function (key) {
      if (
        this === sessionStorage &&
        key === 'daemon:local-signout' &&
        Reflect.get(window, '__blockFenceRead')
      )
        throw new Error('fictional tab read failure');
      return read.call(this, key);
    };
    Storage.prototype.removeItem = function (key) {
      if (
        this === sessionStorage &&
        key === 'daemon:local-signout' &&
        Reflect.get(window, '__blockFenceRemoval')
      )
        throw new Error('fictional tab removal failure');
      remove.call(this, key);
    };
  });
  await freshProtected(page, 'read-failure');
  await expect.poll(async () => (await state(page)).cacheStatus).toBe('ready');
  expect(await state(page)).toMatchObject({ token: null, header: null });
  expect(await refreshAttempts(request)).toBe(0);
  await expect(page.getByTestId('protected-cache-probe')).toHaveCount(0);
  await page.goto('/auth?cache-fixture&settled-auth-config&credential-owner');
  await expect
    .poll(() => page.evaluate(() => typeof window.__p02?.ownedSignIn))
    .toBe('function');
  await page.evaluate(() => Reflect.set(window, '__blockFenceRead', false));
  await plan(request, { 'email-complete': ['success'] });
  const owned = page.evaluate(() => window.__p02!.ownedSignIn!('b'));
  await expect(page.getByText(/Validated sign-in is waiting/)).toBeVisible();
  expect((await state(page)).header).toBeNull();
  expect(
    await page.evaluate(() => sessionStorage.getItem('daemon:local-signout')),
  ).toBe('corrupt');
  await page.evaluate(() => Reflect.set(window, '__blockFenceRemoval', false));
  await page.getByTestId('auth-cache-gate-retry').click();
  expect((await owned).success).toBe(true);
  expect((await state(page)).header).toBe('Bearer fictional-owner-b');
  expect(
    await page.evaluate(() => sessionStorage.getItem('daemon:local-signout')),
  ).toBeNull();
  expect(await refreshAttempts(request)).toBe(0);
});

async function holdCookieLock(page: Page) {
  await page.goto('/__fixture/seed');
  await page.evaluate(() => {
    Reflect.set(window, '__held', false);
    void navigator.locks.request('daemon-auth-cookie', async () => {
      Reflect.set(window, '__held', true);
      await new Promise<void>((resolve) =>
        Reflect.set(window, '__releaseLock', resolve),
      );
    });
  });
  await expect
    .poll(() => page.evaluate(() => Reflect.get(window, '__held')))
    .toBe(true);
}
const pendingLocks = (page: Page) =>
  page.evaluate(
    async () =>
      (await navigator.locks.query()).pending?.filter(
        (lock) => lock.name === 'daemon-auth-cookie',
      ).length,
  );
const stableRecord = (epoch: string) =>
  JSON.stringify({ version: 1, epoch, status: 'stable' });

test('two genuinely unbound native fresh documents get serialized cookie refreshes and preserve both protected deep links', async ({
  page,
  context,
  request,
}) => {
  await context.addInitScript(() => {
    const native = window.fetch;
    Reflect.set(window, '__cacheAcknowledgements', []);
    window.fetch = async (input, init) => {
      const response = await native(input, init);
      if (typeof input === 'string' && input.endsWith('/auth/cache/clear'))
        Reflect.get(window, '__cacheAcknowledgements').push({
          url: input,
          status: response.status,
          cacheControl: response.headers.get('cache-control'),
        });
      return response;
    };
  });
  await context.addCookies([
    {
      name: 'daemon_refresh',
      value: 'fictional-refresh-a',
      url: 'http://127.0.0.1:3142',
      httpOnly: true,
      sameSite: 'Strict',
    },
  ]);
  await holdCookieLock(page);
  expect(
    await page.evaluate(() => localStorage.getItem('daemon:auth-coordination')),
  ).toBeNull();
  await plan(request, { refresh: ['refresh-success', 'refresh-success'] });
  const tabs = [await context.newPage(), await context.newPage()];
  await Promise.all(
    tabs.map((tab, index) => freshProtected(tab, `unbound-${index}`)),
  );
  await expect.poll(() => pendingLocks(page)).toBe(2);
  await page.evaluate(() => Reflect.get(window, '__releaseLock')());
  for (const [index, tab] of tabs.entries()) {
    await expect
      .poll(async () => (await state(tab)).header)
      .toBe('Bearer fictional-rotated-a');
    expect(new URL(tab.url()).pathname).toBe(`/settings/p02-unbound-${index}`);
    await expect(tab.getByTestId('protected-cache-probe')).toBeVisible();
    expect((await state(tab)).cacheStatus).toBe('ready');
    const acknowledgements = await tab.evaluate(() =>
      Reflect.get(window, '__cacheAcknowledgements'),
    );
    for (const url of [
      '/api/v1/auth/cache/clear',
      `${API}/daemon/v1/auth/cache/clear`,
    ]) {
      expect(acknowledgements).toContainEqual({
        url,
        status: 204,
        cacheControl: 'no-store',
      });
    }
  }
  expect(await refreshAttempts(request)).toBe(2);
  // Both successful refresh bodies terminate in writer order, not overlapping.
  const events = ((await upstream(request)).events as string[][]).filter(
    (event) => event[0] === 'refresh',
  );
  const starts = events.flatMap((event, index) =>
    event[2] === 'attempt-started' ? [index] : [],
  );
  expect(
    events
      .slice(starts[0] + 1, starts[1])
      .some((event) => event[2] === 'step-body'),
  ).toBe(true);
  for (const tab of tabs) await tab.close();
});

for (const status of ['signed-out', 'uncertain', 'pending'] as const) {
  test(`unbound queued startup never adopts ${status} replacement or loses its deep link`, async ({
    page,
    context,
    request,
  }) => {
    await holdCookieLock(page);
    const tab = await context.newPage();
    await freshProtected(tab, `ineligible-${status}`);
    await expect.poll(() => pendingLocks(page)).toBe(1);
    await page.evaluate((status) => {
      localStorage.setItem(
        'daemon:auth-coordination',
        JSON.stringify({
          version: 1,
          epoch: 'fictional-peer',
          status,
          ...(status === 'pending'
            ? { attempt: 'fictional-abandoned', kind: 'refresh' }
            : {}),
        }),
      );
      Reflect.get(window, '__releaseLock')();
    }, status);
    await expect
      .poll(async () => (await state(tab)).refreshPending)
      .toBe(false);
    expect((await state(tab)).header).toBeNull();
    expect(await refreshAttempts(request)).toBe(0);
    expect((await tab.evaluate(() => window.__p02!.refresh())).success).toBe(
      false,
    );
    expect(await refreshAttempts(request)).toBe(0);
    expect(new URL(tab.url()).pathname).toBe(
      `/settings/p02-ineligible-${status}`,
    );
    await expect(
      tab.getByRole('button', { name: 'Sign in', exact: true }),
    ).toBeVisible();
    await expect(tab.getByTestId('protected-cache-probe')).toHaveCount(0);
    await tab.close();
  });
}

test('a second stable epoch change exhausts the one unbound retry under a fresh native lock and preserves explicit recovery', async ({
  page,
  context,
  request,
}) => {
  await holdCookieLock(page);
  const tab = await context.newPage();
  await freshProtected(tab, 'second-epoch');
  await expect.poll(() => pendingLocks(page)).toBe(1);
  // A separate queued native holder allows inspection of the genuine second
  // request before changing its epoch. No module/generation reset or fake lock.
  await page.evaluate((record) => {
    localStorage.setItem('daemon:auth-coordination', record);
    Reflect.set(window, '__heldAgain', false);
    void navigator.locks.request('daemon-auth-cookie', async () => {
      Reflect.set(window, '__heldAgain', true);
      await new Promise<void>((resolve) =>
        Reflect.set(window, '__releaseAgain', resolve),
      );
    });
    Reflect.get(window, '__releaseLock')();
  }, stableRecord('fictional-first'));
  await expect
    .poll(() => page.evaluate(() => Reflect.get(window, '__heldAgain')))
    .toBe(true);
  await expect.poll(() => pendingLocks(page)).toBe(1);
  await page.evaluate((record) => {
    localStorage.setItem('daemon:auth-coordination', record);
    Reflect.get(window, '__releaseAgain')();
  }, stableRecord('fictional-second'));
  await expect.poll(async () => (await state(tab)).refreshPending).toBe(false);
  expect(await refreshAttempts(request)).toBe(0);
  expect(new URL(tab.url()).pathname).toBe('/settings/p02-second-epoch');
  expect((await state(tab)).header).toBeNull();
  await expect(
    tab.getByRole('button', { name: 'Sign in', exact: true }),
  ).toBeVisible();
  expect(await pendingLocks(page)).toBe(0);
  await plan(request, { 'email-complete': ['reject'] });
  expect((await tab.evaluate(() => window.__p02!.reject())).success).toBe(
    false,
  );
  expect(await tab.evaluate(() => window.__p02!.refresh())).toMatchObject({
    success: false,
    preserveProtectedUrl: true,
  });
  expect(await refreshAttempts(request)).toBe(0);
  await tab.close();
});

test('the fresh native retry expires at the ORIGINAL startup deadline while queued behind another holder', async ({
  page,
  context,
  request,
}) => {
  await holdCookieLock(page);
  const tab = await context.newPage();
  await freshProtected(tab, 'original-deadline');
  await expect.poll(() => pendingLocks(page)).toBe(1);
  const queuedAt = Date.now();
  const completion = tab.evaluate(() => window.__p02!.refresh());
  // Consume most of the first owner's real budget before its epoch race. A
  // renewed +10s retry would visibly overrun the ORIGINAL deadline below.
  await page.evaluate(
    () => new Promise<void>((resolve) => setTimeout(resolve, 6000)),
  );
  await page.evaluate((record) => {
    localStorage.setItem('daemon:auth-coordination', record);
    Reflect.set(window, '__heldAgain', false);
    void navigator.locks.request('daemon-auth-cookie', async () => {
      Reflect.set(window, '__heldAgain', true);
      await new Promise<void>((resolve) =>
        Reflect.set(window, '__releaseAgain', resolve),
      );
    });
    Reflect.get(window, '__releaseLock')();
  }, stableRecord('fictional-first'));
  await expect
    .poll(() => page.evaluate(() => Reflect.get(window, '__heldAgain')))
    .toBe(true);
  await expect.poll(() => pendingLocks(page)).toBe(1);
  expect(await completion).toMatchObject({
    success: false,
    error: 'Session operation timed out',
    preserveProtectedUrl: true,
  });
  expect(Date.now() - queuedAt).toBeLessThan(11_000);
  expect(await pendingLocks(page)).toBe(0);
  expect(await refreshAttempts(request)).toBe(0);
  expect(new URL(tab.url()).pathname).toBe('/settings/p02-original-deadline');
  expect((await state(tab)).header).toBeNull();
  await expect(
    tab.getByRole('button', { name: 'Sign in', exact: true }),
  ).toBeVisible();
  await page.evaluate(() => Reflect.get(window, '__releaseAgain')());
  await tab.close();
});

test('explicit credential supersession cancels genuinely unbound queued startup before it can adopt a peer epoch', async ({
  page,
  context,
  request,
}) => {
  await holdCookieLock(page);
  const tab = await context.newPage();
  await freshProtected(tab, 'supersession');
  await expect.poll(() => pendingLocks(page)).toBe(1);
  const startup = tab.evaluate(() => window.__p02!.refresh());
  const identity = tab.evaluate(() => window.__p02!.signIn('b'));
  expect(await startup).toMatchObject({
    success: false,
    error: 'Authentication changed',
  });
  await expect.poll(() => pendingLocks(page)).toBe(1); // credentials only, no surviving startup owner
  await page.evaluate((record) => {
    localStorage.setItem('daemon:auth-coordination', record);
    Reflect.get(window, '__releaseLock')();
  }, stableRecord('fictional-peer'));
  expect((await identity).success).toBe(false); // explicit credentials cannot silently rebase either
  expect(await refreshAttempts(request)).toBe(0);
  expect((await upstream(request)).events).toEqual([]);
  expect(new URL(tab.url()).pathname).toBe('/settings/p02-supersession');
  expect((await state(tab)).header).toBeNull();
  await tab.close();
});
