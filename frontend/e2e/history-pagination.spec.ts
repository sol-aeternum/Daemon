import { expect, test, type Page } from '@playwright/test';

// Fictional cursor API and SDK transport only; no backend/provider connection.
const id = (n: number) =>
  `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const at = '2026-10-28T00:00:00.123456Z';
const conversationId = id(201);
const message = (n: number) => ({
  id: id(n),
  user_id: id(999),
  conversation_id: conversationId,
  role: n % 2 ? 'user' : 'assistant',
  created_at: at,
  content: `Synthetic saved turn ${n}.`,
  metadata: {},
});
const conversation = (n: number) => ({
  id: id(n),
  user_id: id(999),
  title: `Synthetic conversation ${n}`,
  pinned: false,
  title_locked: false,
  status: 'active',
  metadata: {},
  created_at: at,
  updated_at: at,
  last_activity_at: at,
  message_count: 200,
});
const pageMeta = (
  first: number | undefined,
  last: number | undefined,
  more: boolean,
  prefix: string,
) => ({
  has_more: more,
  next_cursor: more ? `${prefix}_${first}` : null,
  start_cursor: first === undefined ? null : `${prefix}_${first}`,
  end_cursor: last === undefined ? null : `${prefix}_${last}`,
});

async function fixture(page: Page, media = false) {
  const payloads: Array<{ messages: Array<{ id: string; role: string }> }> = [];
  const unexpected: string[] = [];
  const runtimeErrors: string[] = [];
  page.on('pageerror', (error) => runtimeErrors.push(error.message));
  const rows = Array.from({ length: 200 }, (_, index) => message(index + 1));
  if (media)
    rows[1].content += '\n\n![Fictional delayed media](/history-media.svg)';
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:3173') {
      unexpected.push(url.origin + url.pathname);
      await route.abort();
      return;
    }
    const path = url.pathname.replace(/^\/api(?=\/)/, '');
    if (url.pathname === '/api/chat') {
      payloads.push(route.request().postDataJSON());
      if (payloads.length === 1) {
        // A failed HTTP transport exercises the existing Retry/regenerate path.
        // Stream error-frame status reporting is tracked separately from #230.
        await route.fulfill({
          status: 503,
          json: { error: 'Fictional transport unavailable' },
        });
        return;
      }
      const chunks = [
        { type: 'start', messageId: 'synthetic-retry-reply' },
        { type: 'finish' },
      ];
      await route.fulfill({
        contentType: 'text/event-stream',
        headers: { 'x-vercel-ai-ui-message-stream': 'v1' },
        body:
          chunks.map((chunk) => `data: ${JSON.stringify(chunk)}\n\n`).join('') +
          'data: [DONE]\n\n',
      });
    } else if (path === '/v1/auth/cache/clear') {
      await route.fulfill({
        status: 204,
        headers: { 'Cache-Control': 'no-store', 'Clear-Site-Data': '"cache"' },
      });
    } else if (path === '/v1/auth/config') {
      await route.fulfill({
        json: {
          mode: 'self_hosted',
          email: { enabled: false },
          google: { enabled: false },
        },
      });
    } else if (path === '/v1/auth/refresh') {
      await route.fulfill({
        json: { access_token: 'fictional-history-token', expires_in: 3600 },
      });
    } else if (path === '/conversations') {
      const before = Number(
        url.searchParams.get('cursor')?.split('_')[1] ?? 202,
      );
      const selected = Array.from({ length: 201 }, (_, index) =>
        conversation(201 - index),
      )
        .filter((row) => Number(row.id.slice(-12)) < before)
        .slice(0, 100);
      const first = Number(selected[0]?.id.slice(-12));
      const last = Number(selected.at(-1)?.id.slice(-12));
      await route.fulfill({
        json: {
          conversations: selected,
          total: 201,
          page: {
            ...pageMeta(first, last, last > 1, 'list'),
            next_cursor: last > 1 ? `list_${last}` : null,
          },
        },
      });
    } else if (path === `/conversations/${conversationId}`) {
      const before = Number(
        url.searchParams.get('before')?.split('_')[1] ?? 201,
      );
      const through = Number(
        url.searchParams.get('through')?.split('_')[1] ?? 1,
      );
      const candidates = rows.filter((row) => {
        const n = Number(row.id.slice(-12));
        return n < before && n >= through;
      });
      const selected = candidates.slice(-100);
      await route.fulfill({
        json: {
          ...conversation(201),
          messages: selected,
          active_task: null,
          latest_task: null,
          message_page: pageMeta(
            Number(selected[0]?.id.slice(-12)),
            Number(selected.at(-1)?.id.slice(-12)),
            candidates.length > 100,
            'before',
          ),
        },
      });
    } else if (path.startsWith('/tasks/')) {
      await route.fulfill({
        status: 404,
        json: { detail: 'Fictional task not found' },
      });
    } else if (path === '/users/me/settings') {
      await route.fulfill({ json: {} });
    } else if (path === '/v1/catalog') {
      await route.fulfill({
        json: {
          auto: {
            id: 'auto',
            name: 'Auto',
            tagline: 'Automatic routing',
            icon: 'zap',
          },
          featured: [],
        },
      });
    } else if (path === '/entitlements') {
      await route.fulfill({
        json: { plan: 'free', capabilities: ['chat'], limits: {} },
      });
    } else if (path === '/home-suggestions') {
      await route.fulfill({
        json: { enabled: false, status: 'disabled', suggestions: [] },
      });
    } else {
      await route.continue();
    }
  });
  return { payloads, unexpected, runtimeErrors };
}

for (const width of [390, 1440]) {
  test(`older messages preserve focus/anchors/media and bounded send/retry at ${width}px`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 900 });
    const evidence = await fixture(page, true);
    let releaseMedia!: () => void;
    const media = new Promise<void>((resolve) => {
      releaseMedia = resolve;
    });
    await page.route('**/history-media.svg', async (route) => {
      await media;
      await route.fulfill({
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="navy"/></svg>',
      });
    });
    await page.goto(`/?id=${conversationId}`);
    const main = page.getByRole('main', { name: 'Conversation messages' });
    await expect(main.locator('article')).toHaveCount(100);
    const older = main.getByRole('button', {
      name: /^(Load older messages|Loading earlier messages…|No earlier messages|Retry history)$/,
    });
    await older.scrollIntoViewIfNeeded();
    await older.focus();
    const anchor = main.locator(`[data-message-id="${id(101)}"]`);
    const offset = () =>
      anchor.evaluate(
        (node) =>
          node.getBoundingClientRect().top -
          node.closest('main')!.getBoundingClientRect().top,
      );
    const before = await offset();
    await page.keyboard.press('Enter');
    await expect(main.locator('article')).toHaveCount(200);
    await expect(older).toHaveText('No earlier messages');
    await expect(older).toBeFocused();
    await expect
      .poll(async () => Math.abs((await offset()) - before))
      .toBeLessThan(2);
    releaseMedia();
    await expect(main.getByAltText('Fictional delayed media')).toHaveJSProperty(
      'complete',
      true,
    );
    await expect
      .poll(async () => Math.abs((await offset()) - before))
      .toBeLessThan(2);
    // Every loaded row remains searchable in the DOM, not in a virtual window.
    await expect(main).toContainText('Synthetic saved turn 1.');
    await page.screenshot({
      path: info.outputPath(`loaded-history-${width}.png`),
    });

    const composer = page.getByRole('textbox', { name: 'Message Daemon' });
    await composer.fill('Fictional payload isolation check');
    await page
      .getByRole('button', { name: 'Send message', exact: true })
      .click();
    await expect.poll(() => evidence.payloads.length).toBe(1);
    const retry = page
      .getByRole('button', { name: 'Retry', exact: true })
      .filter({ visible: true });
    await expect(retry).toBeVisible();
    await retry.click();
    await expect.poll(() => evidence.payloads.length).toBe(2);
    const prefix = new Set(Array.from({ length: 100 }, (_, n) => id(n + 1)));
    for (const payload of evidence.payloads) {
      expect(payload.messages.some((row) => prefix.has(row.id))).toBe(false);
      expect(
        payload.messages
          .filter((row) => rowsInTail.has(row.id))
          .map((row) => row.id),
      ).toEqual(Array.from({ length: 100 }, (_, n) => id(n + 101)));
      expect(payload.messages.length).toBeLessThanOrEqual(102);
    }
    expect(evidence.unexpected).toEqual([]);
    expect(evidence.runtimeErrors).toEqual([]);
  });
}

const rowsInTail = new Set(Array.from({ length: 100 }, (_, n) => id(n + 101)));

test('conversation pages reach beyond 200 while the exhausted control retains keyboard focus', async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const evidence = await fixture(page);
  await page.goto(`/?id=${conversationId}`);
  const more = page.getByRole('button', {
    name: 'Load more conversations',
    exact: true,
  });
  await expect(more).toBeVisible();
  await more.focus();
  await page.keyboard.press('Enter');
  await expect(
    page.getByText('Synthetic conversation 2', { exact: true }),
  ).toBeVisible();
  await expect(more).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(
    page.getByText('Synthetic conversation 1', { exact: true }),
  ).toBeVisible();
  const exhausted = page.getByRole('button', {
    name: 'No more conversations',
    exact: true,
  });
  await expect(exhausted).toBeFocused();
  expect(evidence.unexpected).toEqual([]);
  expect(evidence.runtimeErrors).toEqual([]);
});
