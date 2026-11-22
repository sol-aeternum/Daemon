import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { expect, test, type Page } from '@playwright/test';

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
  // response would mean the frame reached the network.
  const remote: string[] = [];
  const blocked: string[] = [];
  page.on('response', (response) => {
    if (response.url().startsWith('https://example.com/')) {
      remote.push(response.url());
    }
  });
  page.on('requestfailed', (request) => {
    if (request.url().startsWith('https://example.com/')) {
      blocked.push(request.failure()?.errorText ?? '');
    }
  });
  const frame = page.locator('iframe[title="Sized HTML"]');
  await expect
    .poll(async () => (await frame.boundingBox())?.height ?? 0)
    .toBeGreaterThan(690);
  const content = page
    .frameLocator('iframe[title="Sized HTML"]')
    .frameLocator('#daemon-html-preview-content');
  await expect(content.locator('body')).toHaveAttribute(
    'data-remote',
    'blocked',
  );
  expect(remote).toEqual([]);
  expect(blocked).toHaveLength(1);
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
