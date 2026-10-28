import { expect, type Page } from '@playwright/test';
import { captureSurface, test } from './design-token-capture';
import {
  historyFixtureConversationId,
  historyFixtureList,
  historyFixtureDetail,
} from './history-fixtures';

// Current GET /users/me/settings contract: routes/users.py, ProfileTab and
// HomeSuggestionsPreference. Synthetic saved preferences, not an account/plan.
const settings = {
  preferences: {
    display_name: 'Alex',
    custom_instructions: '',
    personality: 'default',
    characteristics: {
      warmth: 'default',
      enthusiasm: 'default',
      emoji: 'default',
      formatting: 'default',
    },
    home_suggestions_enabled: false,
  },
};

// Consumer-relevant subset of routes/system.py + memory/embedding.py's additive
// /status contract. Deliberately unavailable, never a fabricated green Ready.
const status = {
  status: 'healthy',
  db_healthy: true,
  redis_healthy: true,
  memory_enabled: true,
  embeddings: {
    configuration: 'unavailable',
    reason_codes: ['route_unapproved'],
    observation_scope: 'backend_process',
    last_outcome: 'never_attempted',
    last_success_at: null,
    last_failure_at: null,
    configuration_denials_total: 0,
    configuration_denial_reason_counts: {},
  },
};

type StatusState = 'unavailable' | 'missing' | 'error' | 'eligible';

const conversation = {
  id: historyFixtureConversationId,
  title: 'Planning',
  status: 'active',
  pinned: false,
  created_at: '2026-09-22T10:00:00Z',
  updated_at: '2026-09-22T10:00:00Z',
  title_locked: false,
  metadata: {},
  messages: [
    {
      id: 'question',
      role: 'user',
      content: 'Help me plan a focused afternoon.',
    },
    {
      id: 'answer',
      role: 'assistant',
      content: 'Start with your most important task, then take a short break.',
    },
  ],
};

