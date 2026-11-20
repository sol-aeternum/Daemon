import { expect, test } from '@playwright/test';
// Same locked test bundler as e2e/cache-fixture-server.mjs; no new dependency.
import { build } from 'esbuild';
import { execFileSync } from 'node:child_process';

const origin = 'http://openui487.invalid';
let bundle: string;
let css: string;

test.beforeAll(async () => {
  const result = await build({
    entryPoints: ['e2e/openui487-harness.tsx'],
    jsx: 'automatic',
    bundle: true,
    write: false,
    platform: 'browser',
    define: {
      'process.env.NODE_ENV': '"production"',
      'process.env': '{}',
    },
  });
  bundle = result.outputFiles[0].text;
  css = execFileSync(
    'node_modules/.bin/tailwindcss',
    [
      '-i',
      'app/globals.css',
      '--content',
      'e2e/openui487-harness.tsx,__tests__/fixtures/openui487/*.tsx,components/ResourceCards.tsx,src/components/MarkdownRenderer.tsx',
    ],
    { encoding: 'utf8' },
  );
});

test('inline results stay readable and keyboard reachable as the layout settles', async ({
  page,
}, testInfo) => {
  const unexpected: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  await page.route('**/*', (route) => {
    const url = route.request().url();
    if (url === `${origin}/`) {
      return route.fulfill({
        contentType: 'text/html',
        body: '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Offline OpenUI #487</title><link rel="icon" href="data:,"><link rel="stylesheet" href="/fixture.css"><main id="root"></main><script defer src="/fixture.js"></script></html>',
      });
    }
    if (url === `${origin}/fixture.js`) {
      return route.fulfill({ contentType: 'text/javascript', body: bundle });
    }
    if (url === `${origin}/fixture.css`) {
      return route.fulfill({ contentType: 'text/css', body: css });
    }
    unexpected.push(`${route.request().method()} ${url}`);
    return route.abort();
  });

  // DOM commit timing only: no model, real chat transport, or paint claim.
  await page.addInitScript(() => {
    const observer = new MutationObserver(() => {
      const pilot = document.querySelector('[data-layout-active]');
      const active = pilot?.getAttribute('data-layout-active');
      if (
        active === 'false' &&
        !performance.getEntriesByName('native-ready').length
      ) {
        performance.mark('native-ready');
      }
      if (
        active === 'true' &&
        !performance.getEntriesByName('layout-ready').length
      ) {
        performance.mark('layout-ready');
        observer.disconnect();
      }
    });
    observer.observe(document, {
      subtree: true,
      childList: true,
      attributes: true,
    });
  });

  await page.goto(origin);
  expect(errors).toEqual([]);
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.locator('figure svg')).toHaveCount(0);
  const pilot = page.getByRole('region', { name: 'Synthetic OpenUI pilot' });
  await expect(pilot).toHaveAttribute('data-layout-active', 'false');
  await expect(pilot).toHaveAttribute(
    'data-layout-order',
    'TaskStatus>ArtifactResults>Figures',
  );
  await expect(pilot.getByTestId('openui487-connection')).toHaveAttribute(
    'data-connection',
    'reconnecting',
  );
  await expect(page.getByRole('textbox', { name: 'Message' })).toBeEditable();
  await page.getByRole('textbox', { name: 'Message' }).fill('Offline draft');
  const preview = page
    .getByRole('button', { name: 'Preview', exact: true })
    .first();
  await preview.focus();
  // Simulate stream arrival without moving the user's keyboard focus.
  await page
    .getByRole('button', { name: 'Finish fixture stream' })
    .evaluate((button: HTMLButtonElement) => button.click());
  await expect(page.locator('figure')).toBeVisible();
  await expect(page.locator('figure svg[aria-hidden="true"]')).toBeVisible();
  await expect(pilot).toHaveAttribute('data-layout-active', 'true');
  await expect(pilot).toHaveAttribute(
    'data-layout-order',
    'Figures>TaskStatus>ArtifactResults',
  );
  await expect(page.getByRole('table')).toBeVisible();
  await expect(page.getByRole('table')).toHaveAccessibleName(
    'Synthetic weekday deliveries values supplied by the app fixture.',
  );
  await expect(
    page.getByRole('button', { name: 'Preview', exact: true }),
  ).toHaveCount(2);
  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveValue(
    'Offline draft',
  );

  await expect(preview).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(
    page.getByRole('button', { name: 'Download', exact: true }).first(),
  ).toBeFocused();
  expect(
    await page.evaluate(() => {
      const table = document.querySelector('table');
      const bounds = table?.getBoundingClientRect();
      const container = table?.parentElement;
      return Boolean(
        bounds &&
        container &&
        container.scrollWidth <= container.clientWidth &&
        bounds.left >= 0 &&
        bounds.right <= window.innerWidth &&
        document.documentElement.scrollWidth <= window.innerWidth,
      );
    }),
  ).toBe(true);
  expect(unexpected).toEqual([]);
  expect(errors).toEqual([]);
  const timing = await page.evaluate(() =>
    performance
      .getEntriesByType('mark')
      .filter((mark) =>
        ['native-ready', 'fixture-final-click', 'layout-ready'].includes(
          mark.name,
        ),
      )
      .map(({ name, startTime }) => ({ name, startTimeMs: startTime })),
  );
  expect(timing.map(({ name }) => name)).toEqual([
    'native-ready',
    'fixture-final-click',
    'layout-ready',
  ]);
  await testInfo.attach('fixture-dom-timing', {
    contentType: 'application/json',
    body: JSON.stringify(timing),
  });
});
