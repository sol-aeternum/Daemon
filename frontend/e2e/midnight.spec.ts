import { expect, test, type Page } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  // Exercise the online application with deterministic API fixtures. Present a
  // browser without service-worker support so Serwist doesn't try to register
  // against Playwright's blocked worker API. PWA/offline behavior is not covered.
  await page.addInitScript(() => {
    Reflect.deleteProperty(Navigator.prototype, 'serviceWorker');
  });
});

// Fictional API responses exercise the real Next/React integration. These are
// not authenticated production evidence and never invoke a provider.
const conversation = {
  id: 'midnight-fixture',
  title: 'Midnight review',
  pinned: false,
  status: 'active',
  created_at: '2026-09-30T00:00:00Z',
  updated_at: '2026-09-30T00:00:00Z',
  title_locked: false,
  metadata: {},
  messages: [
    {
      id: 'question',
      role: 'user',
      content: 'Compare these fictional reports.',
    },
    ...['older', 'newer'].map((name) => ({
      id: name,
      role: 'assistant',
      content: `${name} response\n\n\`\`\`js\nconst answer = 42;\n\`\`\``,
      tool_calls: [
        { name: 'web_search', arguments: { query: 'fixture' } },
        { name: 'generate_document', arguments: {} },
      ],
      tool_results: [
        {
          name: 'web_search',
          result: {
            results: [
              {
                url: 'https://example.org/reference',
                title: 'Fictional reference',
              },
            ],
          },
        },
        {
          name: 'generate_document',
          result: {
            success: true,
            data: {
              file_url: `/generated-files/${name}.csv`,
              filename: `${name}.csv`,
              format: 'csv',
            },
          },
        },
      ],
    })),
  ],
};

async function mockApi(page: Page) {
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
      json: { access_token: 'midnight-fixture-token', expires_in: 3600 },
    }),
  );
  await page.route('**/conversations*', (route) =>
    route.fulfill({ json: { conversations: [conversation] } }),
  );
  await page.route('**/conversations/midnight-fixture', (route) =>
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
  await page.route('**/generated-files/*.csv', (route) =>
    route.fulfill({
      contentType: 'text/csv',
      body: `report,value\n${route.request().url().includes('older') ? 'older fixture' : 'newer fixture'},42`,
    }),
  );
  await page.route('**/memories?*', (route) =>
    route.fulfill({ json: { memories: [], total: 0 } }),
  );
}

for (const theme of ['dark', 'light']) {
  for (const width of [1440, 768, 390, 360]) {
    test(`${theme} ${width}: selected output, focus, draft and settings`, async ({
      page,
    }, testInfo) => {
      await mockApi(page);
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      page.on('console', (message) => {
        if (message.type() === 'error' || message.type() === 'warning')
          errors.push(message.text());
      });
      await page.setViewportSize({ width, height: 844 });
      await page.addInitScript(
        (value) => localStorage.setItem('daemon-theme', value),
        theme,
      );
      await page.goto('/?id=midnight-fixture');
      const composer = page.locator('textarea').first();
      await composer.fill('Keep my unfinished draft');
      await page.locator('input[type="file"]').setInputFiles({
        name: 'draft.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from('fictional attachment'),
      });
      const older = page.getByRole('button', {
        name: 'Preview older.csv',
        exact: true,
      });
      await older.click();
      const details =
        width >= 1100
          ? page.getByRole('complementary', { name: 'Conversation details' })
          : page.getByRole('dialog', { name: 'Conversation details' });
      await expect(details).toBeVisible();
      await expect(
        details.getByText('older fixture', { exact: true }),
      ).toBeVisible();
      await expect(
        details.getByText('newer fixture', { exact: true }),
      ).toHaveCount(0);
      await page.screenshot({
        path: testInfo.outputPath(`${width}-${theme}-preview.png`),
      });
      await page.keyboard.press('Escape');
      await expect(details).toHaveCount(0);
      await expect(older).toBeFocused();
      await expect(composer).toHaveValue('Keep my unfinished draft');
      await expect(
        page.getByRole('button', { name: 'Remove draft.txt' }),
      ).toBeVisible();
      await page
        .getByRole('button', { name: 'Open conversation details' })
        .filter({ visible: true })
        .click();
      await expect(
        details.getByRole('link', { name: /Fictional reference/ }),
      ).toHaveCount(1);
      await details.getByRole('tab', { name: 'Activity' }).click();
      await expect(details.getByText('Result returned')).toHaveCount(4);
      await page.keyboard.press('Escape');
      const bounds = await composer.boundingBox();
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(844);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: testInfo.outputPath(`${width}-${theme}-chat.png`),
      });
      // Use a real client navigation, not page.goto (which reloads the memory store).
      if (width < 768)
        await page
          .getByRole('button', { name: /open.*(sidebar|menu)|menu/i })
          .first()
          .click();
      const settingsButton = page
        .getByRole('button', { name: 'Open settings', exact: true })
        .filter({ visible: true });
      if (await settingsButton.count()) await settingsButton.click();
      else {
        await page
          .getByRole('button', { name: 'U User Free', exact: true })
          .click();
        await page
          .getByRole('button', { name: 'Settings', exact: true })
          .click();
      }
      await expect(page).toHaveURL(/\/settings/);
      await page
        .getByRole('navigation', { name: 'Settings' })
        .getByRole('link', { name: 'Appearance' })
        .click();
      await expect(
        page.getByRole('heading', { name: 'Appearance', exact: true }),
      ).toBeVisible();
      await page.screenshot({
        animations: 'disabled',
        path: testInfo.outputPath(`${width}-${theme}-appearance.png`),
      });
      await page.getByRole('link', { name: '← Chat' }).click();
      await expect(composer).toHaveValue('Keep my unfinished draft');
      await expect(
        page.getByRole('button', { name: 'Remove draft.txt' }),
      ).toBeVisible();
      expect(errors).toEqual([]);
    });
  }
}

