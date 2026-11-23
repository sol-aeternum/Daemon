import type { Page } from '@playwright/test';
import {
  historyFixtureConversationId,
  historyFixtureDetail,
  historyFixtureList,
} from './history-fixtures';
import { mockCacheClear } from './online-ui-fixture';

// Shared by the ergonomics benchmark and the manual #765 rail capture. Keep the
// loaded fixture byte-identical so both exercises measure the same DOM.
export const conversation = {
  id: historyFixtureConversationId,
  title: 'Planning',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  status: 'active',
  pinned: false,
  title_locked: false,
  metadata: {},
  messages: [
    {
      id: 'system-context',
      role: 'system',
      content: 'Internal context must stay hidden.',
    },
    ...Array.from({ length: 100 }, (_, index) => ({
      id: `message-${index + 1}`,
      role: index % 2 === 0 ? 'user' : 'assistant',
      content:
        index % 10 === 3
          ? `Message ${index + 1} short note.`
          : `Message ${index + 1} preview.\n\n${'Detailed discussion with useful context. '.repeat(40)}\n\nFull content ${index + 1}.`,
      ...(index === 99
        ? {
            tool_calls: [
              {
                name: 'web_search',
                arguments: { query: 'research' },
                id: 'search-call-100',
              },
            ],
            tool_results: [
              {
                name: 'web_search',
                result: { hits: ['Useful result'] },
                id: 'search-result-100',
              },
            ],
          }
        : {}),
    })),
  ],
};

export async function mockApi(page: Page) {
  await mockCacheClear(page);
  await page.route('**/api/entitlements', (route) =>
    route.fulfill({
      json: { plan: 'free', capabilities: ['chat'], limits: {} },
    }),
  );
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
        featured: Array.from({ length: 8 }, (_, index) => ({
          id: `fixture-model-${index + 1}`,
          name: `Fixture model ${index + 1}`,
          tagline: 'Fictional catalog option',
          badges: [],
        })),
      },
    }),
  );
}
