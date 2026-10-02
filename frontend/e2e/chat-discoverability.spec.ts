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
  const composer = page.locator('textarea');
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
  await expect(
    page.getByRole('heading', { name: 'Planning', exact: true }),
  ).toBeVisible();
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

test('an unsent draft and its attachment survive a page reload in the same tab', async ({
  page,
}) => {
  await page.goto('/?id=conversation-1');
  const composer = page.locator('textarea');
  await composer.fill('Half-written question');
  await page.locator('input[type="file"]').setInputFiles({
    name: 'notes.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('attached notes'),
  });
  await expect(
    page.getByRole('button', { name: 'Remove notes.txt' }),
  ).toBeVisible();
  // Let the attachment write reach IndexedDB before reloading.
  await page.waitForFunction(
    () =>
      new Promise<boolean>((resolve) => {
        const req = indexedDB.open('daemon-chat-drafts');
        req.onsuccess = () => {
          const tx = req.result.transaction('attachments', 'readonly');
          const count = tx.objectStore('attachments').count();
          count.onsuccess = () => resolve(count.result === 1);
          count.onerror = () => resolve(false);
        };
        req.onerror = () => resolve(false);
      }),
  );

  await page.reload();

  await expect(composer).toHaveValue('Half-written question');
  await expect(
    page.getByRole('button', { name: 'Remove notes.txt' }),
  ).toBeVisible();

  // Removing the attachment and clearing the text leaves nothing to restore.
  await page.getByRole('button', { name: 'Remove notes.txt' }).click();
  await composer.fill('');
  await page.reload();
  await expect(composer).toHaveValue('');
  await expect(
    page.getByRole('button', { name: 'Remove notes.txt' }),
  ).toHaveCount(0);
});

test('sidebar search finds older conversations through the server and pages results', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const requests: URLSearchParams[] = [];
  const older = (id: string, title: string) => ({
    ...conversation,
    id,
    title,
    updated_at: '2024-01-01T00:00:00Z',
    last_activity_at: '2024-01-01T00:00:00Z',
    message_count: 4,
  });
  await page.route(
    (url) =>
      url.pathname.endsWith('/conversations') && url.searchParams.has('search'),
    (route) => {
      const params = new URL(route.request().url()).searchParams;
      requests.push(params);
      const offset = Number(params.get('offset'));
      const conversations =
        offset === 0
          ? Array.from({ length: 50 }, (_, i) =>
              older(`old-${i}`, `Budget ${i}`),
            )
          : [older('old-extra', 'Budget archive')];
      return route.fulfill({ json: { conversations } });
    },
  );
  await page.goto('/?id=conversation-1');
  const search = page.getByRole('searchbox', {
    name: 'Search conversation titles',
  });
  await search.fill('budget');

  await expect(page.getByText('Budget 0', { exact: true })).toBeVisible();
  await expect(page.getByTestId('conversation-search-status')).toHaveText(
    '50+ titles match',
  );
  expect(requests.map((p) => p.get('search'))).toEqual(['budget']);

  await page.getByRole('button', { name: 'Load more results' }).click();
  await expect(page.getByText('Budget archive')).toBeVisible();
  expect(requests.at(-1)?.get('offset')).toBe('50');

  await search.press('Escape');
  await expect(search).toHaveValue('');
  await expect(page.getByText('Budget archive')).toHaveCount(0);
  await expect(page.getByTestId('conversation-search-status')).toHaveCount(0);
  await expect(page.getByText('Budget 0', { exact: true })).toHaveCount(0);
});
