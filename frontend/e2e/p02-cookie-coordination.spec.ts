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
