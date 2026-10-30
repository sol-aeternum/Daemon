import { expect } from '@playwright/test';
import { conversation, mockApi, test } from './discoverability-fixture';
import { mockCacheClear } from './online-ui-fixture';

test('non-PWA UI uses a real cache acknowledgement without a worker controller', async ({
  page,
  context,
}) => {
  await mockApi(page);
  await mockCacheClear(page);
  await page.goto(`/?id=${conversation.id}`);
  await expect(page.locator('textarea')).toBeVisible();
  expect(await page.evaluate(() => 'serviceWorker' in navigator)).toBe(false);
  expect(context.serviceWorkers()).toHaveLength(0);
});

test('missing cache acknowledgement still blocks protected UI and generation', async ({
  page,
}) => {
  await mockApi(page);
  let generations = 0;
  await page.route('**/api/chat', async (route) => {
    generations += 1;
    await route.abort();
  });
  await page.route('**/api/v1/auth/cache/clear', (route) =>
    route.fulfill({ status: 404 }),
  );
  await page.goto(`/?id=${conversation.id}`);
  await expect(
    page.getByText(
      'The browser cache clear could not be acknowledged. Protected content stays unavailable until it succeeds.',
    ),
  ).toBeVisible();
  await expect(page.locator('textarea')).toHaveCount(0);
  expect(generations).toBe(0);
});
