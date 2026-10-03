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

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test('a 100-message thread collapses long older messages and can expand them', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/?id=conversation-1');
  const messages = page.getByRole('main', { name: 'Conversation messages' });
  await expect(messages.locator('article')).toHaveCount(100);
  await expect
    .poll(() =>
      messages.evaluate(
        (element) => element.scrollHeight - element.clientHeight,
      ),
    )
    .toBeGreaterThan(200);
  await expect(messages).not.toContainText(
    'Internal context must stay hidden.',
  );
  await expect(
    messages.getByRole('button', { name: 'Show more', exact: true }),
  ).toHaveCount(85);
  const short = messages.locator('[data-message-id="message-4"]');
  await expect(short.getByText('Message 4 short note.')).toBeVisible();
  await expect(short.getByRole('button', { name: 'Show more' })).toHaveCount(0);
  await expect(
    messages.locator('[data-message-id="message-100"]'),
  ).toContainText('Full content 100.');
  const old = messages.locator('[data-message-id="message-2"]');
  const oldDetail = old.getByText('Full content 2.');
  await old.scrollIntoViewIfNeeded();
  await expect(old.locator('[hidden="until-found"]')).toHaveCount(1);
  await expect(oldDetail).toBeHidden();
  await page.screenshot({ path: testInfo.outputPath('collapsed-message.png') });
  await old.getByRole('button', { name: 'Show more' }).click();
  await expect(oldDetail).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('expanded-message.png') });
  await old.getByRole('button', { name: 'Show less' }).click();
  await expect(oldDetail).toBeHidden();

  const fps = await messages.evaluate(
    (element) =>
      new Promise<number>((resolve) => {
        let frames = 0;
        const started = performance.now();
        function step(now: number) {
          frames += 1;
          element.scrollTop =
            ((now - started) / 1000) *
            (element.scrollHeight - element.clientHeight);
          if (now - started >= 1000) resolve((frames * 1000) / (now - started));
          else requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
      }),
  );
  console.log(`100-message scroll: ${fps.toFixed(1)} FPS`);
  expect(fps).toBeGreaterThan(30);
});

test('browser find reveals text inside a collapsed message', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/?id=conversation-1');
  const messages = page.getByRole('main', { name: 'Conversation messages' });
  await expect(messages.locator('article')).toHaveCount(100);
  const old = messages.locator('[data-message-id="message-2"]');
  const oldDetail = old.getByText('Full content 2.');
  await expect(oldDetail).toBeHidden();
  // Text fragments reveal hidden="until-found" content through the same
  // beforematch path as Ctrl+F, which Playwright cannot drive directly.
  await page.evaluate(() => {
    location.hash = ':~:text=Full%20content%202.';
  });
  await expect(oldDetail).toBeVisible();
  await expect(old.getByRole('button', { name: 'Show less' })).toBeVisible();
});