async function mockApi(
  page: Page,
  baseURL: string,
  statusState: StatusState = 'unavailable',
) {
  const fixtures: { path: string; method: string; status: number }[] = [];
  // No unmatched API request may escape to a live backend or become HTML 404.
  // Next's own same-origin assets/documents/RSC remain real browser requests.
  await page.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const appOrigin = new URL(baseURL).origin;
    const nextDiagnostic =
      url.pathname === '/__nextjs_original-stack-frames' &&
      request.method() === 'POST';
    const nextPage =
      request.method() === 'GET' &&
      url.searchParams.has('_rsc') &&
      pages.some(
        (target) => new URL(target.path, baseURL).pathname === url.pathname,
      );
    if (
      url.origin !== appOrigin ||
      (['fetch', 'xhr'].includes(request.resourceType()) &&
        !url.pathname.startsWith('/_next/') &&
        !nextPage &&
        !nextDiagnostic)
    ) {
      fixtures.push({
        path: url.pathname,
        method: request.method(),
        status: 0,
      });
      await route.abort('blockedbyclient');
      return;
    }
    await route.continue();
  });
  page.on('response', (response) => {
    const path = new URL(response.url()).pathname;
    if (
      !path.startsWith('/_next/') &&
      ['fetch', 'xhr'].includes(response.request().resourceType())
    ) {
      fixtures.push({
        path,
        method: response.request().method(),
        status: response.status(),
      });
    }
  });
  await page.route('**/api/v1/auth/cache/clear', (route) =>
    route.fulfill({
      status: 204,
      headers: {
        'Cache-Control': 'no-store',
        'Clear-Site-Data': '"cache"',
      },
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
      json: {
        access_token: 'browser-test-token',
        expires_in: 3600,
      },
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
    route.fulfill({ json: settings }),
  );
  // The home hook can load before the saved conversation is restored. Match
  // routes/home_suggestions.py's disabled response to the explicit preference.
  await page.route('**/home-suggestions', (route) =>
    route.fulfill({
      headers: { 'Cache-Control': 'no-store' },
      json: { enabled: false, status: 'disabled', suggestions: [] },
    }),
  );
  // Preserve the existing conservative Plan unavailable UI deliberately, using
  // useEntitlements' documented 404 fallback, not an incidental loopback HTML page.
  await page.route('**/api/entitlements', (route) =>
    route.fulfill({
      status: 404,
      json: { detail: 'Plan details are not available yet.' },
    }),
  );
  await page.route('**/status', (route) =>
    route.fulfill({
      status: statusState === 'error' ? 503 : 200,
      json:
        statusState === 'missing'
          ? { status: 'healthy' }
          : statusState === 'error'
            ? { detail: 'Synthetic status service unavailable' }
            : {
                ...status,
                embeddings: {
                  ...status.embeddings,
                  configuration:
                    statusState === 'eligible' ? 'eligible' : 'unavailable',
                  reason_codes:
                    statusState === 'eligible' ? [] : ['route_unapproved'],
                },
              },
    }),
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
  await page.route(/^http:\/\/[^/]+\/(?:api\/)?skills(?:\/|\?|$)/, (route) =>
    route.fulfill({ json: { skills: [] } }),
  );
  await page.route(/^http:\/\/[^/]+\/(?:api\/)?memories(?:\/|\?|$)/, (route) =>
    route.fulfill({ json: { memories: [], total: 0, has_more: false } }),
  );
  await page.route('**/video-credits/balance*', (route) =>
    route.fulfill({ json: { balance: 100 } }),
  );
  return fixtures;
}

const pages = [
  {
    slug: 'chat',
    path: `/?id=${conversation.id}`,
    ready: 'Help me plan a focused afternoon.',
  },
  { slug: 'landing', path: '/landing', ready: 'Your AI, Your Rules.' },
  {
    slug: 'artifacts',
    path: '/artifacts',
    ready: 'No generated artifacts found yet.',
  },
  { slug: 'studio', path: '/studio', ready: 'Image generation is retired' },
  { slug: 'profile', path: '/settings/profile', ready: 'Profile' },
  {
    slug: 'appearance',
    path: '/settings/appearance',
    ready: 'Currently using',
  },
  { slug: 'memory', path: '/settings/memory', ready: 'Memory' },
  {
    slug: 'skills',
    path: '/settings/skills',
    ready: 'Create reusable skill files',
  },
];

test.use({ locale: 'en-US', timezoneId: 'UTC', deviceScaleFactor: 1 });

test.beforeEach(async ({ page, captureDiagnostics, baseURL }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.clock.setFixedTime(new Date('2026-09-22T12:00:00Z'));
  captureDiagnostics.expectedHttpFailures.push({
    url: new URL('/api/entitlements', baseURL).href,
    status: 404,
  });
});

async function fixtureReady(
  page: Page,
  slug: string,
  fixtures: Awaited<ReturnType<typeof mockApi>>,
  statusState: StatusState = 'unavailable',
) {
  if (slug !== 'landing') {
    await expect(
      page.getByText('Plan unavailable', { exact: true }),
    ).toBeVisible();
    expect(fixtures).toContainEqual({
      path: '/api/entitlements',
      method: 'GET',
      status: 404,
    });
  }
  if (slug === 'profile') {
    await expect(page.getByLabel('Display Name', { exact: true })).toHaveValue(
      'Alex',
    );
    await expect(
      page.getByLabel('Custom Instructions', { exact: true }),
    ).toHaveValue('');
    await expect(
      page.getByText('Personal suggestions are off.', { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Turn on suggestions', exact: true }),
    ).toBeEnabled();
    expect(fixtures).toContainEqual({
      path: '/users/me/settings',
      method: 'GET',
      status: 200,
    });
  }
  if (slug === 'memory') {
    const label =
      statusState === 'unavailable'
        ? 'Embeddings unavailable'
        : statusState === 'eligible'
          ? 'Embeddings unverified'
          : 'Embeddings unknown';
    await expect(
      page.getByLabel(`Memory embeddings: ${label}`, { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText('No memories found', { exact: true }),
    ).toBeAttached();
    await expect(
      page.getByRole('button', { name: 'Clear All', exact: true }),
    ).toBeDisabled();
    await expect(page.getByText('Ready', { exact: true })).toHaveCount(0);
    expect(fixtures).toContainEqual({
      path: '/status',
      method: 'GET',
      status: statusState === 'error' ? 503 : 200,
    });
    expect(fixtures).toContainEqual({
      path: '/memories',
      method: 'GET',
      status: 200,
    });
  }
  expect(
    fixtures.filter((fixture) => fixture.status === 0),
    'Unmocked API requests',
  ).toEqual([]);
}

// Positive capture checks independent of the intentionally unchanged old PNGs.
for (const slug of ['profile', 'memory']) {
  test(`${slug} fixture qualifies the original top-of-main capture`, async ({
    page,
    captureDiagnostics,
    baseURL,
  }, testInfo) => {
    await page.addInitScript(() =>
      localStorage.setItem('daemon-theme', 'dark'),
    );
    const fixtures = await mockApi(page, baseURL!);
    await page.goto(`/settings/${slug}`);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.waitForLoadState('networkidle');
    await fixtureReady(page, slug, fixtures);
    const surface = await captureSurface(page, true);
    captureDiagnostics.assertClean();
    await testInfo.attach('capture-surface.json', {
      body: JSON.stringify({ surface, fixtures }, null, 2),
      contentType: 'application/json',
    });
    await testInfo.attach('qualified-top-of-main', {
      body: await page.screenshot({ fullPage: true, animations: 'disabled' }),
      contentType: 'image/png',
    });
  });
}

for (const state of ['missing', 'error', 'eligible'] as const) {
  test(`memory ${state} status never presents healthy embeddings`, async ({
    page,
    captureDiagnostics,
    baseURL,
  }, testInfo) => {
    if (state === 'error')
      captureDiagnostics.expectedHttpFailures.push({
        url: new URL('/status', baseURL).href,
        status: 503,
      });
    const fixtures = await mockApi(page, baseURL!, state);
    await page.goto('/settings/memory');
    await page.waitForLoadState('networkidle');
    await fixtureReady(page, 'memory', fixtures, state);
    captureDiagnostics.assertClean();
    await testInfo.attach('status-fixture.json', {
      body: JSON.stringify({ state, fixtures }, null, 2),
      contentType: 'application/json',
    });
  });
}

for (const theme of ['dark', 'light']) {
  for (const target of pages) {
    test(`${target.slug} uses ${theme} tokens`, async ({
      page,
      captureDiagnostics,
      baseURL,
    }, testInfo) => {
      await page.addInitScript(
        (value) => localStorage.setItem('daemon-theme', value),
        theme,
      );
      const fixtures = await mockApi(page, baseURL!);
      await page.goto(target.path);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(
        page
          .getByText(target.ready, { exact: false })
          .filter({ visible: true })
          .first(),
      ).toBeVisible();
      await page.waitForLoadState('networkidle');
      await fixtureReady(page, target.slug, fixtures);
      const surface = await captureSurface(
        page,
        ['profile', 'memory'].includes(target.slug),
      );
      await testInfo.attach('capture-surface.json', {
        body: JSON.stringify({ surface, fixtures }, null, 2),
        contentType: 'application/json',
      });
      captureDiagnostics.assertClean();

      const styles = await page.evaluate(() => {
        const root = getComputedStyle(document.documentElement);
        return {
          background: getComputedStyle(document.body).backgroundColor,
          touch: root.getPropertyValue('--touch-min').trim(),
          legacy: root.getPropertyValue('--daemon-bg-primary').trim(),
          smallLabels: [...document.querySelectorAll('.text-xs')].every(
            (element) => parseFloat(getComputedStyle(element).fontSize) >= 12,
          ),
          overflow: document.documentElement.scrollWidth > innerWidth,
          touchControls: [...document.querySelectorAll('.min-h-touch')].every(
            (element) => getComputedStyle(element).minHeight === '44px',
          ),
        };
      });
      expect(styles.background).toBe(
        theme === 'dark' ? 'rgb(22, 25, 31)' : 'rgb(249, 250, 252)',
      );
      expect(styles.touch).toBe('44px');
      expect(styles.legacy).toBe('');
      expect(styles.smallLabels).toBe(true);
      expect(styles.touchControls).toBe(true);
      expect(styles.overflow).toBe(false);

      const options = {
        fullPage: true,
        animations: 'disabled' as const,
      };
      await page.screenshot({
        ...options,
        path: testInfo.outputPath(`${target.slug}-${theme}.png`),
      });
      await expect(page).toHaveScreenshot(
        `${target.slug}-${theme}.png`,
        options,
      );
    });
  }
}
