import { expect, test, type Page } from '@playwright/test';

const conversation = {
  id: 'conversation-1',
  title: 'Planning',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  status: 'active',
  pinned: false,
  title_locked: false,
  metadata: {},
  messages: [],
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
      json: { access_token: 'browser-test-token', expires_in: 3600 },
    }),
  );
  await page.route('**/conversations*', (route) =>
    route.fulfill({ json: { conversations: [conversation] } }),
  );
  await page.route('**/conversations/conversation-1', (route) =>
    route.fulfill({ json: conversation }),
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

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test('Deliberate submits the Council command and opens its interview without consuming the draft', async ({
  page,
}) => {
  const submitted: unknown[] = [];
  await page.route('**/api/chat', async (route) => {
    submitted.push(route.request().postDataJSON());
    const chunks = [
      { type: 'start', messageId: 'council-reply' },
      {
        type: 'data-event',
        data: {
          type: 'council_interview',
          id: 'interview-1',
          request_id: 'request-1',
          roster: {},
          presets: ['Default'],
          rounds_options: [1, 2],
          audit_default: false,
        },
      },
      { type: 'finish' },
    ];
    await route.fulfill({
      contentType: 'text/event-stream',
      headers: { 'x-vercel-ai-ui-message-stream': 'v1' },
      body:
        chunks.map((chunk) => `data: ${JSON.stringify(chunk)}\n\n`).join('') +
        'data: [DONE]\n\n',
    });
  });
  await page.goto('/?id=conversation-1');
  const composer = page.getByPlaceholder(
    'Message Daemon — try /council, /image, /code',
  );
  await composer.fill('Keep this draft');
  await page.getByRole('button', { name: /Deliberate/ }).click();
  await expect(
    page.getByText('Council Configuration', { exact: true }),
  ).toBeVisible();
  expect(submitted).toHaveLength(1);
  const payload = submitted[0] as {
    messages: { role: string; parts: { type: string; text?: string }[] }[];
  };
  expect(payload.messages.at(-1)?.parts).toEqual([
    { type: 'text', text: '/council' },
  ]);
  await expect(composer).toHaveValue('Keep this draft');
});

test('header settings shortcut preserves the conversation across sections and back to chat', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/?id=conversation-1');
  await page.getByRole('button', { name: 'Open settings' }).click();
  await expect(page).toHaveURL(/\/settings\/profile\?from=conversation-1$/);
  await page
    .getByRole('navigation', { name: 'Settings' })
    .getByRole('link', { name: 'Appearance' })
    .click();
  await expect(page).toHaveURL(/\/settings\/appearance\?from=conversation-1$/);
  await page.getByRole('link', { name: '← Chat' }).click();
  await expect(page).toHaveURL(/\/\?id=conversation-1$/);
});

for (const width of [375, 768, 1440]) {
  test(`navigation fits at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1100 });
    await page.goto('/?id=conversation-1');
    await expect(
      page.getByRole('button', { name: /Deliberate/ }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Open settings' }),
    ).toBeVisible({ visible: width >= 768 });
    await page.screenshot({
      path: testInfo.outputPath(`chat-${width}.png`),
      fullPage: true,
    });
    await page.goto('/settings/profile?from=conversation-1');
    const nav = page.getByRole('navigation', { name: 'Settings' });
    await expect(nav).toBeInViewport();
    await expect(nav.getByRole('link')).toHaveCount(6);
    const links = await nav.getByRole('link').all();
    expect(links).toHaveLength(6);
    const bounds = await Promise.all(links.map((link) => link.boundingBox()));
    for (const box of bounds) {
      expect(box).not.toBeNull();
      expect(box!.height).toBeGreaterThanOrEqual(44);
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width);
      expect(box!.y + box!.height).toBeLessThanOrEqual(1100);
    }
    if (width < 768) {
      for (let i = 1; i < bounds.length; i++) {
        expect(bounds[i]!.y).toBeGreaterThanOrEqual(
          bounds[i - 1]!.y + bounds[i - 1]!.height,
        );
        expect(bounds[i]!.width).toBe(bounds[0]!.width);
      }
    }
    expect(
      await nav.evaluate(
        (element) => element.scrollWidth <= element.clientWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`settings-${width}.png`),
      fullPage: true,
    });
  });
}
