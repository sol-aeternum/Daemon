import { expect, type Page } from '@playwright/test';
import { mockCacheClear, test } from './online-ui-fixture';
import {
  historyFixtureConversationId,
  historyFixtureId,
  historyFixtureList,
  historyFixtureDetail,
} from './history-fixtures';

const conversation = {
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

async function mockApi(page: Page) {
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

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test('the composer stays centered in the transcript when Contents changes visibility', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1114, height: 800 });
  await page.goto(`/?id=${conversation.id}`);

  const transcript = page.getByRole('main', { name: 'Conversation messages' });
  const rail = page.getByRole('complementary', { name: 'Contents' });
  const composer = page
    .locator('footer form')
    .filter({ has: page.getByRole('textbox', { name: 'Message Daemon' }) });
  const assertAligned = async () => {
    await expect(composer).toBeVisible();
    await expect
      .poll(async () => {
        const [main, form, footer] = await Promise.all([
          transcript.boundingBox(),
          composer.boundingBox(),
          page.locator('footer').boundingBox(),
        ]);
        if (!main || !form || !footer) return Infinity;
        return Math.max(
          Math.abs(main.x + main.width / 2 - (form.x + form.width / 2)),
          Math.abs(main.x - footer.x),
          Math.abs(main.width - footer.width),
          Math.abs(main.y + main.height - footer.y),
        );
      })
      .toBeLessThanOrEqual(1);
  };

  await expect(rail).toBeVisible();
  await assertAligned();

  await page.setViewportSize({ width: 390, height: 800 });
  await expect(rail).toBeHidden();
  await expect(
    page.getByRole('button', { name: 'Open conversation contents' }),
  ).toBeVisible();
  await assertAligned();

  await page.setViewportSize({ width: 1114, height: 800 });
  await page.route(
    (url) => url.pathname.endsWith(`/conversations/${conversation.id}`),
    (route) =>
      route.fulfill({
        json: historyFixtureDetail(
          {
            ...conversation,
            messages: [
              {
                id: 'message-1',
                role: 'assistant',
                content: 'No user prompts in this fixture.',
              },
            ],
          },
          new URL(route.request().url()),
        ),
      }),
  );
  await page.reload();
  await expect(transcript).toContainText('No user prompts in this fixture.');
  await expect(rail).toHaveCount(0);
  await assertAligned();
});

