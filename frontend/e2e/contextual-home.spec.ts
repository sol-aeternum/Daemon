import { expect, test, type Page } from '@playwright/test';

// Deterministic, fictional transport fixtures. No provider or private data calls.
const prompt =
  'Write a release plan with concrete acceptance tests, a rollback checklist and an explicit list of unresolved decisions. Use the release discussion as context; do not assume a deployment has been authorised.';
const candidate = {
  id: 'fixture-candidate',
  summary: 'Plan the release',
  prompt,
  source: { conversation_id: 'fixture-source', title: 'Release discussion' },
  expires_at: '2099-01-01T00:00:00Z',
};

async function fixtures(page: Page) {
  await page.addInitScript(() =>
    Reflect.deleteProperty(Navigator.prototype, 'serviceWorker'),
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
        access_token: 'contextual-home-fictional-token',
        expires_in: 3600,
      },
    }),
  );
  await page.route('**/api/entitlements', (route) =>
    route.fulfill({
      json: { plan: 'free', capabilities: ['chat'], limits: {} },
    }),
  );
  await page.route('**/conversations*', (route) =>
    route.fulfill({ json: { conversations: [] } }),
  );
  await page.route('**/conversations/fixture-destination', (route) =>
    route.fulfill({
      json: {
        id: 'fixture-destination',
        title: 'Release plan',
        pinned: false,
        title_locked: false,
        status: 'active',
        metadata: {},
        created_at: '2026-10-05T00:00:00Z',
        updated_at: '2026-10-05T00:00:00Z',
        messages: [
          {
            id: 'persisted-question',
            role: 'user',
            content: prompt,
            metadata: {
              home_suggestion: {
                version: 1,
                suggestion_id: candidate.id,
                sources: [
                  {
                    conversation_id: 'fixture-source',
                    title: 'Release discussion',
                    messages: [
                      {
                        id: 'source-turn',
                        role: 'user',
                        content:
                          'Create a release plan, but do not deploy yet.',
                      },
                    ],
                  },
                ],
              },
            },
          },
          {
            id: 'fixture-answer',
            role: 'assistant',
            content: 'Here is the fictional release plan.',
          },
        ],
      },
    }),
  );
  await page.route('**/users/me/settings', (route) =>
    route.fulfill({
      json: { preferences: { home_suggestions_enabled: true } },
    }),
  );
  await page.route('**/home-suggestions', (route) =>
    route.fulfill({
      json: { enabled: true, status: 'ready', suggestions: [candidate] },
      headers: { 'Cache-Control': 'no-store' },
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
}

test.beforeEach(async ({ page }) => fixtures(page));

test('quiet empty home and persistent Settings control without generation', async ({
  page,
}, testInfo) => {
  let enabled = true;
  const patches: unknown[] = [];
  let refreshes = 0;
  await page.route('**/home-suggestions', (route) =>
    route.fulfill({
      json: {
        enabled,
        status: enabled ? 'empty' : 'disabled',
        suggestions: [],
      },
    }),
  );
  await page.route('**/home-suggestions/refresh', (route) => {
    refreshes++;
    return route.fulfill({ json: { status: 'unchanged' } });
  });
  await page.route('**/users/me/settings', (route) => {
    if (route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON();
      patches.push(body);
      enabled = body.preferences.home_suggestions_enabled;
      return route.fulfill({
        json: {
          status: 'updated',
          settings: { preferences: { home_suggestions_enabled: enabled } },
        },
      });
    }
    return route.fulfill({
      json: { preferences: { home_suggestions_enabled: enabled } },
    });
  });
  await page.setViewportSize({ width: 689, height: 820 });
  await page.goto('/');
  await expect(page.locator('textarea')).toBeVisible();
  await expect(page.getByText(/No contextual suggestions/)).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Deliberate' })).toHaveCount(0);
  await expect(page.getByText('Type a message to get started')).toHaveCount(0);
  await expect(
    page.getByText(/Voice and image generation are unavailable/),
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Refresh suggestions' }),
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Turn off suggestions' }),
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Local pipeline coming soon' }),
  ).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath('quiet-empty-home.png'),
    fullPage: true,
  });
  await page.goto('/settings/profile');
  await expect(page.getByText('Personal suggestions are on.')).toBeVisible();
  await page.getByRole('button', { name: 'Turn off suggestions' }).click();
  await expect(page.getByText('Personal suggestions are off.')).toBeVisible();
  expect(patches).toEqual([
    { preferences: { home_suggestions_enabled: false } },
  ]);
  await page.getByRole('button', { name: 'Turn on suggestions' }).click();
  await expect(page.getByText('Personal suggestions are on.')).toBeVisible();
  expect(refreshes).toBe(0);
});

for (const width of [375, 768, 1440]) {
  test(`centred exact preview and immediate new chat at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1100 });
    const sent: Record<string, unknown>[] = [];
    await page.route('**/api/chat', async (route) => {
      sent.push(route.request().postDataJSON());
      const frames = [
        { type: 'start', messageId: 'fixture-answer' },
        {
          type: 'data-event',
          data: {
            type: 'conversation',
            conversation_id: 'fixture-destination',
          },
        },
        { type: 'text-start', id: 'answer-text' },
        {
          type: 'text-delta',
          id: 'answer-text',
          delta: 'Here is the fictional release plan.',
        },
        { type: 'text-end', id: 'answer-text' },
        { type: 'finish' },
      ];
      await route.fulfill({
        contentType: 'text/event-stream',
        headers: { 'x-vercel-ai-ui-message-stream': 'v1' },
        body:
          frames.map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join('') +
          'data: [DONE]\n\n',
      });
    });
    await page.goto('/');
    const row = page.getByRole('button', { name: /^Plan the release/ });
    await expect(row).toBeVisible();
    await expect(page.locator('textarea')).toHaveCount(1);
    await row.hover();
    const tooltip = page.getByRole('tooltip');
    await expect(tooltip).toContainText(prompt);
    expect(sent).toHaveLength(0);
    const rowBox = await page
      .getByTestId(`suggestion-row-${candidate.id}`)
      .boundingBox();
    const tipBox = await tooltip.boundingBox();
    expect(rowBox).not.toBeNull();
    expect(tipBox).not.toBeNull();
    if (rowBox && tipBox) {
      const intended = rowBox.x + rowBox.width / 2;
      const clamped = Math.max(
        tipBox.width / 2 + 12,
        Math.min(width - tipBox.width / 2 - 12, intended),
      );
      expect(Math.abs(tipBox.x + tipBox.width / 2 - clamped)).toBeLessThan(3);
      expect(tipBox.x).toBeGreaterThanOrEqual(0);
      expect(tipBox.x + tipBox.width).toBeLessThanOrEqual(width);
    }
    await page.screenshot({
      path: testInfo.outputPath(`contextual-home-${width}.png`),
      fullPage: true,
    });
    await row.focus();
    await page.keyboard.press('Escape');
    await expect(tooltip).not.toBeVisible();
    await row.click();
    await expect(page).toHaveURL(/id=fixture-destination/);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ id: null, suggestion_id: candidate.id });
    const messages = sent[0].messages as Array<{
      parts: Array<{ type: string; text?: string }>;
    }>;
    expect(messages.at(-1)?.parts).toEqual([{ type: 'text', text: prompt }]);
    expect(sent[0].attachments).toBeUndefined();
    await page.reload();
    await expect(
      page.getByText('Context used · Release discussion'),
    ).toBeVisible();
    await page.getByText('Context used · Release discussion').click();
    await expect(
      page.getByText('Create a release plan, but do not deploy yet.'),
    ).toBeVisible();
  });
}
