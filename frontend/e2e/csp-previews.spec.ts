import { createSocket, type Socket } from 'node:dgram';
import { readFileSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import {
  createServer as createNetServer,
  type AddressInfo,
  type Server as NetServer,
} from 'node:net';
import {
  chromium,
  expect,
  test,
  type Page,
  type TestInfo,
} from '@playwright/test';

interface CspViolation {
  blockedURI: string;
  effectiveDirective: string;
  violatedDirective: string;
}

function directive(policy: string, name: string): string[] {
  const value = policy
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${name} `));
  return value?.split(/\s+/).slice(1) ?? [];
}

async function topLevelViolations(page: Page) {
  return page.evaluate(
    () =>
      (
        window as Window & {
          __cspViolations?: CspViolation[];
        }
      ).__cspViolations ?? [],
  );
}

test.beforeEach(async ({ page }) => {
  await page.goto('/');
});

test('DOCX preview attaches generated styles with the response nonce', async ({
  page,
}) => {
  const title = page.getByText('CSP DOCX Preview');
  await expect(title).toBeVisible();

  const nonce = await page
    .locator('meta[name="csp-nonce"]')
    .getAttribute('content');
  expect(nonce).toBeTruthy();

  const styles = page.locator(
    '[data-testid="docx-preview"] .docx-preview-container style',
  );
  await expect.poll(() => styles.count()).toBeGreaterThan(1);
  const styleNonces = await styles.evaluateAll((nodes) =>
    nodes.map((node) => (node as HTMLStyleElement).nonce),
  );
  expect(styleNonces.every((value) => value === nonce)).toBe(true);
  await expect(title).toHaveCSS('color', 'rgb(192, 0, 0)');
});

test('HTML preview runs inline scripts only inside the isolated frame policy', async ({
  page,
}) => {
  const response = await page.request.get('/api/previews/html');
  const framePolicy = response.headers()['content-security-policy'] ?? '';
  expect(response.headers()['x-frame-options']).toBe('SAMEORIGIN');
  expect(directive(framePolicy, 'script-src')).toContain("'unsafe-inline'");
  expect(directive(framePolicy, 'frame-ancestors')).toContain("'self'");
  // Generated content may run script, so the frame has no network egress.
  for (const name of ['img-src', 'font-src', 'media-src']) {
    expect(directive(framePolicy, name)).not.toContain('https:');
  }
  expect(directive(framePolicy, 'connect-src')).toEqual([]);
  expect(directive(framePolicy, 'default-src')).toEqual(["'none'"]);

  const outerFrame = page.frameLocator('iframe[title="Interactive HTML"]');
  const previewFrame = outerFrame.frameLocator('#daemon-html-preview-content');
  const runButton = previewFrame.getByRole('button', { name: 'Run script' });
  await expect(runButton).toBeVisible();
  await expect(runButton).toHaveCSS('color', 'rgb(0, 128, 0)');
  await runButton.click();
  await expect(previewFrame.locator('#script-result')).toHaveText('ran');
});

test('application media-src admits supported data video URLs', async ({
  page,
}) => {
  const response = await page.goto('/');
  const policy = response?.headers()['content-security-policy'] ?? '';
  expect(directive(policy, 'media-src')).toContain('data:');
  expect(directive(policy, 'style-src')).not.toContain("'unsafe-inline'");

  const video = page.locator('[data-testid="video-preview"] video');
  await expect(video).toHaveAttribute('src', /^data:video\/webm;base64,/);
  await expect
    .poll(() =>
      video.evaluate((element) => (element as HTMLVideoElement).readyState),
    )
    .toBeGreaterThan(0);

  const violations = await topLevelViolations(page);
  expect(
    violations.filter((event) => event.effectiveDirective === 'media-src'),
  ).toEqual([]);
});

test('sandboxed blocks size to content and cannot reach the network', async ({
  page,
}) => {
  // A CSP-blocked load still surfaces as a failed request event; only a
  // response would mean the frame reached the network. The shared beforeEach
  // has already loaded `page`, whose frame may try the remote image before
  // listeners attach, so observe a fresh page from before its navigation.
  const probe = await page.context().newPage();
  const remote: string[] = [];
  const blocked: string[] = [];
  probe.on('response', (response) => {
    if (response.url().startsWith('https://example.com/')) {
      remote.push(response.url());
    }
  });
  probe.on('requestfailed', (request) => {
    if (request.url().startsWith('https://example.com/')) {
      blocked.push(request.failure()?.errorText ?? '');
    }
  });
  await probe.goto('/');
  const frame = probe.locator('iframe[title="Sized HTML"]');
  await expect
    .poll(async () => (await frame.boundingBox())?.height ?? 0)
    .toBeGreaterThan(690);
  const content = probe
    .frameLocator('iframe[title="Sized HTML"]')
    .frameLocator('#daemon-html-preview-content');
  await expect(content.locator('body')).toHaveAttribute(
    'data-remote',
    'blocked',
  );
  await expect.poll(() => blocked.length).toBe(1);
  expect(remote).toEqual([]);
  await probe.close();
});

test.describe('generated content egress', () => {
  let server: Server;
  let port = 0;
  const hits: string[] = [];

  test.beforeAll(async () => {
    server = createServer((request, response) => {
      hits.push(`${request.method} ${request.url}`);
      response.writeHead(200, { 'Access-Control-Allow-Origin': '*' });
      response.end('reached');
    });
    server.on('upgrade', (request, socket) => {
      hits.push(`UPGRADE ${request.url}`);
      socket.destroy();
    });
    await new Promise<void>((resolve) =>
      server.listen(0, '127.0.0.1', resolve),
    );
    port = (server.address() as AddressInfo).port;
  });

  test.afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  test.beforeEach(() => {
    hits.length = 0;
  });

  test('frame policy denies frame navigation and every governed fetch', async ({
    page,
  }) => {
    const response = await page.request.get('/api/previews/html');
    const framePolicy = response.headers()['content-security-policy'] ?? '';
    expect(directive(framePolicy, 'frame-src')).toEqual(["'none'"]);
    expect(directive(framePolicy, 'form-action')).toEqual(["'none'"]);
    expect(response.headers()['x-dns-prefetch-control']).toBe('off');
  });

  test('every request route from generated content stays inside the browser', async ({
    page,
  }) => {
    await page.goto(`/?probe=${port}`);
    const probe = page
      .frameLocator('iframe[title="Egress probe"]')
      .frameLocator('#daemon-html-preview-content');
    const body = probe.locator('body');
    await expect(body).toHaveAttribute('data-done', '1');
    await expect(body).toHaveAttribute('data-webrtc', 'unavailable');
    await expect(body).toHaveAttribute('data-fetch', 'blocked');
    await expect(body).toHaveAttribute('data-xhr', 'blocked');
    await expect(body).toHaveAttribute('data-image', 'blocked');
    await expect(body).toHaveAttribute('data-popup', /blocked|threw/);
    await expect(body).toHaveAttribute('data-top', 'threw');
    // Give queued work (beacon, prefetch, form, worker, CSS) time to fire.
    await page.waitForTimeout(1500);
    expect(hits).toEqual([]);
  });

  for (const [title, how] of [
    ['Navigation probe', 'location'],
    ['Refresh probe', 'refresh'],
  ] as const) {
    test(`content cannot navigate its frame out (${how})`, async ({ page }) => {
      await page.goto(`/?probe=${port}`);
      const content = page
        .frameLocator(`iframe[title="${title}"]`)
        .frameLocator('#daemon-html-preview-content');
      await expect(
        content.getByText(
          'This page tried to leave its sandbox and was stopped.',
        ),
      ).toBeVisible();
      await page.waitForTimeout(500);
      expect(hits).toEqual([]);
    });
  }
});

// D6 residual egress. Routes CSP does not govern, probed in the browser
// configuration users actually run. Open residuals are pinned with
// `test.fail`: the assertion states the qualified behaviour, so a mitigation
// that closes one shows up as an unexpected pass. Each listener has a
// positive control so a dead detector cannot read as "closed".
test.describe('residual egress outside CSP (D6)', () => {
  test.skip(
    ({ browserName }) => browserName !== 'chromium',
    'Probes use Chromium net logs and ICE gathering; other engines are not qualified yet.',
  );

  test.describe('WebRTC', () => {
    let stun: Socket;
    let port = 0;
    const packets: string[] = [];

    test.beforeAll(async () => {
      stun = createSocket('udp4');
      stun.on('message', (message, from) => {
        packets.push(`${from.address}:${from.port} ${message.length}B`);
      });
      await new Promise<void>((resolve) => stun.bind(0, '127.0.0.1', resolve));
      port = (stun.address() as AddressInfo).port;
    });

    test.afterAll(async () => {
      await new Promise<void>((resolve) => stun.close(() => resolve()));
    });

    test.beforeEach(() => {
      packets.length = 0;
    });

    async function runRealm(page: Page, realm: string) {
      await page.goto(`/?stun=${port}&realm=${realm}`);
      const body = page
        .frameLocator('iframe[title="WebRTC probe"]')
        .frameLocator('#daemon-html-preview-content')
        .locator('body');
      await expect(body).toHaveAttribute('data-started', '1');
      return body;
    }

    test('control: the listener sees ICE gathering from an unguarded page', async ({
      page,
    }) => {
      await page.goto('/');
      await page.evaluate(async (stunPort) => {
        const connection = new RTCPeerConnection({
          iceServers: [{ urls: `stun:127.0.0.1:${stunPort}` }],
        });
        connection.createDataChannel('control');
        await connection.setLocalDescription(await connection.createOffer());
      }, port);
      await expect.poll(() => packets.length).toBeGreaterThan(0);
    });

    test('the content window has no peer connection constructor', async ({
      page,
    }) => {
      const body = await runRealm(page, 'top');
      await expect(body).toHaveAttribute('data-webrtc', 'unavailable');
      await page.waitForTimeout(2000);
      expect(packets).toEqual([]);
    });

    test('content cannot reach into a nested about:blank realm', async ({
      page,
    }) => {
      // Both frame layers omit allow-same-origin, and the inherited
      // sandboxed-origin flag gives every nested document a fresh opaque
      // origin, so the content cannot borrow an unguarded constructor.
      const body = await runRealm(page, 'blank');
      await expect(body).toHaveAttribute('data-webrtc', 'SecurityError');
      await page.waitForTimeout(2000);
      expect(packets).toEqual([]);
    });

    test('a nested srcdoc realm cannot gather ICE', async ({ page }) => {
      // Open residual: a nested srcdoc document runs its own script in a
      // realm the guard never reaches, and Chromium ignores CSP `webrtc`.
      test.fail(true, 'D6 residual: nested srcdoc realm reaches STUN');
      await runRealm(page, 'srcdoc');
      await page.waitForTimeout(3000);
      expect(packets).toEqual([]);
    });
  });

  test.describe('resource hints', () => {
    // Chromium only acts on hints in the full browser with an on-the-record
    // profile; the headless shell and incognito-style contexts drop them,
    // which is why the default page fixture cannot see this route.
    let server: NetServer;
    let port = 0;
    let sockets = 0;
    const requests: string[] = [];

    test.beforeAll(async () => {
      server = createNetServer((socket) => {
        sockets += 1;
        socket.once('data', (data) => {
          requests.push(data.toString().split('\r\n')[0] ?? '');
          socket.destroy();
        });
        socket.setTimeout(1000, () => socket.destroy());
      });
      await new Promise<void>((resolve) =>
        server.listen(0, '127.0.0.1', resolve),
      );
      port = (server.address() as AddressInfo).port;
    });

    test.afterAll(async () => {
      await new Promise<void>((resolve) => server.close(() => resolve()));
    });

    test.beforeEach(() => {
      sockets = 0;
      requests.length = 0;
    });

    async function resolvedHosts(
      testInfo: TestInfo,
      tag: string,
      visit: (page: Page) => Promise<void>,
    ): Promise<string[]> {
      const netLog = testInfo.outputPath('netlog.json');
      const context = await chromium.launchPersistentContext(
        testInfo.outputPath('profile'),
        { channel: 'chromium', args: [`--log-net-log=${netLog}`] },
      );
      try {
        const page = context.pages()[0] ?? (await context.newPage());
        await visit(page);
        await page.waitForTimeout(4000);
      } finally {
        await context.close();
      }
      const log = JSON.parse(readFileSync(netLog, 'utf8')) as {
        constants: { logEventTypes: Record<string, number> };
        events: { type: number; params?: unknown }[];
      };
      const job = log.constants.logEventTypes.HOST_RESOLVER_MANAGER_JOB;
      const hosts = new Set<string>();
      for (const event of log.events) {
        if (event.type !== job) continue;
        for (const match of JSON.stringify(event.params ?? {}).matchAll(
          /[a-z-]+-[a-z0-9]+\.test/g,
        )) {
          if (match[0].includes(tag)) hosts.add(match[0]);
        }
      }
      return [...hosts].sort();
    }

    test('control: the net log records hint lookups from an unguarded page', async ({}, testInfo) => {
      const tag = `control${Date.now()}`;
      const hosts = await resolvedHosts(testInfo, tag, async (page) => {
        await page.goto('about:blank');
        await page.setContent(
          `<link rel="preconnect" href="http://hint-${tag}.test">`,
        );
      });
      expect(hosts).toEqual([`hint-${tag}.test`]);
    });

    test('hints from generated content trigger no lookup, connection or request', async ({}, testInfo) => {
      // Open residual: dns-prefetch, preconnect and prerender are acted on
      // outside CSP, and X-DNS-Prefetch-Control does not stop them.
      test.fail(true, 'D6 residual: resource hints leave the frame');
      const tag = `frame${Date.now()}`;
      const hosts = await resolvedHosts(testInfo, tag, async (page) => {
        await page.goto(`/?hints=${port}&tag=${tag}`);
        await expect(
          page
            .frameLocator('iframe[title="Hint probe"]')
            .frameLocator('#daemon-html-preview-content')
            .locator('body'),
        ).toHaveAttribute('data-done', '1');
      });
      expect({ hosts, sockets, requests }).toEqual({
        hosts: [],
        sockets: 0,
        requests: [],
      });
    });
  });
});
