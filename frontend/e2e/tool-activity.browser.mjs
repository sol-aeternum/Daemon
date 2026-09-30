// In an isolated frontend source copy, create app/tool-activity-fixture/page.tsx
// that re-exports ../..\/e2e/tool-activity-app/app/page, then run Next normally.
// Use a minimal test-only root layout importing app/globals.css in that copy,
// so auth providers never mount. The fixture is never a production route. Run:
// node e2e/tool-activity.browser.mjs BASE_URL EXECUTABLE_PATH OUTPUT_DIRECTORY
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';

const [baseURL, executablePath, outputDirectory] = process.argv.slice(2);
assert(
  baseURL && executablePath && outputDirectory,
  'Three arguments required',
);
await mkdir(outputDirectory, { recursive: true });
const browser = await chromium.launch({ executablePath, headless: true });
try {
  console.log(`Browser executable reports: ${browser.version()}`);
  for (const [label, viewport] of [
    ['desktop', { width: 1280, height: 900 }],
    ['mobile', { width: 375, height: 812 }],
  ]) {
    const page = await browser.newPage({ viewport });
    const externalRequests = [];
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    await page.route('**/*', (route) => {
      if (new URL(route.request().url()).origin !== new URL(baseURL).origin) {
        externalRequests.push(route.request().url());
        return route.abort();
      }
      return route.continue();
    });
    await page.goto(baseURL, { waitUntil: 'networkidle' });
    const group = page.getByRole('button', { name: /^Tool activity:/ });
    assert.equal(await group.count(), 1);
    assert.equal(await group.getAttribute('aria-expanded'), 'false');
    assert.match(await group.innerText(), /Searched 3 times · Read 2 pages/);
    assert.match(await group.innerText(), /Working… \(1\) · 1 issue/);
    assert.equal(
      await page
        .getByRole('button', { name: 'web_search', exact: true })
        .count(),
      0,
    );
    const response = page.getByRole('region', { name: 'Assistant response' });
    assert.equal(await response.getByRole('link').count(), 5); // three source previews + two answer links
    assert.equal(
      await page.getByRole('link', { name: /failed.example/ }).count(),
      0,
    );
    await group.focus();
    await page.keyboard.press('Enter');
    // Wait for React's event handler, not merely server-rendered markup. In
    // particular, a screenshot's caret hiding must not race hydration.
    await page.waitForFunction(
      () =>
        document
          .querySelector('button[aria-label^="Tool activity:"]')
          ?.getAttribute('aria-expanded') === 'true',
    );
    assert.equal(await group.getAttribute('aria-expanded'), 'true');
    await group.focus();
    await page.keyboard.press('Space');
    assert.equal(await group.getAttribute('aria-expanded'), 'false');
    // Hide the development-only badge using an allowed style attribute,
    // without injecting a stylesheet or changing any production component.
    await page.locator('nextjs-portal').evaluateAll((portals) =>
      portals.forEach((portal) => {
        portal.style.display = 'none';
      }),
    );
    await page.screenshot({
      path: `${outputDirectory}/${label}-collapsed.png`,
      fullPage: true,
    });
    await page.keyboard.press('Enter');
    const pending = page.getByRole('button', {
      name: 'Running get_time...',
      exact: true,
    });
    await pending.focus();
    await page.keyboard.press('Enter');
    assert.equal(await pending.getAttribute('aria-expanded'), 'true');
    await page
      .getByRole('button', { name: 'Finish pending action', exact: true })
      .evaluate((button) => button.click());
    const completed = page.getByRole('button', {
      name: 'get_time',
      exact: true,
    });
    await completed.waitFor();
    assert.equal(await completed.getAttribute('aria-expanded'), 'true');
    assert(
      await completed.evaluate((button) => document.activeElement === button),
    );
    assert.equal(await group.getAttribute('aria-expanded'), 'true');
    assert.equal(await page.getByText(/12:00 synthetic/).count(), 1);
    assert.equal(
      await page
        .getByRole('button', { name: 'web_search', exact: true })
        .count(),
      3,
    );
    assert.equal(
      await page
        .getByRole('button', { name: 'spawn_agent', exact: true })
        .count(),
      1,
    );
    await page
      .getByRole('button', { name: '+2 more sources', exact: true })
      .click();
    assert.equal(await response.getByRole('link').count(), 7);
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    );
    await page.screenshot({
      path: `${outputDirectory}/${label}-expanded.png`,
      fullPage: true,
    });
    await page.getByRole('checkbox', { name: 'Hide tool calls' }).check();
    assert.equal(await group.count(), 0);
    const citation = page.getByRole('link', {
      name: /source1.example.*first source/,
    });
    assert.equal(
      await citation.getAttribute('href'),
      'https://source1.example/guide#details',
    );
    assert.match(await citation.getAttribute('class'), /rounded-full/);
    assert.deepEqual(
      externalRequests,
      [],
      'No source, favicon, auth or provider requests',
    );
    assert.deepEqual(errors, [], 'No uncaught browser errors');
    console.log(
      `${label}: compact summary, all actions, Enter/Space, focus, streaming stability, source disclosure, hidden-tools citations, no overflow/network — passed`,
    );
    await page.close();
  }
} finally {
  await browser.close();
}
