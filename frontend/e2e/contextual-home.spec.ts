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
