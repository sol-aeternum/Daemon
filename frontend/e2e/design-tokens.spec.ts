import { expect, test, type Page } from '@playwright/test';

const conversation = {
  id: 'conversation-1',
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

async function mockApi(page: Page) {
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
    route.fulfill({ json: { conversations: [conversation] } }),
  );
  await page.route('**/conversations/conversation-1', (route) =>
    route.fulfill({ json: conversation }),
  );
  await page.route('**/users/me/settings', (route) =>
    route.fulfill({ json: { display_name: 'Alex', tier: 'pro' } }),
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
    route.fulfill({ json: { memories: [], total: 0 } }),
  );
  await page.route('**/video-credits/balance*', (route) =>
    route.fulfill({ json: { balance: 100 } }),
  );
}

const pages = [
  {
    slug: 'chat',
    path: '/?id=conversation-1',
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

for (const theme of ['dark', 'light']) {
  for (const target of pages) {
    test(`${target.slug} uses ${theme} tokens`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.clock.setFixedTime(new Date('2026-09-22T12:00:00Z'));
      await page.addInitScript(
        (value) => localStorage.setItem('daemon-theme', value),
        theme,
      );
      await mockApi(page);
      await page.goto(target.path);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await expect(
        page
          .getByText(target.ready, { exact: false })
          .filter({ visible: true })
          .first(),
      ).toBeVisible();
      await page.waitForLoadState('networkidle');

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
        theme === 'dark' ? 'rgb(22, 24, 29)' : 'rgb(249, 250, 251)',
      );
      expect(styles.touch).toBe('44px');
      expect(styles.legacy).toBe('');
      expect(styles.smallLabels).toBe(true);
      expect(styles.touchControls).toBe(true);
      expect(styles.overflow).toBe(false);

      const options = {
        fullPage: true,
        animations: 'disabled' as const,
        style: 'nextjs-portal { display: none !important; }',
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