test('unavailable file is visible and does not regenerate or lose the draft', async ({
  page,
}) => {
  await mockApi(page);
  let submits = 0;
  await page.route('**/api/chat', (route) => {
    submits += 1;
    return route.abort();
  });
  await page.route('**/generated-files/older.csv', (route) =>
    route.fulfill({ status: 404 }),
  );
  await page.goto('/?id=midnight-fixture');
  await page.locator('textarea').fill('retain');
  await page
    .getByRole('button', { name: 'Preview older.csv', exact: true })
    .click();
  await expect(
    page
      .getByRole('complementary', { name: 'Conversation details' })
      .getByRole('alert'),
  ).toContainText('unavailable (missing or expired)');
  await page
    .getByRole('button', { name: 'Close conversation details' })
    .click();
  await expect(page.locator('textarea')).toHaveValue('retain');
  expect(submits).toBe(0);
});

test('Escape closes details before Stop and preserves a newer streaming draft', async ({
  page,
}) => {
  await mockApi(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => {
    const original = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url =
        typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      if (!url.endsWith('/api/chat')) return original(input, init);
      const encoder = new TextEncoder();
      return new Response(
        new ReadableStream({
          start(controller) {
            for (const event of [
              { type: 'start', messageId: 'streaming-fixture' },
              { type: 'text-start', id: 'text' },
              {
                type: 'text-delta',
                id: 'text',
                delta: 'Fictional partial answer.',
              },
            ])
              controller.enqueue(
                encoder.encode(`data: ${JSON.stringify(event)}\n\n`),
              );
          },
        }),
        {
          headers: {
            'content-type': 'text/event-stream',
            'x-vercel-ai-ui-message-stream': 'v1',
          },
        },
      );
    };
  });
  await page.goto('/?id=midnight-fixture');
  const composer = page.locator('textarea');
  await composer.fill('Begin fixture');
  await composer.press('Enter');
  await expect(page.getByText('Fictional partial answer.')).toBeVisible();
  await composer.fill('Keep next draft');
  await composer.press('Enter');
  await expect(composer).toHaveValue('Keep next draft');
  await page
    .getByRole('button', { name: 'Open conversation details' })
    .filter({ visible: true })
    .click();
  await expect(
    page.getByRole('dialog', { name: 'Conversation details' }),
  ).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(
    page.getByRole('dialog', { name: 'Conversation details' }),
  ).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Stop/ })).toBeVisible();
  await composer.focus();
  await page.keyboard.press('Escape');
  await expect(page.getByText('(stopped)', { exact: true })).toBeVisible();
  await expect(composer).toHaveValue('Keep next draft');
});
