import { expect } from '@playwright/test';
import { conversation, mockApi, test } from './discoverability-fixture';

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test('the development fixture keeps uncaught runtime errors observable', async ({
  page,
  runtimeErrors,
}, testInfo) => {
  await page.setViewportSize({ width: 375, height: 1100 });
  await page.goto(`/settings/profile?from=${conversation.id}`);
  await expect(page.getByRole('link', { name: '← Chat' })).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Open Next.js Dev Tools', exact: true }),
  ).toHaveCount(0);

  const failure = page.waitForEvent('pageerror');
  await page.evaluate(() => {
    setTimeout(() => {
      throw new Error('J490 synthetic runtime observability');
    }, 0);
  });
  expect((await failure).message).toBe('J490 synthetic runtime observability');
  const issues = page.getByRole('button', {
    name: 'Open issues overlay',
    exact: true,
  });
  await expect(issues).toBeVisible();
  await issues.click();
  const dialog = page.locator('[data-nextjs-dialog]');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('J490 synthetic runtime observability');
  await page.screenshot({
    path: testInfo.outputPath('runtime-error-visible-375.png'),
    fullPage: true,
  });
  // Consume only this explicitly injected error. Every other pageerror observed
  // during the test still fails the automatic fixture.
  expect(runtimeErrors).toHaveLength(1);
  expect(runtimeErrors.shift()?.message).toBe(
    'J490 synthetic runtime observability',
  );
});