test('a 100-message thread collapses long older messages and can expand them', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(`/?id=${conversation.id}`);
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
  const short = messages.locator(`[data-message-id="${historyFixtureId(4)}"]`);
  await expect(short.getByText('Message 4 short note.')).toBeVisible();
  await expect(short.getByRole('button', { name: 'Show more' })).toHaveCount(0);
  await expect(
    messages.locator(`[data-message-id="${historyFixtureId(100)}"]`),
  ).toContainText('Full content 100.');
  const old = messages.locator(`[data-message-id="${historyFixtureId(2)}"]`);
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
  await page.goto(`/?id=${conversation.id}`);
  const messages = page.getByRole('main', { name: 'Conversation messages' });
  await expect(messages.locator('article')).toHaveCount(100);
  const old = messages.locator(`[data-message-id="${historyFixtureId(2)}"]`);
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
    await page.goto(`/?id=${conversation.id}`);
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
      const old = messages.locator(
        `[data-message-id="${historyFixtureId(2)}"]`,
      );
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
    await page.goto(`/?id=${conversation.id}`);
    const toggle = page
      .getByRole('button', { name: 'Hide tool calls' })
      .filter({ visible: true });
    const latest = page.locator(`[data-message-id="${historyFixtureId(100)}"]`);
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
  await page.goto(`/?id=${conversation.id}`);
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

type BurstMode = 'text-only' | 'thinking-then-text';

async function installBurstStream(
  page: Page,
  mode: BurstMode,
  deltaCount: number,
  throttleHiddenTimers = false,
) {
  await page.addInitScript(
    ({ mode, count, throttleHiddenTimers }) => {
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (input, init) => {
        const url =
          typeof input === 'string'
            ? input
            : input instanceof URL
              ? input.href
              : input.url;
        if (!url.endsWith('/api/chat')) return originalFetch(input, init);
        if (throttleHiddenTimers) {
          // Headless Chromium keeps both tabs visible. Model a hidden tab's
          // deferred short timers deterministically once the burst starts.
          // Leave other task sources and the genuine timer for longer waits.
          Object.defineProperty(document, 'visibilityState', {
            configurable: true,
            value: 'hidden',
          });
          Object.defineProperty(document, 'hidden', {
            configurable: true,
            value: true,
          });
          const timerHost: Window = window;
          const setTimeout = timerHost.setTimeout.bind(timerHost);
          timerHost.setTimeout = (handler, delay = 0, ...args) =>
            setTimeout(handler, delay <= 4 ? 60_000 : delay, ...args);
          document.dispatchEvent(new Event('visibilitychange'));
        }
        sessionStorage.setItem(
          'burst-chat-requests',
          String(
            Number(sessionStorage.getItem('burst-chat-requests') ?? '0') + 1,
          ),
        );
        const encoder = new TextEncoder();
        return new Response(
          new ReadableStream({
            start(controller) {
              const send = (chunk: object) =>
                controller.enqueue(
                  encoder.encode(`data: ${JSON.stringify(chunk)}\n\n`),
                );
              send({ type: 'start', messageId: 'burst-reply' });
              // Every chunk is enqueued before the stream yields, so the
              // client sees the whole burst with no task boundary between
              // deltas: the shape that reproduced #752 and #185.
              if (mode === 'thinking-then-text') {
                for (let index = 0; index < count; index += 1) {
                  send({
                    type: 'data-event',
                    data: {
                      type: 'thinking',
                      content: `Thought ${index + 1}. `,
                    },
                  });
                }
              }
              send({ type: 'text-start', id: 'burst-text' });
              for (let index = 0; index < count; index += 1) {
                send({
                  type: 'text-delta',
                  id: 'burst-text',
                  delta: `Delta ${index + 1}. `,
                });
              }
              send({
                type: 'text-delta',
                id: 'burst-text',
                delta: 'Burst reply complete.',
              });
              send({ type: 'text-end', id: 'burst-text' });
              send({ type: 'finish' });
              controller.close();
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
    },
    { mode, count: deltaCount, throttleHiddenTimers },
  );
}

const burstRequestCount = (page: Page) =>
  page.evaluate(() =>
    Number(sessionStorage.getItem('burst-chat-requests') ?? '0'),
  );

test('a burst of 145 text deltas renders the reply, stays connected and is not resent on reload (#752)', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBurstStream(page, 'text-only', 145);
  await page.goto(`/?id=${conversation.id}`);
  const messages = page.getByRole('main', { name: 'Conversation messages' });
  await expect(messages.locator('article')).toHaveCount(100);
  await page.locator('textarea').fill('Burst this reply');
  await page.locator('textarea').press('Enter');

  // The parsed reply carries every delta, not a truncated prefix of the burst.
  // A synthetic burst is paced across tasks (#752), so it can take longer than
  // a real stream; the default expect timeout is too short for 145 deltas.
  await expect(messages).toContainText('Burst reply complete.', {
    timeout: 30_000,
  });
  await expect(messages).toContainText('Delta 1. ');
  await expect(messages).toContainText('Delta 145. ');

  // A clean finish settles connected; the burst must not surface #185's
  // transport failure or the error boundary.
  await expect(
    page.getByText('Connected', { exact: true }).filter({ visible: true }),
  ).toHaveCount(1);
  await expect(
    page.getByText('Disconnected', { exact: true }).filter({ visible: true }),
  ).toHaveCount(0);
  await expect(page.getByText('Something went wrong')).toHaveCount(0);
  await expect.poll(() => burstRequestCount(page)).toBe(1);

  // Reload reads the synthetic saved completion, not backend persistence:
  // the settled view renders from history and nothing is sent again.
  const savedReply = 'Burst reply complete.';
  await page.route(
    (url) => url.pathname.endsWith(`/conversations/${conversation.id}`),
    (route) =>
      route.fulfill({
        json: historyFixtureDetail(
          {
            ...conversation,
            messages: [
              ...conversation.messages,
              { id: 'message-101', role: 'user', content: 'Burst this reply' },
              { id: 'message-102', role: 'assistant', content: savedReply },
            ],
          },
          new URL(route.request().url()),
        ),
      }),
  );
  await page.reload();
  await expect(
    messages.locator(`[data-message-id="${historyFixtureId(102)}"]`),
  ).toContainText(savedReply);
  await expect(
    page.getByText('Disconnected', { exact: true }).filter({ visible: true }),
  ).toHaveCount(0);
  await expect.poll(() => burstRequestCount(page)).toBe(1);
});

test('a burst of 145 thinking deltas then 145 text deltas keeps the thought and reply (#752)', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBurstStream(page, 'thinking-then-text', 145);
  await page.goto(`/?id=${conversation.id}`);
  const messages = page.getByRole('main', { name: 'Conversation messages' });
  await expect(messages.locator('article')).toHaveCount(100);
  await page.locator('textarea').fill('Burst this reply');
  await page.locator('textarea').press('Enter');

  await expect(messages).toContainText('Burst reply complete.', {
    timeout: 30_000,
  });
  await expect(messages).toContainText('Delta 145. ');

  // onFinish archives from the last committed React state, so the final
  // thinking deltas must still be visible after the paced stream settles.
  const thought = messages.getByRole('button', { name: /Thinking for \d+s/ });
  await expect(thought).toBeVisible();
  await thought.click();
  await expect(messages).toContainText('Thought 1.');
  await expect(messages).toContainText('Thought 145.');

  await expect(
    page.getByText('Connected', { exact: true }).filter({ visible: true }),
  ).toHaveCount(1);
  await expect(
    page.getByText('Disconnected', { exact: true }).filter({ visible: true }),
  ).toHaveCount(0);
  await expect(page.getByText('Something went wrong')).toHaveCount(0);
  await expect.poll(() => burstRequestCount(page)).toBe(1);
});

test('a hidden-tab chat burst finishes without short timers (#752)', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await installBurstStream(page, 'thinking-then-text', 145, true);
  await page.goto(`/?id=${conversation.id}`);
  const messages = page.getByRole('main', { name: 'Conversation messages' });
  await expect(messages.locator('article')).toHaveCount(100);
  const composer = page.getByRole('textbox', { name: 'Message Daemon' });
  await composer.fill('Burst this reply');
  await composer.press('Enter');

  // The simulated timer delay exceeds the test deadline. The final text and
  // return to Send prove the stream drained and settled without a timer batch.
  await expect(messages).toContainText('Delta 145. Burst reply complete.', {
    timeout: 15_000,
  });
  await expect(
    page.getByRole('button', { name: 'Send message' }),
  ).toBeVisible();
  await expect(
    page.getByText('Connected', { exact: true }).filter({ visible: true }),
  ).toHaveCount(1);
  expect(await page.evaluate(() => document.visibilityState)).toBe('hidden');
  await expect.poll(() => burstRequestCount(page)).toBe(1);
});

test('HTML artifacts distinguish a streaming reply from saved preview and source-only states (#759)', async ({
  page,
}) => {
  const source =
    '  <h2>Quarterly totals</h2>\n<table><tr><td>42</td></tr></table>\n';
  const open = '```html:interactive\n';
  const close = '```\n\nEnd of preview.';
  await page.addInitScript(
    ({ open, source, close }) => {
      const fetchOriginal = window.fetch.bind(window);
      window.fetch = (input, init) => {
        const url =
          typeof input === 'string'
            ? input
            : input instanceof URL
              ? input.href
              : input.url;
        if (!url.endsWith('/api/chat')) return fetchOriginal(input, init);
        const encoder = new TextEncoder();
        return Promise.resolve(
          new Response(
            new ReadableStream({
              start(controller) {
                const send = (frame: object) =>
                  controller.enqueue(
                    encoder.encode(`data: ${JSON.stringify(frame)}\n\n`),
                  );
                send({ type: 'start', messageId: 'artifact-reply' });
                send({ type: 'text-start', id: 'text' });
                send({ type: 'text-delta', id: 'text', delta: open + source });
                (
                  window as unknown as { closeArtifactFence: () => void }
                ).closeArtifactFence = () =>
                  send({ type: 'text-delta', id: 'text', delta: close });
                (
                  window as unknown as { finishArtifactReply: () => void }
                ).finishArtifactReply = () => {
                  send({ type: 'text-end', id: 'text' });
                  send({ type: 'finish' });
                  controller.close();
                };
              },
            }),
            {
              headers: {
                'content-type': 'text/event-stream',
                'x-vercel-ai-ui-message-stream': 'v1',
              },
            },
          ),
        );
      };
    },
    { open, source, close },
  );
  await page.goto(`/?id=${conversation.id}`);
  const main = page.getByRole('main', { name: 'Conversation messages' });
  await expect(main.locator('article')).toHaveCount(100);
  const composer = page.getByRole('textbox', { name: 'Message Daemon' });
  await composer.fill('Make a static preview');
  await composer.press('Enter');

  const frame = main.locator('iframe[title="Static HTML preview"]');
  const totals = main
    .frameLocator('iframe[title="Static HTML preview"]')
    .getByRole('heading', { name: 'Quarterly totals' });
  await expect(main.getByText('Receiving HTML…')).toBeVisible();
  await expect(frame).toHaveCount(0);
  await page.evaluate(() =>
    (
      window as unknown as { closeArtifactFence: () => void }
    ).closeArtifactFence(),
  );
  await expect(main.getByText('End of preview.')).toBeVisible();
  await expect(main.getByText('Receiving HTML…')).toBeVisible();
  await expect(frame).toHaveCount(0);
  await page.evaluate(() =>
    (
      window as unknown as { finishArtifactReply: () => void }
    ).finishArtifactReply(),
  );
  await expect(totals).toBeVisible();
  await expect(frame).toHaveCSS('opacity', '1');

  const unsupported =
    '<div id="dashboard"></div><script>document.querySelector("#dashboard").textContent = "Generated using JS.";</script>\n';
  const unfinished = '  <div>Preserve this source verbatim.</div>\n\n';
  const zeroViewport =
    '<svg viewBox="0 0 0 0"><circle cx="20" cy="20" r="10" /></svg>\n';
  const validSvg =
    '<svg viewBox="0 0 40 40" width="40" height="40"><circle cx="20" cy="20" r="10" fill="orange" /></svg>\n';
  const savedReply = (id: number) =>
    main.locator(`[data-message-id="${historyFixtureId(id)}"]`);
  await page.route(
    (url) => url.pathname.endsWith(`/conversations/${conversation.id}`),
    (route) =>
      route.fulfill({
        json: historyFixtureDetail(
          {
            ...conversation,
            messages: [
              ...conversation.messages,
              {
                id: 'message-101',
                role: 'user',
                content: 'Make a static preview',
              },
              {
                id: 'message-102',
                role: 'assistant',
                content: open + source + close,
              },
              {
                id: 'message-103',
                role: 'assistant',
                content: open + unsupported + close,
              },
              {
                id: 'message-104',
                role: 'assistant',
                content: open + unfinished,
              },
              {
                id: 'message-105',
                role: 'assistant',
                content: open + zeroViewport + close,
              },
              {
                id: 'message-106',
                role: 'assistant',
                content: open + validSvg + close,
              },
            ],
          },
          new URL(route.request().url()),
        ),
      }),
  );
  await page.reload();
  await expect(frame).toHaveCount(2);
  await expect(
    savedReply(102)
      .frameLocator('iframe[title="Static HTML preview"]')
      .getByRole('heading', { name: 'Quarterly totals' }),
  ).toBeVisible();
  const svgFrame = savedReply(106).locator(
    'iframe[title="Static HTML preview"]',
  );
  await expect(svgFrame).toHaveCSS('opacity', '1');
  await expect(svgFrame.contentFrame().locator('circle')).toBeVisible();
  for (const [id, body, notice] of [
    [103, unsupported, /uses unsupported features/],
    [104, unfinished, /code block was not closed/],
    [105, zeroViewport, /no visible content/],
  ] as const) {
    const reply = savedReply(id);
    await expect(reply.locator('iframe')).toHaveCount(0);
    await expect(reply.getByText(notice)).toBeVisible();
    await reply.getByText('View source').click();
    expect(await reply.locator('pre code').textContent()).toBe(body);
    await expect(
      reply.getByRole('button', { name: 'Copy source' }),
    ).toBeVisible();
  }
});
