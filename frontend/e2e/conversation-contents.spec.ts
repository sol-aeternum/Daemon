import { expect, test, type Page } from '@playwright/test';

// Entirely synthetic loaded history. No pagination, second history store, or
// live backend is involved in these production-module browser checks.
const conversation = {
  id: 'contents-long',
  title: 'Contents long fixture',
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
      content: 'Internal context stays hidden.',
    },
    ...Array.from({ length: 100 }, (_, index) => ({
      id: `message-${index + 1}`,
      role: index % 2 === 0 ? 'user' : 'assistant',
      content: `${index === 0 || index === 2 ? 'Duplicate prompt label' : `Message ${index + 1} first line`}\n\n${'Detailed synthetic discussion with useful context. '.repeat(45)}\n\nFindable detail ${index + 1}.`,
    })),
  ],
};
const otherConversation = {
  ...conversation,
  id: 'contents-other',
  title: 'Contents other fixture',
  messages: [
    {
      id: 'other-prompt',
      role: 'user',
      content: 'Only this conversation prompt',
    },
  ],
};

async function mockApi(page: Page) {
  await page.route('**/api/v1/auth/cache/clear', (route) =>
    route.fulfill({
      status: 204,
      headers: { 'Cache-Control': 'no-store', 'Clear-Site-Data': '"cache"' },
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
        access_token: 'synthetic-contents-token',
        expires_in: 3600,
      },
    }),
  );
  await page.route('**/conversations*', (route) =>
    route.fulfill({
      json: {
        conversations: [conversation, otherConversation],
      },
    }),
  );
  await page.route('**/conversations/contents-long', (route) =>
    route.fulfill({ json: conversation }),
  );
  await page.route('**/conversations/contents-other', (route) =>
    route.fulfill({ json: otherConversation }),
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

async function openContents(page: Page, width: number) {
  if (
    width < 768 &&
    !(await page.getByRole('dialog', { name: 'Contents', exact: true }).count())
  )
    await page
      .getByRole('button', { name: 'Open conversation contents' })
      .click();
  return page
    .getByRole('navigation', { name: 'Conversation contents' })
    .filter({ visible: true });
}

async function selectPrompt(page: Page, width: number, id: string) {
  const contents = await openContents(page, width);
  await contents.locator(`[data-contents-message-id="${id}"]`).click();
  const article = page.locator(`article[data-message-id="${id}"]`);
  await expect(article).toBeFocused();
  await expect(article.locator('[hidden="until-found"]')).toHaveCount(0);
  await expect
    .poll(() =>
      article.evaluate((element) => {
        const main = element.closest('main')!;
        return (
          element.getBoundingClientRect().top - main.getBoundingClientRect().top
        );
      }),
    )
    .toBeGreaterThanOrEqual(0);
  await expect
    .poll(() =>
      article.evaluate((element) => {
        const main = element.closest('main')!;
        return (
          element.getBoundingClientRect().top - main.getBoundingClientRect().top
        );
      }),
    )
    .toBeLessThan(40);
  return article;
}

async function switchConversation(page: Page, width: number, title: string) {
  if (width < 768)
    await page.getByRole('button', { name: 'Open menu', exact: true }).click();
  await page
    .getByRole('paragraph')
    .filter({ hasText: title })
    .filter({ visible: true })
    .click();
}

async function installStream(page: Page) {
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url =
        typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.href
            : input.url;
      if (!url.endsWith('/api/chat')) return originalFetch(input, init);
      const encoder = new TextEncoder();
      return new Response(
        new ReadableStream({
          start(controller) {
            const send = (chunk: object) =>
              controller.enqueue(
                encoder.encode(`data: ${JSON.stringify(chunk)}\n\n`),
              );
            send({ type: 'start', messageId: 'contents-stream-reply' });
            send({ type: 'text-start', id: 'contents-text' });
            send({
              type: 'text-delta',
              id: 'contents-text',
              delta: 'Contents stream begins. ',
            });
            (
              window as unknown as {
                appendContentsStream: (text: string) => void;
              }
            ).appendContentsStream = (text) => {
              send({ type: 'text-delta', id: 'contents-text', delta: text });
            };
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
}

test.beforeEach(async ({ page }) => mockApi(page));

for (const width of [375, 1440]) {
  test(`loaded contents repeat navigation, find and conversation reading positions at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/?id=contents-long');
    const main = page.getByRole('main', { name: 'Conversation messages' });
    await expect(main.locator('article')).toHaveCount(100);
    let contents = await openContents(page, width);
    await expect(contents).toContainText('Loaded portion only · 50 prompts');
    await expect(contents.getByRole('button')).toHaveCount(50);
    await expect(
      contents.getByRole('button', { name: /Duplicate prompt label/ }),
    ).toHaveCount(2);
    expect(
      (await contents.getByRole('button').first().boundingBox())!.height,
    ).toBeGreaterThanOrEqual(44);
    if (width < 768) {
      // Native dialog trap, including reverse Tab, on the long scrolling list.
      const close = page.getByRole('button', {
        name: 'Close conversation contents',
      });
      await close.focus();
      await page.keyboard.press('Shift+Tab');
      await expect(contents.getByRole('button').last()).toBeFocused();
      await page.keyboard.press('Tab');
      await expect(close).toBeFocused();
    }
    for (const id of [
      'message-1',
      'message-51',
      'message-3',
      'message-81',
      'message-21',
    ]) {
      await selectPrompt(page, width, id);
      contents = await openContents(page, width);
      await expect(
        contents.locator(`[data-contents-message-id="${id}"]`),
      ).toHaveAttribute('aria-current', 'location');
      if (width < 768) await page.keyboard.press('Escape');
    }
    const selected = main.locator('[data-message-id="message-21"]');
    const readingTop = await selected.evaluate(
      (element) => element.getBoundingClientRect().top,
    );
    await switchConversation(page, width, otherConversation.title);
    await expect(main.locator('article')).toHaveCount(1);
    contents = await openContents(page, width);
    await expect(contents.getByRole('button')).toHaveCount(1);
    await expect(contents).toContainText('Only this conversation prompt');
    await expect(contents).not.toContainText('Duplicate prompt label');
    if (width < 768) await page.keyboard.press('Escape');
    await switchConversation(page, width, conversation.title);
    await expect(main.locator('article')).toHaveCount(100);
    await expect
      .poll(() =>
        selected.evaluate((element) => element.getBoundingClientRect().top),
      )
      .toBeCloseTo(readingTop, 0);

    // Navigation must not break the native beforematch/browser-find path in a
    // different still-collapsed message. Text fragments use that browser path.
    const findTarget = main.locator('[data-message-id="message-2"]');
    await expect(findTarget.locator('[hidden="until-found"]')).toHaveCount(1);
    await page.evaluate(() => {
      location.hash = ':~:text=Findable%20detail%202.';
    });
    await expect(
      findTarget.getByText('Findable detail 2.', { exact: false }),
    ).toBeVisible();
    await expect(
      findTarget.getByRole('button', { name: 'Show less' }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect(await page.evaluate(() => scrollY)).toBe(0);
  });

  test(`contents leaves stream running, owns modal Escape and Latest resumes following at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await installStream(page);
    await page.goto('/?id=contents-long');
    const main = page.getByRole('main', { name: 'Conversation messages' });
    await expect(main.locator('article')).toHaveCount(100);
    await page
      .getByRole('textbox', { name: 'Message Daemon' })
      .fill('Continue the synthetic discussion');
    await page.getByRole('textbox', { name: 'Message Daemon' }).press('Enter');
    await expect(main).toContainText('Contents stream begins.');
    await selectPrompt(page, width, 'message-21');
    const top = await main.evaluate((element) => element.scrollTop);
    const stop = page.getByRole('button', {
      name: 'Stop generating',
      exact: true,
    });
    if (width < 768) {
      const opener = page.getByRole('button', {
        name: 'Open conversation contents',
      });
      await openContents(page, width);
      await page.keyboard.press('Escape');
      await expect(opener).toBeFocused();
    }
    await expect(stop).toBeVisible();
    await page.evaluate(() =>
      (
        window as unknown as { appendContentsStream: (text: string) => void }
      ).appendContentsStream('More synthetic streamed text. '.repeat(250)),
    );
    await expect(main).toContainText('More synthetic streamed text.');
    await expect
      .poll(() => main.evaluate((element) => element.scrollTop))
      .toBeCloseTo(top, 0);
    const jump = page.getByRole('button', {
      name: 'Jump to latest',
      exact: true,
    });
    await expect(jump).toBeVisible();
    await jump.click();
    await expect
      .poll(() =>
        main.evaluate(
          (element) =>
            element.scrollHeight - element.scrollTop - element.clientHeight,
        ),
      )
      .toBeLessThan(64);
    await page.evaluate(() =>
      (
        window as unknown as { appendContentsStream: (text: string) => void }
      ).appendContentsStream('Following remains live. '.repeat(250)),
    );
    await expect(main).toContainText('Following remains live.');
    await expect
      .poll(() =>
        main.evaluate(
          (element) =>
            element.scrollHeight - element.scrollTop - element.clientHeight,
        ),
      )
      .toBeLessThan(64);
    await expect(stop).toBeVisible();
    await expect(jump).toHaveCount(0);
    await stop.click();
  });
}