for (const width of [390, 1440]) {
  test(`long-chat reload keeps scrolling inside messages at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/?id=conversation-1');
    const messages = page.getByRole('main', { name: 'Conversation messages' });
    const wrapper = page
      .locator('[data-panel] > div')
      .filter({ has: messages });
    const footer = page.locator('footer');
    const header = page.locator('header').filter({ visible: true });
    const assertBoundary = async () => {
      await expect(wrapper).toHaveCSS('overflow-y', 'clip');
      await expect(messages).toHaveCSS('overscroll-behavior-y', 'contain');
      expect(await wrapper.evaluate((element) => element.scrollTop)).toBe(0);
      const bounds = await page.evaluate(() => ({
        pageY: scrollY,
        excess: document.documentElement.scrollHeight - innerHeight,
        headerTop: [...document.querySelectorAll('header')]
          .find((element) => element.getBoundingClientRect().height > 0)!
          .getBoundingClientRect().top,
        footerBottom: document.querySelector('footer')!.getBoundingClientRect()
          .bottom,
      }));
      expect(bounds.pageY).toBe(0);
      expect(bounds.excess).toBeLessThanOrEqual(1);
      expect(bounds.headerTop).toBeGreaterThanOrEqual(0);
      expect(bounds.footerBottom).toBeLessThanOrEqual(1001);
    };
    for (let reload = 0; reload < 2; reload++) {
      if (reload) await page.reload();
      await expect(messages.locator('article')).toHaveCount(100);
      await expect
        .poll(() =>
          messages.evaluate(
            (element) =>
              element.scrollHeight - element.clientHeight - element.scrollTop,
          ),
        )
        .toBeLessThan(64);
      expect(
        await wrapper.evaluate(
          (element) => element.scrollHeight - element.clientHeight,
        ),
      ).toBeLessThanOrEqual(1);
      await messages.hover();
      await page.mouse.wheel(0, 3000);
      await assertBoundary();
      const old = messages.locator('[data-message-id="message-2"]');
      await old.scrollIntoViewIfNeeded();
      await old.getByRole('button', { name: 'Show more', exact: true }).focus();
      await page.keyboard.press('Enter');
      await expect(old.getByText('Full content 2.')).toBeVisible();
      await assertBoundary();
      await footer.getByRole('button', { name: 'Model: Auto' }).click();
      await expect(
        page.getByText('Smart routing based on your message'),
      ).toBeVisible();
      const menuChoices = page.getByRole('button', {
        name: /Fixture model \d/,
      });
      await expect(menuChoices).toHaveCount(8);
      for (const choice of await menuChoices.all()) {
        const box = (await choice.boundingBox())!;
        expect(box.y).toBeGreaterThanOrEqual(0);
        expect(box.y + box.height).toBeLessThanOrEqual(1000);
      }
      await page
        .getByRole('button', { name: /Smart routing based on your message/ })
        .click();
      await page
        .getByRole('button', { name: 'Open conversation details', exact: true })
        .click();
      await expect(
        page.getByRole('heading', { name: 'Conversation details' }),
      ).toBeVisible();
      await page
        .getByRole('button', { name: 'Close conversation details' })
        .click();
      await assertBoundary();

      // The real overflow-producing descendant is not yet isolated (#428).
      // Inject excess wrapper content to prove this boundary stays non-scrollable
      // even then; unlike overflow:hidden, clip must reject programmatic scroll.
      const headerBefore = await header.boundingBox();
      const footerBefore = await footer.boundingBox();
      await wrapper.evaluate((element) => {
        const excess = document.createElement('div');
        excess.dataset.scrollBoundaryProbe = 'true';
        // The chat content is already positioned. Add overflowing paint, not a
        // flex sibling that would shrink the chat and invalidate the fixture.
        excess.style.cssText =
          'position:absolute;top:100%;height:1800px;width:1px';
        element.firstElementChild!.appendChild(excess);
      });
      expect(
        await wrapper.evaluate(
          (element) => element.scrollHeight - element.clientHeight,
        ),
      ).toBeGreaterThan(1000);
      await wrapper.evaluate((element) => {
        element.scrollTop = element.scrollHeight;
        element.scrollTo({ top: element.scrollHeight });
      });
      await messages.evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
      await messages.hover();
      await page.mouse.wheel(0, 3000);
      await page.getByRole('textbox', { name: 'Message Daemon' }).focus();
      await page.keyboard.press('Tab');
      await assertBoundary();
      expect(await header.boundingBox()).toEqual(headerBefore);
      expect(await footer.boundingBox()).toEqual(footerBefore);
      await wrapper
        .locator('[data-scroll-boundary-probe]')
        .evaluate((element) => element.remove());
    }
  });
}

for (const width of [375, 1440]) {
  test(`tool-log preference persists after reload at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/?id=conversation-1');
    const toggle = page
      .getByRole('button', { name: 'Hide tool calls' })
      .filter({ visible: true });
    const latest = page.locator('[data-message-id="message-100"]');
    await expect(
      latest.getByRole('button', { name: /^Tool activity:/ }),
    ).toBeVisible();
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await expect(
      latest.getByRole('button', { name: /^Tool activity:/ }),
    ).toHaveCount(0);
    await expect(latest).toContainText('Full content 100.');
    await page.reload();
    await expect(toggle).toHaveAttribute('aria-pressed', 'true');
    await expect(
      latest.getByRole('button', { name: /^Tool activity:/ }),
    ).toHaveCount(0);
    await toggle.click();
    await expect(
      latest.getByRole('button', { name: /^Tool activity:/ }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  });
}

test('streaming preserves reading position and Jump to latest resumes following', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
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
            send({ type: 'start', messageId: 'streaming-reply' });
            send({ type: 'text-start', id: 'reply-text' });
            send({
              type: 'text-delta',
              id: 'reply-text',
              delta: 'Live reply begins. ',
            });
            (
              window as unknown as { appendChatText: (text: string) => void }
            ).appendChatText = (text) => {
              send({ type: 'text-delta', id: 'reply-text', delta: text });
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
  await page.goto('/?id=conversation-1');
  const messages = page.getByRole('main', { name: 'Conversation messages' });
  await expect(messages.locator('article')).toHaveCount(100);
  const distanceFromBottom = () =>
    messages.evaluate(
      (element) =>
        element.scrollHeight - element.scrollTop - element.clientHeight,
    );
  await expect.poll(distanceFromBottom).toBeLessThan(64);
  await messages.evaluate((element) => {
    element.scrollTop = 200;
  });
  // Wait for the native scroll event before starting the stream.
  await expect
    .poll(() => messages.evaluate((element) => element.scrollTop))
    .toBe(200);
  await page.locator('textarea').fill('Continue this discussion');
  await page.locator('textarea').press('Enter');
  await expect(messages).toContainText('Live reply begins.');
  const jump = page.getByRole('button', { name: 'Jump to latest' });
  await expect(jump).toBeVisible();
  await expect
    .poll(() => messages.evaluate((element) => element.scrollTop))
    .toBeLessThan(250);
  await jump.click();
  await expect.poll(distanceFromBottom).toBeLessThan(64);
  await page.evaluate(() => {
    (
      window as unknown as { appendChatText: (text: string) => void }
    ).appendChatText('More streamed content. '.repeat(400));
  });
  await expect(messages).toContainText('More streamed content.');
  await expect.poll(distanceFromBottom).toBeLessThan(64);
  await expect(jump).toHaveCount(0);
  await messages.evaluate((element) => {
    element.scrollTop = 300;
  });
  await expect(jump).toBeVisible();
  await page.evaluate(() => {
    (
      window as unknown as { appendChatText: (text: string) => void }
    ).appendChatText('Still streaming. '.repeat(300));
  });
  await expect(messages).toContainText('Still streaming.');
  await expect
    .poll(() => messages.evaluate((element) => element.scrollTop))
    .toBeLessThan(350);
});
