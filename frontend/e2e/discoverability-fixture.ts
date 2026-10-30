import { expect, type Page } from '@playwright/test';
import { mockCacheClear, test as base } from './online-ui-fixture';
import {
  historyFixtureConversationId,
  historyFixtureList,
  historyFixtureDetail,
} from './history-fixtures';

export const test = base.extend<{ runtimeErrors: Error[] }>({
  runtimeErrors: [
    async ({ page }, use, testInfo) => {
      const errors: Error[] = [];
      const consoleMessages: string[] = [];
      const onError = (error: Error) => errors.push(error);
      page.on('pageerror', onError);
      page.on('console', (message) => {
        if (['error', 'warning'].includes(message.type())) {
          consoleMessages.push(message.text());
        }
      });
      await use(errors);
      await testInfo.attach('browser-console.json', {
        body: JSON.stringify(consoleMessages, null, 2),
        contentType: 'application/json',
      });
      page.off('pageerror', onError);
      expect(errors.map((error) => error.message)).toEqual([]);
    },
    { auto: true },
  ],
});

export const conversation = {
  id: historyFixtureConversationId,
  title: 'Planning',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  status: 'active',
  pinned: false,
  title_locked: false,
  metadata: {},
  messages: [],
};

export async function mockApi(page: Page) {
  await mockCacheClear(page);
  await page.route('**/api/v1/auth/config', (route) =>
    route.fulfill({
      json: {
        mode: 'self_hosted',
        email: { enabled: false },
        google: { enabled: false },
      },
    }),
  );
  await page.route('**/api/v1/auth/refresh', (route) =>
    route.fulfill({
      json: { access_token: 'browser-test-token', expires_in: 3600 },
    }),
  );
  await page.route('**/conversations*', (route) =>
    route.fulfill({ json: historyFixtureList([conversation]) }),
  );
  await page.route(
    (url) => url.pathname.endsWith(`/conversations/${conversation.id}`),
    (route) =>
      route.fulfill({
        json: historyFixtureDetail(
          conversation,
          new URL(route.request().url()),
        ),
      }),
  );
  await page.route('**/users/me/settings', (route) =>
    route.fulfill({ json: {} }),
  );
  await page.route('**/v1/catalog', (route) =>
    route.fulfill({
      json: {
        auto: {
          id: 'auto',
          name: 'Auto',
          tagline: 'Automatic routing',
          icon: 'zap',
        },
        featured: [],
      },
    }),
  );
}
